#!/usr/bin/env bash
# Manually exercise the deployed PLM -> Wrike pipeline from a terminal.
# See docs/azure_deployment_guide_2026-06-03.md section 7.
#
# Usage:
#   ./scripts/trigger_azure.sh producer        # fire the timer producer on demand
#   ./scripts/trigger_azure.sh send WP-71511    # inject one test message onto the queue
#   ./scripts/trigger_azure.sh status           # show queue + dead-letter counts
#   ./scripts/trigger_azure.sh logs             # tail the consumer logs
set -euo pipefail

RG="${RG:-rg-wrike-sync}"
SB_NS="${SB_NS:-sb-wrike-poc-pk}"
QUEUE="${QUEUE:-plm-sync}"
PRODUCER="${PRODUCER:-func-wrike-producer}"
CONSUMER="${CONSUMER:-func-wrike-consumer}"

cmd="${1:-status}"
case "$cmd" in
  producer)
    # Azure Functions admin endpoint runs a non-HTTP function by name (needs the master key).
    KEY=$(az functionapp keys list -g "$RG" -n "$PRODUCER" --query masterKey -o tsv)
    curl -s -X POST "https://${PRODUCER}.azurewebsites.net/admin/functions/plm_wrike_producer" \
         -H "x-functions-key: ${KEY}" -H "Content-Type: application/json" -d '{}'
    echo "Triggered producer. Check 'status' then 'logs'."
    ;;
  send)
    family="${2:?usage: trigger_azure.sh send <family_id> [plm_internal_id]}"
    pid="${3:-test}"
    az servicebus queue message send -g "$RG" --namespace-name "$SB_NS" \
       --queue-name "$QUEUE" \
       --body "{\"family_id\":\"${family}\",\"plm_internal_id\":\"${pid}\",\"modified_at\":\"2026-06-03T00:00:00+00:00\"}"
    echo "Sent one message for ${family}."
    ;;
  status)
    az servicebus queue show -g "$RG" --namespace-name "$SB_NS" -n "$QUEUE" \
       --query "countDetails" -o jsonc
    ;;
  logs)
    az webapp log tail -g "$RG" -n "$CONSUMER"
    ;;
  *)
    echo "unknown command: $cmd (use: producer | send | status | logs)" >&2
    exit 2
    ;;
esac
