"""Read ready-for-Wrike styles from the upstream Postgres PLM database.

The source DB (centric_8_plm schema, populated by Airbyte from Centric 8) has its own
credentials, separate from the app/state database. connect_source() opens that connection;
read_ready_styles() runs the JOIN query that pre-resolves reference fields so the mapper
in loader.py needs no extra lookups.
"""
from __future__ import annotations

import os
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from state import EPOCH


def connect_source():
    """Open a psycopg connection to the source PLM Postgres.

    Uses PLM_SOURCE_PG_CONN (full conninfo string) or the PLM_SOURCE_PG_HOST/PORT/DB/USER/
    PASSWORD component vars. Mirrors db.pg_conninfo()/connect().
    """
    if os.environ.get("PLM_SOURCE_PG_CONN"):
        conninfo = os.environ["PLM_SOURCE_PG_CONN"]
    else:
        host = os.environ.get("PLM_SOURCE_PG_HOST", "localhost")
        port = os.environ.get("PLM_SOURCE_PG_PORT", "5432")
        db = os.environ.get("PLM_SOURCE_PG_DB", "plm_source")
        user = os.environ.get("PLM_SOURCE_PG_USER", "plm_source")
        pw = os.environ.get("PLM_SOURCE_PG_PASSWORD", "")
        conninfo = f"host={host} port={port} dbname={db} user={user} password={pw}"
    return psycopg.connect(conninfo)


_SOURCE_QUERY = """
SELECT
  s.id,
  s.mgf_item_identifier      #>> '{}' AS item_number,
  s.node_name                         AS item_name,
  s.mgf_print_method         #>> '{}' AS mgf_print_method,
  s.mgf_previous_item_number #>> '{}' AS previous_item_number,
  s.mgf_test_material        #>> '{}' AS contents,
  s.mgf_material_code_item   #>> '{}' AS material_codes,
  s.mgf_brand_category_2     #>> '{}' AS brand_category,
  c2.node_name                        AS customer,
  c1.node_name                        AS product_category,
  col.node_name                       AS brand,
  ps.node_name                        AS season,
  s.mgf_item_description     #>> '{}' AS design_request,
  s.mgf_image_link           #>> '{}' AS mgf_image_link,
  s.mgf_ready_for_wrike      #>> '{}' AS mgf_ready_for_wrike,
  s._modified_at
FROM centric_8_plm.styles s
LEFT JOIN centric_8_plm.category2s  c2  ON c2.id  = s.category_2
LEFT JOIN centric_8_plm.category1s  c1  ON c1.id  = s.category_1
LEFT JOIN centric_8_plm.collections col ON col.id = s.collection
LEFT JOIN centric_8_plm.seasons     ps  ON ps.id  = s.parent_season
WHERE s.mgf_ready_for_wrike #>> '{}' = 'true'
"""


def read_ready_styles(source_conn, since: datetime) -> list[dict]:
    """Return all ready-for-Wrike styles from centric_8_plm with reference fields
    pre-resolved by JOIN.

    When since > EPOCH, only styles modified after that point are returned (delta);
    at EPOCH, the full ready set is returned (first-run backfill).
    """
    sql = _SOURCE_QUERY
    if since > EPOCH:
        sql += "  AND s._modified_at::timestamptz > %(since)s\n"
        params = {"since": since}
    else:
        params = {}
    sql += "ORDER BY s._modified_at::timestamptz, item_number"
    with source_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
