"""Minimal Wrike v4 client used by the sync orchestrator.

Author is the identity that owns the token, so a client is created per author
(see make_wrike_client). Retries 429/5xx with capped exponential backoff.
"""
from __future__ import annotations

import json
import os
import time

import requests


class WrikeError(Exception):
    pass


class WrikeClient:
    def __init__(self, token: str, host: str = "www.wrike.com", timeout: float = 30.0,
                 max_attempts: int = 5):
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
                time.sleep(min(delay, 60))
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
