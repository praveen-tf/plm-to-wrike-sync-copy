"""Sync state: the PLM-family -> Wrike-task map (+ last-synced snapshot) and the watermark."""
from __future__ import annotations

from datetime import datetime, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Json

from changes import SNAPSHOT_FIELDS

# Watermark floor: the "nothing synced yet" timestamp used before the first run.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# snapshot field -> wrike_task_map column (single source: changes.SNAPSHOT_FIELDS)
_SNAP_COLS = {field: f"snap_{field}" for field in SNAPSHOT_FIELDS}


def get_task_map_entry(conn, family_id: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM wrike_task_map WHERE family_id = %s", (family_id,))
        row = cur.fetchone()
    if row is None:
        return None
    row["snapshot"] = {key: row[col] for key, col in _SNAP_COLS.items()}
    return row


def upsert_task_map(conn, *, family_id, plm_internal_id, wrike_task_id,
                    wrike_permalink, snapshot, now) -> None:
    columns = ["family_id", "plm_internal_id", "wrike_task_id", "wrike_permalink",
               *_SNAP_COLS.values(), "created_at", "last_synced_at"]
    values = [family_id, plm_internal_id, wrike_task_id, wrike_permalink,
              *(snapshot[field] for field in _SNAP_COLS), now, now]
    # On conflict, refresh everything except the identity key and the original created_at.
    updates = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in columns if c not in ("family_id", "created_at")
    )
    sql = (
        f"INSERT INTO wrike_task_map ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) "
        f"ON CONFLICT (family_id) DO UPDATE SET {updates}"
    )
    with conn.cursor() as cur:
        cur.execute(sql, values)
    conn.commit()


def get_watermark(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT last_modified_at FROM sync_watermark WHERE id = 1")
        row = cur.fetchone()
    return row[0] if row else None


def set_watermark(conn, ts, *, rows_in_delta=None, rows_succeeded=None,
                  rows_failed=None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_watermark "
            "(id, last_modified_at, run_completed_at, rows_in_delta, rows_succeeded, rows_failed) "
            "VALUES (1, %s, now(), %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "last_modified_at = EXCLUDED.last_modified_at, "
            "run_completed_at = EXCLUDED.run_completed_at, "
            "rows_in_delta = EXCLUDED.rows_in_delta, "
            "rows_succeeded = EXCLUDED.rows_succeeded, "
            "rows_failed = EXCLUDED.rows_failed",
            (ts, rows_in_delta, rows_succeeded, rows_failed),
        )
    conn.commit()


def record_sync_result(conn, family_id, status, error=None) -> None:
    """Stamp the per-record sync outcome on the family's wrike_task_map row (Stage 5).

    Called after process_family() decides create/update/unchanged/deferred/failed. A
    deferred/failed family may have no map row yet (it is upserted only on a successful
    create/update), in which case the UPDATE matches 0 rows and is a harmless no-op -
    the failure is captured in sync_dlq instead.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE wrike_task_map SET sync_status = %s, error_message = %s, "
            "updated_at = now() WHERE family_id = %s",
            (status, error, family_id),
        )
    conn.commit()


def dead_letter(conn, *, family_id, plm_internal_id, reason, payload=None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sync_dlq (family_id, plm_internal_id, reason, payload) "
            "VALUES (%s, %s, %s, %s)",
            (family_id, plm_internal_id, reason, Json(payload) if payload is not None else None),
        )
    conn.commit()
