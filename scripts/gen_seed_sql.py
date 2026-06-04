"""Generate a portable seed_plm_item.sql (INSERTs) from the input Excel.

Uses the repo's loader so derived columns (family_id/prefix/code) and the
column set exactly match what the app expects. Output is idempotent
(ON CONFLICT DO UPDATE) and uses a fixed timestamp so re-runs are stable.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import loader

EXCEL = "data/input/winnie_the_pooh_input.xlsx"
CSV = "data/input/synthetic_jesse_confection.csv"
OUT = "seed_plm_item.sql"
# Fixed seed timestamp (past) so the producer's watermark picks the rows up.
NOW = dt.datetime(2026, 6, 4, 0, 0, 0, tzinfo=dt.timezone.utc)


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dt.datetime):
        return "'" + v.isoformat() + "'"
    s = str(v).replace("'", "''")
    return "'" + s + "'"


def main() -> None:
    rows = loader.read_source_rows(EXCEL, CSV)
    items = [loader.to_plm_item(r, now=NOW) for r in rows]
    cols = loader.PLM_ITEM_COLUMNS
    collist = ", ".join(cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "plm_internal_id")

    lines = [
        "-- Generated seed for plm_item (" + str(len(items)) + " rows).",
        "-- Idempotent: re-running upserts on plm_internal_id.",
        "BEGIN;",
    ]
    for it in items:
        vals = ", ".join(lit(it.get(c)) for c in cols)
        lines.append(
            f"INSERT INTO plm_item ({collist}) VALUES ({vals})\n"
            f"  ON CONFLICT (plm_internal_id) DO UPDATE SET {updates};"
        )
    lines.append("COMMIT;")
    lines.append(f"-- end ({len(items)} rows)")
    Path(OUT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(items)} rows, {len(cols)} columns")


if __name__ == "__main__":
    main()
