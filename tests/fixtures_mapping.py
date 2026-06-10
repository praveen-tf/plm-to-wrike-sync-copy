"""Builders + constants for the mapping/reconciliation tests.

plm_row() builds a plm_item record by running a source-shaped row through
loader.to_plm_item, so the family/prefix/code derivation lives only in the loader.
wrike_card() builds a pre-existing Wrike card in the shape test_sync.FakeWrike stores
them - used to seed hand-made / customer-variant cards before a run. The constants
name the test folder topology shared across test_sync.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from loader import to_plm_item
from mapping import CUSTOMER_FIELD_ID, ITEM_NUMBER_FIELD_ID

WP_FOLDER, LT_FOLDER, STAGING_FOLDER = "fold-wp", "fold-lt", "fold-staging"

CORE = "*CORE"
INTL_CUSTOMER = "THERANGE"
# A real data-quality value seen in the MGF folder: an HTML anchor pasted into the
# Customer field. The raw exact match against PG's clean '*CORE' must FAIL on it.
HTML_CORE = ('<a rel="nofollow noreferrer noopener" target="_blank" '
             'href="https://mgf.centricsoftware.com/x">*CORE</a>')

T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)


def plm_row(plm_internal_id, item_number, *, customer=CORE, ready=True,
            created_at=T0, modified_at=T0, **extra) -> dict:
    """One plm_item record at loader grain (insert via loader.load_plm_items)."""
    family_id = item_number[:8]
    source = {
        "Key": plm_internal_id,
        "PLM - Item #": item_number,
        "PLM Item Name": f"{family_id} TEST ITEM",
        "Title": f"{family_id} {family_id} TEST ITEM",
        "Status": "Active",
        "Priority": "Normal",
        "Description": "<h5>Material Codes:     </h5>MAT<br><h5>Contents:     </h5>CONTENTS",
        "Print Method": "MATTE",
        "PLM - Contents": "CONTENTS",
        "PLM - Material Codes": "MAT",
        "Brand Category": "THOUGHTFULLY GOURMET",
        "Customer": customer,
        "Product Category": "BAKING",
        "Brand": "TEST",
        "Season": "2026 FALL/HOLIDAY",
    }
    row = to_plm_item(source, now=created_at)
    row["ready_for_wrike"] = ready
    row["modified_at"] = modified_at
    row.update(extra)
    return row


def wrike_card(item_number, customer, *, title=None, folder=WP_FOLDER) -> dict:
    """A live Wrike card as FakeWrike stores it (customFields as [{'id','value'}])."""
    return {
        "title": title or f"{item_number[:8]} TEST ITEM",
        "description": "",
        "parentIds": [folder],
        "customFields": [
            {"id": ITEM_NUMBER_FIELD_ID, "value": item_number},
            {"id": CUSTOMER_FIELD_ID, "value": customer},
        ],
    }
