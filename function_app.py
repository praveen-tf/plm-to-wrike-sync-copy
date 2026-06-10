"""Azure Functions app: PLM -> Wrike sync, decoupled via a Service Bus queue.

Two functions share one codebase (see README.md):

* plm_wrike_producer  - timer. Reads the changed/eligible canonical families and pushes
  one message per family onto the `plm-sync` queue, then advances the enqueue watermark.
  NCRONTAB `0 0 12,0 * * *` = 12:00 and 00:00 UTC = 05:00 / 17:00 Pacific. The schedule
  only fires once deployed to Azure (run_on_startup=False).

* plm_wrike_consumer  - Service Bus queue trigger. Processes ONE message: re-reads the
  family by id and creates/updates its Wrike card. An unhandled exception abandons the
  lock so Service Bus retries, then dead-letters after maxDeliveryCount.

Secrets (Wrike tokens, database and Service Bus connection strings) come from Key Vault
references in the Function App settings. The test suite runs locally against the Postgres
in docker-compose; see README.md.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from db import connect
from plm_reader import read_one_family, sorted_eligible_families
from settings import load_local_settings
from state import EPOCH, get_watermark, set_watermark
from sync import load_sync_context, reconcile_folders, sync_one_family
from wrike_client import make_wrike_client

app = func.FunctionApp()

QUEUE = os.environ.get("ServiceBusQueue", "plm-sync")


def _family_message(item: dict) -> ServiceBusMessage:
    """One queue message pointing at a changed family (ids + modified_at, not a snapshot).

    message_id = family_id:modified_at lets Service Bus duplicate detection drop a
    re-enqueue of the same unchanged family at the broker.
    """
    modified = item["modified_at"].isoformat()
    return ServiceBusMessage(
        json.dumps({"family_id": item["family_id"],
                    "plm_internal_id": item["plm_internal_id"],
                    "modified_at": modified}),
        message_id=f'{item["family_id"]}:{modified}',
        content_type="application/json",
    )


@app.timer_trigger(schedule="0 0 12,0 * * *", arg_name="timer", run_on_startup=False)
def plm_wrike_producer(timer: func.TimerRequest) -> None:
    load_local_settings()
    with connect() as conn:
        watermark = get_watermark(conn) or EPOCH
        families = sorted_eligible_families(conn, watermark)
        if not families:
            logging.info("PLM->Wrike producer: nothing changed since %s", watermark)
            return

        conn_str = os.environ["ServiceBusSendConnection"]
        with ServiceBusClient.from_connection_string(conn_str) as sb, \
                sb.get_queue_sender(QUEUE) as sender:
            # One batched send: the SDK packs the list into broker batches instead of a
            # round-trip per family.
            sender.send_messages([_family_message(item) for item in families])

        # Enqueue is the producer's unit of done; the queue owns delivery + retry from
        # here, so the watermark advances once everything is safely queued.
        set_watermark(conn, max(i["modified_at"] for i in families),
                      rows_in_delta=len(families), rows_succeeded=len(families),
                      rows_failed=0)
    logging.info("PLM->Wrike producer: enqueued %d families onto %s", len(families), QUEUE)


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
    family_id = payload["family_id"]
    now = datetime.now(timezone.utc)

    with connect() as conn:
        # The message is a pointer, not a snapshot: re-read current DB state by id so a
        # record edited between enqueue and processing syncs its latest values.
        item = read_one_family(conn, family_id)
        if item is None:  # gone / no longer ready since enqueue -> ack and skip
            logging.info("consumer: family %s no longer eligible; skipping", family_id)
            return

        # Let exceptions propagate: an unhandled error abandons the SB lock so the broker
        # redelivers, then dead-letters after maxDeliveryCount (no in-process try/except).
        status = sync_one_family(conn, make_wrike_client, item, load_sync_context(conn), now)
    logging.info("consumer: family %s -> %s (delivery #%s)",
                 family_id, status, msg.delivery_count)
