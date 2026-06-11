import pytest
import requests

from centric_client import CentricClient, CentricError


class FakeResp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = "" if isinstance(body, (dict, list)) else str(body)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class FakeSession:
    """Drives auth (post) + GETs without real HTTP.

    `get_queue` is a list of FakeResp or Exception (raised), consumed in order.
    """

    def __init__(self, *, token="tok-123", get_queue=None):
        self.headers = {}
        self._token = token
        self._get_queue = list(get_queue or [])
        self.get_calls = []
        self.post_call = None

    def post(self, url, json=None, timeout=None):
        self.post_call = (url, json)
        return FakeResp(200, {"token": self._token})

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, dict(params or {})))
        nxt = self._get_queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _client(get_queue=None, **kw):
    session = FakeSession(get_queue=get_queue)
    client = CentricClient("https://centric.example.com", "u", "p",
                           session=session, **kw)
    return client, session


def test_authenticate_sets_cookie_header():
    client, session = _client()
    assert session.post_call[0].endswith("/csi-requesthandler/api/v2/session")
    assert session.post_call[1] == {"username": "u", "password": "p"}
    assert session.headers["Cookie"] == "tok-123"


def test_list_styles_paginates_until_short_page():
    full = [{"id": str(i)} for i in range(200)]  # a full page -> fetch again
    tail = [{"id": "x"}]                          # short page -> stop
    client, session = _client(get_queue=[FakeResp(200, full), FakeResp(200, tail)])
    out = client.list_styles()
    assert len(out) == 201
    assert session.get_calls[0][1] == {"skip": 0, "limit": 200}
    assert session.get_calls[1][1] == {"skip": 200, "limit": 200}


def test_list_styles_passes_filters():
    client, session = _client(get_queue=[FakeResp(200, [{"id": "1"}])])
    client.list_styles(mgf_ready_for_wrike="true")
    assert session.get_calls[0][1] == {"mgf_ready_for_wrike": "true", "skip": 0, "limit": 200}


def test_list_styles_single_short_page_one_call():
    client, session = _client(get_queue=[FakeResp(200, [{"id": "1"}, {"id": "2"}])])
    assert [s["id"] for s in client.list_styles()] == ["1", "2"]
    assert len(session.get_calls) == 1


def test_list_styles_passes_modified_after():
    client, session = _client(get_queue=[FakeResp(200, [{"id": "1"}])])
    client.list_styles(modified_after="2026/06/01T00:00:00")
    assert session.get_calls[0][1]["modified_after"] == "2026/06/01T00:00:00"


def test_resolve_ref_caches_and_returns_node_name():
    client, session = _client(get_queue=[FakeResp(200, {"node_name": "*CORE"})])
    assert client.resolve_ref("category2s", "C123") == "*CORE"
    assert client.resolve_ref("category2s", "C123") == "*CORE"   # cached -> no 2nd GET
    assert len(session.get_calls) == 1
    assert session.get_calls[0][0].endswith("/category2s/C123")


def test_resolve_ref_falls_back_to_code():
    client, _ = _client(get_queue=[FakeResp(200, {"code": "ABC"})])
    assert client.resolve_ref("collections", "X1") == "ABC"


def test_resolve_ref_short_circuits_blank_and_placeholder():
    client, session = _client()
    assert client.resolve_ref("category1s", "") == ""
    assert client.resolve_ref("category1s", None) == ""
    assert client.resolve_ref("category1s", "centric%3Anull") == ""
    assert session.get_calls == []   # no request made for placeholders


def test_get_trusts_json_body_on_http_500(monkeypatch):
    monkeypatch.setattr("centric_client.time.sleep", lambda *_: None)
    # A 500 carrying a valid JSON body is used, not retried/raised (sandbox quirk).
    client, session = _client(get_queue=[FakeResp(500, [{"id": "ok"}])])
    assert [s["id"] for s in client.list_styles()] == ["ok"]
    assert len(session.get_calls) == 1


def test_get_retries_on_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr("centric_client.time.sleep", lambda *_: None)
    client, session = _client(
        get_queue=[requests.Timeout("slow"), FakeResp(200, [{"id": "1"}])])
    assert [s["id"] for s in client.list_styles()] == ["1"]
    assert len(session.get_calls) == 2


def test_get_raises_after_max_timeout_attempts(monkeypatch):
    monkeypatch.setattr("centric_client.time.sleep", lambda *_: None)
    client, _ = _client(
        get_queue=[requests.Timeout("a"), requests.Timeout("b")], max_attempts=2)
    with pytest.raises(requests.Timeout):
        client.list_styles()


def test_get_raises_centric_error_on_4xx():
    client, _ = _client(get_queue=[FakeResp(404, "nope")])
    with pytest.raises(CentricError):
        client.list_styles()


def test_authenticate_raises_on_non_200():
    session = FakeSession()
    session.post = lambda url, json=None, timeout=None: FakeResp(401, "denied")
    with pytest.raises(CentricError):
        CentricClient("https://centric.example.com", "u", "p", session=session)
