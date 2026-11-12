#!/usr/bin/env bash
# Start a fresh AWS Emulator (telemetry off) on :9297 and wait for health. PID goes to /root/emulator.pid.
set -euo pipefail
pkill -TERM -x aws-emulator 2>/dev/null && sleep 1 || true
nohup aws-emulator --telemetry off "$@" > /root/results/emulator-server.log 2>&1 &
echo $! > /root/emulator.pid
for i in $(seq 100); do
  curl -sf --cacert ~/.aws/emulator/9297/ca.pem https://127.0.0.1:9297/__admin/health >/dev/null 2>&1 && { echo "ready pid=$(cat /root/emulator.pid)"; exit 0; }
  sleep 0.1
done
echo "emulator did not become healthy" >&2; exit 1
