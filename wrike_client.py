"""Minimal Wrike v4 client used by the sync orchestrator.

Author is the identity that owns the token, so a client is created per author
(see make_wrike_client). Retries 429/5xx with capped exponential backoff.
"""
from __future__ import annotations

import base64
import json
import os
import time

import requests


class WrikeError(Exception):
    pass


class WrikeClient:
    def __init__(self, token: str, host: str = "www.wrike.com", timeout: float = 30.0,
                 max_attempts: int = 8):
        self._base = f"https://{host}/api/v4"
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"bearer {token}"})
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._folder_cache: dict[str, str] = {}

    @staticmethod
    def _to_body(payload: dict) -> dict:
        body = {}
        for key in ("title", "description", "status", "importance", "dates",
                    "customItemTypeId"):
            if payload.get(key) is not None:
                body[key] = payload[key]
        custom = payload.get("customFields")
        if custom:
            body["customFields"] = [
                {"id": fid, "value": "" if val is None else str(val)}
                for fid, val in custom.items()
            ]
        return body

    def _request(self, method: str, path: str, **kwargs):
        delay = 1.0
        for attempt in range(1, self._max_attempts + 1):
            resp = self._session.request(
                method, f"{self._base}{path}", timeout=self._timeout, **kwargs
            )
            if resp.ok:
                return resp.json()
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self._max_attempts:
                    raise WrikeError(f"{method} {path} -> {resp.status_code}: {resp.text}")
                # On 429, honor Wrike's Retry-After header (it states exactly how long it is
                # throttling) instead of guessing; otherwise capped exponential backoff.
                # Waiting it out beats raising -> Service Bus redelivery -> dead-letter under
                # a batch that bursts past Wrike's rate limit.
                retry_after = resp.headers.get("Retry-After")
                wait = (float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else delay)
                time.sleep(min(wait, 60))
                delay *= 2
                continue
            raise WrikeError(f"{method} {path} -> {resp.status_code}: {resp.text}")
        raise WrikeError("unreachable")

    def resolve_folder_id(self, folder_id: str) -> str:
        """Wrike's API uses v4 ids; a numeric permalink id (from a folder's URL) is
        resolved to its v4 id via /ids. Cached per client."""
        if not str(folder_id).isdigit():
            return folder_id
        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id]
        data = self._request(
            "GET", "/ids",
            params={"ids": json.dumps([int(folder_id)]), "type": "ApiV2Folder"},
        )
        ids = data.get("data", [])
        if not ids:
            raise WrikeError(f"could not resolve numeric folder id {folder_id}")
        self._folder_cache[folder_id] = ids[0]["id"]
        return self._folder_cache[folder_id]

    def resolve_folder_ids(self, folder_ids) -> None:
        """Warm the numeric->v4 cache for MANY folder ids in ONE /ids call.

        Wrike's /ids resolves a whole list of numeric (ApiV2) ids to v4 at once, so calling
        this before a batch lets every later resolve_folder_id serve from cache - avoiding a
        burst of one-call-per-folder /ids requests that trips Wrike's rate limit. Non-numeric
        ids (already v4) and already-cached ids are skipped, so it is cheap to call repeatedly."""
        pending = sorted({str(f) for f in folder_ids
                          if str(f).isdigit() and str(f) not in self._folder_cache})
        if not pending:
            return
        data = self._request(
            "GET", "/ids",
            params={"ids": json.dumps([int(p) for p in pending]), "type": "ApiV2Folder"},
        )
        for entry in data.get("data", []):
            self._folder_cache[str(entry["apiV2Id"])] = entry["id"]

    def find_tasks_by_custom_field(self, field_id: str, value: str,
                                   folder_id: str) -> list[dict]:
        """ALL tasks in `folder_id` (and its subfolders) whose custom field `field_id`
        equals `value` exactly — never account-wide. Returns full task dicts
        (customFields included) so the caller can inspect the other fields and decide
        what to do with each match (see project_docs/wrike_api.md)."""
        fid = self.resolve_folder_id(folder_id)
        data = self._request(
            "GET", f"/folders/{fid}/tasks",
            params={
                "customFields": json.dumps([{"id": field_id, "value": value}]),
                "descendants": "true",
                "fields": json.dumps(["customFields", "parentIds"]),
            },
        )
        return data.get("data", [])

    def list_folder_tasks(self, folder_id: str) -> list[dict]:
        """Every task in `folder_id` and its subfolders, customFields included —
        the reconciliation walk. Paginated (pageSize max is 1000)."""
        fid = self.resolve_folder_id(folder_id)
        tasks: list[dict] = []
        page_token = None
        while True:
            params = {
                "descendants": "true",
                "pageSize": 1000,
                "fields": json.dumps(["customFields", "parentIds"]),
            }
            if page_token:
                params["nextPageToken"] = page_token
            data = self._request("GET", f"/folders/{fid}/tasks", params=params)
            tasks.extend(data.get("data", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return tasks

    @staticmethod
    def to_numeric_id(v4_id: str) -> str:
        """Convert a Wrike v4 entity id to its legacy NUMERIC (permalink / ApiV2) id - the
        inverse of resolve_folder_id's numeric->v4 conversion.

        Wrike exposes no API for v4 -> numeric (the /ids endpoint only goes numeric -> v4),
        but a v4 id is just URL-safe base64 of [1 type byte][big-endian numeric id], so we
        decode it directly. Verified by round-tripping every folder in the live space back
        through /ids (89/89 matched). Stored numeric ids are turned back into v4 on use by
        resolve_folder_id, which raises via /ids if a value is ever unresolvable - so a bad
        decode fails loudly, it can never silently misroute."""
        padded = v4_id + "=" * (-len(v4_id) % 4)
        return str(int.from_bytes(base64.urlsafe_b64decode(padded)[1:], "big"))

    def list_top_level_folders(self, space_id: str) -> list[dict]:
        """The TOP-LEVEL folders (id + title) of a Wrike space.

        A Wrike space id IS its root folder id, so a numeric WRIKE_SPACE_ID (a permalink
        id, e.g. 4435490633) is resolved to its v4 id first - the API rejects numeric ids
        with 400 'Invalid Space ID'. GET /folders/{root}/folders returns the root's whole
        flattened subtree, INCLUDING the root entry itself (id == root) and any nested
        descendants (verified live; see project_docs/wrike_api.md). Top-level folders are
        exactly the root entry's childIds; filter the subtree to those (the "not anyone's
        child" heuristic fails here - the root is in the list and claims them as children)."""
        root_id = self.resolve_folder_id(space_id)
        data = self._request("GET", f"/folders/{root_id}/folders").get("data", [])
        by_id = {f["id"]: f for f in data}
        root = by_id.get(root_id)
        if root is None:
            raise WrikeError(
                f"space {space_id} (root folder {root_id}): the root folder was not in the "
                f"/folders/{root_id}/folders response")
        child_ids = set(root.get("childIds") or [])
        return [f for f in data if f["id"] in child_ids]

    def create_folder(self, parent_folder_id: str, title: str) -> dict:
        """Create a folder under parent_folder_id and return it (carries its v4 id). Pass
        a space id as the parent to create at the space's top level - the space id is its
        root folder id; a numeric (permalink) parent is resolved to its v4 id first. title
        goes as a QUERY param, not a JSON body (Wrike folder-create quirk; see project_docs)."""
        parent = self.resolve_folder_id(parent_folder_id)
        data = self._request("POST", f"/folders/{parent}/folders", params={"title": title})
        return data["data"][0]

    def create_task(self, folder_id: str, payload: dict) -> dict:
        folder = self.resolve_folder_id(folder_id)
        data = self._request("POST", f"/folders/{folder}/tasks", json=self._to_body(payload))
        return data["data"][0]

    def update_task(self, task_id: str, payload: dict) -> dict:
        data = self._request("PUT", f"/tasks/{task_id}", json=self._to_body(payload))
        return data["data"][0]

    def get_task(self, task_id: str) -> dict:
        data = self._request("GET", f"/tasks/{task_id}")
        return data["data"][0]

    def add_comment(self, task_id: str, text: str) -> dict:
        data = self._request("POST", f"/tasks/{task_id}/comments", data={"text": text})
        return data["data"][0]


_clients: dict[str, WrikeClient] = {}


def make_wrike_client(token_ref: str) -> WrikeClient:
    """Client for an author identity (token from env). Cached per token_ref so rows
    sharing an author reuse one keep-alive session instead of a fresh TLS handshake."""
    if token_ref not in _clients:
        token = os.environ[token_ref]
        host = os.environ.get("WRIKE_HOST", "www.wrike.com")
        _clients[token_ref] = WrikeClient(token, host=host)
    return _clients[token_ref]
