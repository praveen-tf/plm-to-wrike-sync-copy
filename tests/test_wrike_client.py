from wrike_client import WrikeClient


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
