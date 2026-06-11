"""Minimal Centric 8 PLM REST client used to refresh the local plm_item mirror.

Session auth: POST /session returns a token that is sent as the `Cookie` header on every
later request. Reference ids (category_1/2, collection) are resolved to display names via
each entity's own endpoint and cached per client. Built on `requests`, mirroring
`wrike_client.py`, with the two Centric sandbox quirks handled (see
`project_docs/centric_8_api.md`): the server is intermittently very slow (retry on
timeout / 5xx with capped backoff) and sometimes returns HTTP 500 with a full valid JSON
body (trust the body over the status code).
"""
from __future__ import annotations

import os
import time

import requests

PAGE_SIZE = 200  # observed max page size for GET /styles


class CentricError(Exception):
    pass


class CentricClient:
    def __init__(self, base_url: str, username: str, password: str, *,
                 version: str = "v2", timeout: float = 90.0, max_attempts: int = 5,
                 session: requests.Session | None = None):
        self._api = f"{base_url.rstrip('/')}/csi-requesthandler/api/{version}"
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._ref_cache: dict[str, str] = {}
        self._authenticate(username, password)

    def _authenticate(self, username: str, password: str) -> None:
        resp = self._session.post(
            f"{self._api}/session",
            json={"username": username, "password": password},
            timeout=self._timeout,
        )
        if resp.status_code != 200:
            raise CentricError(f"POST /session -> {resp.status_code}: {resp.text}")
        self._session.headers["Cookie"] = resp.json()["token"]

    def _get(self, path: str, **params):
        """GET {api}/{path}. Retries timeouts / 5xx with capped backoff; trusts a valid
        JSON body even on HTTP 500 (sandbox quirk - some records 500 with full data)."""
        delay = 1.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = self._session.get(
                    f"{self._api}/{path}", params=params, timeout=self._timeout)
            except requests.Timeout:
                if attempt == self._max_attempts:
                    raise
                time.sleep(min(delay, 60))
                delay *= 2
                continue

            # Trust the body over the status: a 500 carrying valid JSON is still usable.
            if resp.ok or resp.status_code == 500:
                try:
                    return resp.json()
                except ValueError:
                    pass  # 500 with no/invalid body -> fall through to the retry path

            if resp.status_code >= 500:  # 503, or a 500 without a usable body
                if attempt == self._max_attempts:
                    raise CentricError(f"GET /{path} -> {resp.status_code}: {resp.text[:300]}")
                time.sleep(min(delay, 60))
                delay *= 2
                continue

            raise CentricError(f"GET /{path} -> {resp.status_code}: {resp.text[:300]}")
        raise CentricError("unreachable")

    def list_styles(self, *, modified_after: str | None = None) -> list[dict]:
        """Active styles, paginated by skip/limit until a short page. `modified_after`
        (a 'yyyy/mm/ddThh:mm:ss' string) limits the pull to the delta; omit it for a
        full pull.

        `active=true` is filtered server-side (honored by the sandbox) so we don't page
        through the entire historical catalogue of inactive styles; the caller also
        re-checks `active` as a safety net in case an instance ignores the filter."""
        styles: list[dict] = []
        skip = 0
        while True:
            params = {"active": "true", "skip": skip, "limit": PAGE_SIZE}
            if modified_after:
                params["modified_after"] = modified_after
            page = self._get("styles", **params)
            styles += page
            if len(page) < PAGE_SIZE:
                return styles
            skip += PAGE_SIZE

    def resolve_ref(self, endpoint: str, ref_id) -> str:
        """Reference id -> display name (node_name, else code) via the entity's own
        endpoint, cached. A blank id or a 'centric%3A...' placeholder resolves to ''."""
        if not ref_id or (isinstance(ref_id, str) and ref_id.startswith("centric%3A")):
            return ""
        if ref_id not in self._ref_cache:
            rec = self._get(f"{endpoint}/{ref_id}")
            self._ref_cache[ref_id] = rec.get("node_name") or rec.get("code") or ref_id
        return self._ref_cache[ref_id]


def make_centric_client() -> CentricClient:
    """An authenticated client from the CENTRIC_* env settings. Not cached: the session
    token is short-lived, so each run (e.g. each producer fire) authenticates afresh."""
    return CentricClient(
        os.environ["CENTRIC_BASE_URL"],
        os.environ["CENTRIC_USERNAME"],
        os.environ["CENTRIC_PASSWORD"],
        version=os.environ.get("CENTRIC_API_VERSION", "v2"),
    )
