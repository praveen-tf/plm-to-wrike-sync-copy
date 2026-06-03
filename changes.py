"""Source-level change detection + comment/alert formatting.

Changes are detected by comparing the incoming item against the last-synced
snapshot stored in wrike_task_map (not by diffing the Wrike card).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")  # auto PST/PDT; labelled "PT"

SECTION_FIELDS = ["material_codes", "contents"]   # refreshed + commented on change
ALERT_FIELDS = ["title", "brand_category"]         # not written; alerted on change
SNAPSHOT_FIELDS = SECTION_FIELDS + ALERT_FIELDS    # snapshotted + diffed vs last sync

LABELS = {
    "material_codes": "Material Codes",
    "contents": "Contents",
    "title": "Title",
    "brand_category": "Brand Category",
}


def diff_fields(snapshot: dict | None, item: dict, fields: list[str]) -> dict:
    """Return {field: (old, new)} for fields whose value differs from the snapshot."""
    snap = snapshot or {}
    changes = {}
    for f in fields:
        old, new = snap.get(f), item.get(f)
        if old != new:
            changes[f] = (old, new)
    return changes


def detect_section_changes(snapshot: dict | None, item: dict) -> dict:
    return diff_fields(snapshot, item, SECTION_FIELDS)


def detect_alerts(snapshot: dict | None, item: dict) -> dict:
    return diff_fields(snapshot, item, ALERT_FIELDS)


def _lines(changes: dict) -> list[str]:
    return [f"- {LABELS.get(f, f)}: '{old}' -> '{new}'" for f, (old, new) in changes.items()]


def format_pt(now) -> str:
    """Pacific time as M/D/YYYY HH:MM PT, e.g. '6/3/2026 21:15 PT' (24-hour, no leading zeros)."""
    pt = now.astimezone(_PACIFIC)
    return f"{pt.month}/{pt.day}/{pt.year} {pt:%H:%M} PT"


def format_change_comment(changes: dict, now) -> str:
    header = f"PLM sync updated the following on {format_pt(now)}:"
    return "\n".join([header, *_lines(changes)])


def format_alert_comment(alerts: dict, now) -> str:
    header = (
        f"PLM sync ALERT on {format_pt(now)} - "
        "PLM value changed but is not auto-updated:"
    )
    return "\n".join([header, *_lines(alerts)])
