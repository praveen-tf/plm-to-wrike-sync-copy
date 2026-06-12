"""Builders + constants for the mapping/reconciliation tests.

plm_row() builds a plm_item record by running a Centric-shaped style through
loader.style_to_plm_item (with an identity resolver), so the field map / family-prefix-
code derivation lives only in the loader. load_sample_items() loads the 11-item test
topology (replaces the retired Excel/CSV source fixtures). wrike_card() builds a
pre-existing Wrike card in the shape test_sync.FakeWrike stores them. The constants name
the test folder topology shared across test_sync.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from loader import load_plm_items, style_to_plm_item
from mapping import CUSTOMER_FIELD_ID, ITEM_NUMBER_FIELD_ID

WP_FOLDER, LT_FOLDER, STAGING_FOLDER = "fold-wp", "fold-lt", "fold-staging"

CORE = "*CORE"
INTL_CUSTOMER = "THERANGE"
# A real data-quality value seen in the MGF folder: an HTML anchor pasted into the
# Customer field. The raw exact match against PG's clean '*CORE' must FAIL on it.
HTML_CORE = ('<a rel="nofollow noreferrer noopener" target="_blank" '
             'href="https://mgf.centricsoftware.com/x">*CORE</a>')

T0 = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

# The 11-item test topology: 6 WP (BAKING -> Praveen) + 5 LT (CONFECTION -> Jesse).
WP_ITEMS = ["WP-71511-006-319", "WP-71512-006-319", "WP-71513-006-319",
            "WP-71514-006-319", "WP-71515-004-319", "WP-71516-004-319"]
LT_ITEMS = ["LT-68102-006-319", "LT-68103-006-319", "LT-68104-006-319",
            "LT-68105-006-319", "LT-68106-006-319"]


def _identity_resolve(endpoint, ref_id):
    """Test resolver: the style passes display values straight through as the ref id."""
    return ref_id or ""


def plm_row(plm_internal_id, item_number, *, customer=CORE, ready=True,
            created_at=T0, modified_at=T0, **extra) -> dict:
    """One plm_item record built through the Centric mapper (loader.style_to_plm_item),
    so the field map / family-prefix-code derivation lives only in the loader."""
    family_id = item_number[:8]
    style = {
        "id": plm_internal_id,
        "active": True,
        "mgf_item_identifier": item_number,
        "node_name": f"{family_id} TEST ITEM",
        "category_2": customer,                  # -> customer (identity resolve)
        "category_1": "BAKING",                  # -> product_category (drives author map)
        "collection": "TEST",                    # -> brand
        "parent_season": "2026 FALL/HOLIDAY",    # -> season
        "mgf_print_method": ["MATTE"],
        "mgf_test_material": "CONTENTS",
        "mgf_material_code_item": "MAT",
        "mgf_brand_category_2": "THOUGHTFULLY GOURMET",
        "mgf_ready_for_wrike": ready,
        "_modified_at": None,                    # mapper falls back to created_at
    }
    row = style_to_plm_item(style, _identity_resolve, now=created_at)
    row["modified_at"] = modified_at
    row.update(extra)
    return row


def load_sample_items(conn, *, now=T0) -> None:
    """Load the 11-item topology (6 WP -> Praveen, 5 LT/CONFECTION -> Jesse), one *CORE
    record per item number - replaces the retired Excel/CSV source fixtures."""
    rows = [plm_row(str(i), n, created_at=now, modified_at=now)
            for i, n in enumerate(WP_ITEMS, start=1)]
    rows += [plm_row(str(i), n, created_at=now, modified_at=now,
                     product_category="CONFECTION")
             for i, n in enumerate(LT_ITEMS, start=len(WP_ITEMS) + 1)]
    load_plm_items(conn, rows)


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
