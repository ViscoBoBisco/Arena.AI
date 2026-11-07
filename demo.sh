#!/usr/bin/env bash
# End-to-end demo against a running gateway (default http://localhost:8080).
# Usage: ./demo.sh [base_url]
set -euo pipefail
BASE="${1:-http://localhost:8080}"
J='Content-Type: application/json'
P='X-Project-Id: demo'
A='Authorization: Bearer dev-key'

echo "== health =="
curl -s "$BASE/healthz"; echo
curl -s "$BASE/readyz"; echo

echo "== tools =="
curl -s "$BASE/v1/tools" -H "$A" | python3 -c "import json,sys; print('\n'.join(t['name'] for t in json.load(sys.stdin)['tools']))"

echo "== submit research task (async 202) =="
TASK=$(curl -s -X POST "$BASE/v1/tasks" -H "$J" -H "$P" -H "$A" -H "$A" -d '{
  "project_id":"demo","idempotency_key":"demo-'$RANDOM'",
  "request":{"intent":"research","name":"demo.html","instructions":"one-line summary"},
  "permissions":["web.read.public","model.infer"]}')
echo "$TASK" | python3 -c "import json,sys; o=json.load(sys.stdin); print(o['task_id'], o['state'], o.get('status_url',''))"
TID=$(echo "$TASK" | python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])")

echo "== poll until terminal =="
for i in $(seq 1 50); do
  ST=$(curl -s "$BASE/v1/tasks/$TID" -H "$P" -H "$A" -H "$A" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")
  echo "state: $ST"
  case "$ST" in COMPLETED|FAILED|CANCELLED|REJECTED) break;; esac
  sleep 0.2
done

echo "== evidence citations =="
curl -s "$BASE/v1/tasks/$TID/evidence" -H "$P" -H "$A" -H "$A" | python3 -m json.tool | head -12

echo "== metrics summary =="
curl -s "$BASE/v1/metrics/summary" -H "$A"; echo

echo "== duplicate submission dedupes =="
curl -s -X POST "$BASE/v1/tasks" -H "$J" -H "$P" -H "$A" -H "$A" -d '{
  "project_id":"demo","idempotency_key":"dup-key-1",
  "request":{"intent":"echo","instructions":"hi"},"permissions":[]}' | python3 -c "import json,sys; print('first:', json.load(sys.stdin)['task_id'])"
curl -s -X POST "$BASE/v1/tasks" -H "$J" -H "$P" -H "$A" -H "$A" -d '{
  "project_id":"demo","idempotency_key":"dup-key-1",
  "request":{"intent":"echo","instructions":"hi"},"permissions":[]}' | python3 -c "import json,sys; o=json.load(sys.stdin); print('second duplicate:', o.get('duplicate'), o['task_id'])"
