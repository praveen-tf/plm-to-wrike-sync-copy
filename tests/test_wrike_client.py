import json

from mapping import ITEM_NUMBER_FIELD_ID
from wrike_client import WrikeClient


def test_find_tasks_by_custom_field_sends_folder_scoped_filter(monkeypatch):
    # The exact request shape documented in project_docs/wrike_api.md: folder-scoped,
    # customFields=[{"id","value"}] exact filter, descendants, customFields returned.
    client = WrikeClient("tok")
    seen = {}

    def fake_request(method, path, **kwargs):
        seen["method"], seen["path"], seen["params"] = method, path, kwargs.get("params")
        return {"data": [{"id": "T1"}, {"id": "T2"}]}

    monkeypatch.setattr(client, "_request", fake_request)
    out = client.find_tasks_by_custom_field(
        ITEM_NUMBER_FIELD_ID, "WP-71511-006-319", "IEFOLDER")
    assert [t["id"] for t in out] == ["T1", "T2"]  # ALL matches, caller branches
    assert (seen["method"], seen["path"]) == ("GET", "/folders/IEFOLDER/tasks")
    assert json.loads(seen["params"]["customFields"]) == [
        {"id": ITEM_NUMBER_FIELD_ID, "value": "WP-71511-006-319"}]
    assert seen["params"]["descendants"] == "true"
    assert "customFields" in json.loads(seen["params"]["fields"])


def test_list_folder_tasks_paginates_with_next_page_token(monkeypatch):
    client = WrikeClient("tok")
    pages = [{"data": [{"id": "T1"}], "nextPageToken": "tok2"},
             {"data": [{"id": "T2"}]}]
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append(kwargs.get("params"))
        return pages[len(calls) - 1]

    monkeypatch.setattr(client, "_request", fake_request)
    out = client.list_folder_tasks("IEFOLDER")
    assert [t["id"] for t in out] == ["T1", "T2"]
    assert "nextPageToken" not in calls[0]
    assert calls[1]["nextPageToken"] == "tok2"


def test_list_top_level_folders_filters_to_root_children(monkeypatch):
    # GET /spaces/{id}/folders returns the WHOLE subtree incl. the root (id == space id)
    # and deep descendants; only the root's direct children are top-level.
    client = WrikeClient("tok")
    space_id = "ISPACE"
    subtree = [
        {"id": space_id, "title": "MGF Space", "childIds": ["F_WP", "F_LT"]},  # root
        {"id": "F_WP", "title": "WP - Winnie-the-Pooh", "childIds": ["F_NESTED"]},
        {"id": "F_LT", "title": "LT - Lindt", "childIds": []},
        {"id": "F_NESTED", "title": "WP - nested", "childIds": []},  # descendant, excluded
    ]
    seen = {}

    def fake_request(method, path, **kwargs):
        seen["method"], seen["path"] = method, path
        return {"data": subtree}

    monkeypatch.setattr(client, "_request", fake_request)
    out = client.list_top_level_folders(space_id)
    assert (seen["method"], seen["path"]) == ("GET", f"/spaces/{space_id}/folders")
    assert [f["id"] for f in out] == ["F_WP", "F_LT"]  # root + deep descendant excluded


def test_create_folder_posts_title_as_query_param(monkeypatch):
    client = WrikeClient("tok")
    seen = {}

    def fake_request(method, path, **kwargs):
        seen["method"], seen["path"], seen["params"] = method, path, kwargs.get("params")
        return {"data": [{"id": "F_NEW", "title": "_PENDING_REVIEW"}]}

    monkeypatch.setattr(client, "_request", fake_request)
    out = client.create_folder("ISPACE", "_PENDING_REVIEW")  # space id == top-level parent
    assert (seen["method"], seen["path"]) == ("POST", "/folders/ISPACE/folders")
    assert seen["params"] == {"title": "_PENDING_REVIEW"}  # query param, not JSON body
    assert out["id"] == "F_NEW"


def test_to_body_forwards_custom_item_type_id():
    body = WrikeClient._to_body({"title": "T", "customItemTypeId": "ITYPE_RETAIL"})
    assert body["customItemTypeId"] == "ITYPE_RETAIL"


def test_to_body_omits_custom_item_type_id_when_absent():
    body = WrikeClient._to_body({"title": "T"})
    assert "customItemTypeId" not in body


def test_to_body_translates_custom_fields_dict_to_list():
    body = WrikeClient._to_body(
        {"title": "T", "description": "D", "customFields": {"F1": "a", "F2": "b"}}
    )
    assert body["title"] == "T"
    assert body["description"] == "D"
    assert {"id": "F1", "value": "a"} in body["customFields"]
    assert {"id": "F2", "value": "b"} in body["customFields"]


def test_to_body_omits_absent_fields():
    body = WrikeClient._to_body({"customFields": {}})
    assert "title" not in body
    assert "customFields" not in body


def test_to_body_stringifies_none_values():
    body = WrikeClient._to_body({"customFields": {"F1": None}})
    assert {"id": "F1", "value": ""} in body["customFields"]
