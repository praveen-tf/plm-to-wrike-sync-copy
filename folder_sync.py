"""Folder-sync stage: rebuild wrike_folder_map from a Wrike space's top-level folders.

Runs at the start of every producer run, BEFORE the engine reads anything. It is now the
sole writer of wrike_folder_map (the table used to be hand-maintained). It makes the table
a faithful mirror of WRIKE_SPACE_ID's top-level folders:

  - each folder titled "<PREFIX> - <Brand>" becomes a (prefix, brand) row. Prefix is the
    routing key; brand is the tiebreaker when two brands share a prefix (e.g. MB - MR BEAST
    vs MB - MEAT BOARDS). Titles without " - " are skipped (no derivable prefix).
  - the "_PENDING_REVIEW" staging folder is guaranteed to exist (created in Wrike if absent)
    and stored under the '*' row, so the unchanged mapping.resolve_folder fallback still works.

The table is TRUNCATEd and rebuilt every run, so renamed/removed folders never leave stale
rows and a changed space id is handled for free. Everything downstream is unchanged and
routes prefix-only (see mapping.resolve_folder).
"""
from __future__ import annotations

import logging

PENDING_REVIEW_TITLE = "_PENDING_REVIEW"

_INSERT_ROW = (
    "INSERT INTO wrike_folder_map "
    "(prefix, wrike_folder_id, full_folder_name, brand, space_id) "
    "VALUES (%s, %s, %s, %s, %s)"
)


def sync_folders(conn, client, *, space_id: str) -> dict:
    """Rebuild wrike_folder_map from space_id's top-level folders. Returns a summary."""
    folders = client.list_top_level_folders(space_id)

    seen: set[tuple[str, str]] = set()
    inserted = skipped = 0
    with conn.cursor() as cur:
        cur.execute("TRUNCATE wrike_folder_map")

        for folder in folders:
            title = folder["title"]
            if title == PENDING_REVIEW_TITLE:
                continue  # the staging folder is handled separately, below
            prefix, separator, brand = title.partition(" - ")
            if not separator:  # no "<PREFIX> - <Brand>" split -> no routing key
                logging.warning("folder sync: skipping folder %s - title %r has no ' - '",
                                folder["id"], title)
                skipped += 1
                continue
            if (prefix, brand) in seen:  # (prefix, brand) is the PK - a true duplicate folder
                logging.warning("folder sync: skipping folder %s - duplicate (prefix, brand) "
                                "%r (%r)", folder["id"], (prefix, brand), title)
                skipped += 1
                continue
            seen.add((prefix, brand))
            cur.execute(_INSERT_ROW, (prefix, folder["id"], title, brand, space_id))
            inserted += 1

        # Guarantee the staging folder exists in Wrike, then store it as the '*' fallback
        # (brand '' - the staging row has no brand).
        pending = next((f for f in folders if f["title"] == PENDING_REVIEW_TITLE), None)
        created = pending is None
        if created:
            pending = client.create_folder(space_id, PENDING_REVIEW_TITLE)
        cur.execute(_INSERT_ROW, ("*", pending["id"], PENDING_REVIEW_TITLE, "", space_id))

    conn.commit()
    summary = {"found": len(folders), "inserted": inserted, "skipped": skipped,
               "pending_review_created": created}
    logging.info("folder sync: %s", summary)
    return summary
