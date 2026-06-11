"""Read changed PLM items: watermark delta, Ready-for-Wrike gate, canonical resolver.

The canonical unit is the **item number**: one Wrike card per distinct `item_number`,
CORE-preferred among that item number's customer rows. This matches the Wrike-side identity
used everywhere else (the `item_number` custom-field search, the raw `item_number` +
`customer` exact match, and reconciliation). `family_id` (first 8 chars of the item number)
is kept only as a descriptive/audit column. (Changed 2026-06-11 from per-family grouping —
see specs/001 Key Decisions.)
"""
from __future__ import annotations

from psycopg.rows import dict_row


def read_changed_items(conn, since) -> list[dict]:
    """Eligible rows changed since the watermark: modified_at > since AND ready_for_wrike."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM plm_item "
            "WHERE modified_at > %s AND ready_for_wrike = true "
            "ORDER BY modified_at, item_number",
            (since,),
        )
        return cur.fetchall()


def resolve_eligible_items(conn, since) -> list[dict]:
    """One canonical record per changed item number (watermark + gate + dedup)."""
    by_item: dict[str, list[dict]] = {}
    for item in read_changed_items(conn, since):
        by_item.setdefault(item["item_number"], []).append(item)
    return [pick_canonical(rows) for rows in by_item.values()]


def sorted_eligible_items(conn, since) -> list[dict]:
    """Eligible canonical records for the delta, oldest-changed first - the order both
    the batch loop (run_sync) and the Service Bus producer process/enqueue them in."""
    return sorted(resolve_eligible_items(conn, since), key=lambda i: i["modified_at"])


def read_one_item(conn, item_number) -> dict | None:
    """The single canonical record for one item number, applying the ready_for_wrike gate.

    The Service Bus consumer carries only ids in the message, so it re-reads the item here
    by item_number and always acts on the current DB state (no watermark - the producer's
    delta already selected it). Returns None if the item is gone or no longer eligible since
    it was enqueued, in which case the consumer acks and skips it.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM plm_item WHERE item_number = %s AND ready_for_wrike = true",
            (item_number,),
        )
        rows = cur.fetchall()
    return pick_canonical(rows) if rows else None


def read_item_customer_pairs(conn) -> list[tuple]:
    """Every (item_number, customer) pair in the source - the reconciliation walk
    matches Wrike cards against these. Deliberately ungated: a card whose record is
    not ready_for_wrike still maps to PLM (it just isn't synced)."""
    with conn.cursor() as cur:
        cur.execute("SELECT item_number, customer FROM plm_item")
        return cur.fetchall()


def pick_canonical(rows: list[dict]) -> dict:
    """Pick the one record that drives the Wrike card from an item number's customer rows.

    Rule (PRD): *CORE, else *CUSTOM; within a tier prefer the latest-updated
    (pure-duplicate handling); if neither tier exists, the earliest-created row.
    """
    for tier in ("*CORE", "*CUSTOM"):
        matches = [r for r in rows if r["customer"] == tier]
        if matches:
            return max(matches, key=lambda r: r["modified_at"])
    return min(rows, key=lambda r: r["created_at"])
