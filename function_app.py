"""Azure Functions app: PLM -> Wrike sync, decoupled via a Service Bus queue.

Two functions share one codebase (see README.md):

* plm_wrike_producer  - timer. Refreshes the local plm_item mirror from the Centric 8 API
  (the delta since the watermark; the first run backfills the full ready set), then reads the
  changed/eligible canonical items (one per item number) and pushes one message per item onto
  the `plm-sync` queue, then advances the enqueue watermark.
  NCRONTAB `0 0 12,0 * * *` = 12:00 and 00:00 UTC = 05:00 / 17:00 Pacific. The schedule
  only fires once deployed to Azure (run_on_startup=False).

* plm_wrike_consumer  - Service Bus queue trigger. Processes ONE message: re-reads the
  item by item_number and creates/updates its Wrike card. An unhandled exception abandons the
  lock so Service Bus retries, then dead-letters after maxDeliveryCount.

Secrets (Wrike tokens, Centric credentials, database and Service Bus connection strings)
come from Key Vault references in the Function App settings. The test suite runs against a
local Postgres (see db.py for connection defaults and README.md for setup).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from centric_client import make_centric_client
from db import connect
from folder_sync import sync_folders
from loader import refresh_plm_items
from mapping import load_category_author_map
from plm_reader import read_one_item, sorted_eligible_items
from settings import load_local_settings
from state import EPOCH, get_watermark, set_watermark
from sync import load_sync_context, reconcile_folders, sync_one_family
from wrike_client import make_wrike_client

app = func.FunctionApp()

QUEUE = os.environ.get("ServiceBusQueue", "plm-sync")


def _item_message(item: dict) -> ServiceBusMessage:
    """One queue message pointing at a changed item (ids + modified_at, not a snapshot).

    message_id = item_number:modified_at lets Service Bus duplicate detection drop a
    re-enqueue of the same unchanged item at the broker.
    """
    modified = item["modified_at"].isoformat()
    return ServiceBusMessage(
        json.dumps({"item_number": item["item_number"],
                    "plm_internal_id": item["plm_internal_id"],
                    "modified_at": modified}),
        message_id=f'{item["item_number"]}:{modified}',
        content_type="application/json",
    )


@app.timer_trigger(schedule="0 0 12,0 * * *", arg_name="timer", run_on_startup=False)
def plm_wrike_producer(timer: func.TimerRequest) -> None:
    load_local_settings()
    now = datetime.now(timezone.utc)
    with connect() as conn:
        watermark = get_watermark(conn) or EPOCH
        # Refresh the plm_item mirror from Centric (the delta since the watermark) before
        # reading it; the first run (watermark == EPOCH) backfills the full ready set.
        refreshed = refresh_plm_items(conn, make_centric_client(), since=watermark, now=now)
        logging.info("PLM->Wrike producer: refreshed %d Centric style(s) into plm_item", refreshed)

        # Rebuild wrike_folder_map from the configured Wrike space so the consumer routes
        # cards against current folders. Runs under the catch-all author identity, which
        # must be a member of WRIKE_SPACE_ID with folder-create permission.
        catch_all = load_category_author_map(conn).get("*")
        if catch_all is None:
            raise RuntimeError("category_author_map has no '*' catch-all row: no Wrike "
                               "identity to run the folder sync with")
        folder_client = make_wrike_client(catch_all[1])  # (author, token_ref)
        folder_summary = sync_folders(conn, folder_client,
                                      space_id=os.environ["WRIKE_SPACE_ID"])
        logging.info("PLM->Wrike producer: folder sync %s", folder_summary)

        items = sorted_eligible_items(conn, watermark)
        if not items:
            logging.info("PLM->Wrike producer: nothing changed since %s", watermark)
            return

        conn_str = os.environ["ServiceBusSendConnection"]
        with ServiceBusClient.from_connection_string(conn_str) as sb, \
                sb.get_queue_sender(QUEUE) as sender:
            # One batched send: the SDK packs the list into broker batches instead of a
            # round-trip per item.
            sender.send_messages([_item_message(item) for item in items])

        # Enqueue is the producer's unit of done; the queue owns delivery + retry from
        # here, so the watermark advances once everything is safely queued.
        set_watermark(conn, max(i["modified_at"] for i in items),
                      rows_in_delta=len(items), rows_succeeded=len(items),
                      rows_failed=0)
    logging.info("PLM->Wrike producer: enqueued %d items onto %s", len(items), QUEUE)


@app.route(route="reconcile", auth_level=func.AuthLevel.FUNCTION)
def plm_wrike_reconcile(req: func.HttpRequest) -> func.HttpResponse:
    """Full-folder reconciliation, run on demand (one-time bootstrap or periodic
    audit) - separate from the per-message sync. Walks every card in each managed
    folder and review-logs the ones that don't map to a PLM record by
    (item_number + raw customer) into wrike_unmapped_log, hand-made cards included.
    Read-only against Wrike. The log is exported to Excel for the client's
    data-quality review."""
    load_local_settings()
    with connect() as conn:
        summary = reconcile_folders(conn, make_wrike_client, load_sync_context(conn))
    logging.info("PLM->Wrike reconcile: %s", summary)
    return func.HttpResponse(json.dumps(summary), mimetype="application/json")


@app.service_bus_queue_trigger(arg_name="msg", queue_name="plm-sync",
                               connection="ServiceBusConnection")
def plm_wrike_consumer(msg: func.ServiceBusMessage) -> None:
    load_local_settings()
    payload = json.loads(msg.get_body().decode("utf-8"))
    item_number = payload["item_number"]
    now = datetime.now(timezone.utc)

    with connect() as conn:
        # The message is a pointer, not a snapshot: re-read current DB state by item_number
        # so a record edited between enqueue and processing syncs its latest values.
        item = read_one_item(conn, item_number)
        if item is None:  # gone / no longer ready since enqueue -> ack and skip
            logging.info("consumer: item %s no longer eligible; skipping", item_number)
            return

        # Let exceptions propagate: an unhandled error abandons the SB lock so the broker
        # redelivers, then dead-letters after maxDeliveryCount (no in-process try/except).
        status = sync_one_family(conn, make_wrike_client, item, load_sync_context(conn), now)
    logging.info("consumer: item %s -> %s (delivery #%s)",
                 item_number, status, msg.delivery_count)
