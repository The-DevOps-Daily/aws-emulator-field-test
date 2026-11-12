#!/usr/bin/env bash
# Cold start: time from exec to the first HTTP 200 from /__admin/health, 10 runs, fresh HOME each.
# Idle memory: RSS right after ready and after 60 s idle.
set -euo pipefail
OUT=/root/results/cold-start.csv
RUNS=${RUNS:-10}
echo "run,ready_ms,rss_kib_at_ready" > "$OUT"

for i in $(seq "$RUNS"); do
  home=$(mktemp -d)
  t0=$(date +%s%N)
  HOME=$home aws-emulator --telemetry off > "/root/results/startup-log-run$i.txt" 2>&1 &
  pid=$!
  until curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:9297/__admin/health 2>/dev/null | grep -q 200; do
    sleep 0.005
  done
  t1=$(date +%s%N)
  rss=$(awk '/VmRSS/{print $2}' /proc/$pid/status)
  echo "$i,$(( (t1 - t0) / 1000000 )),$rss" >> "$OUT"
  if [ "$i" -eq 1 ]; then
    ss -ltnp | grep aws-emulator > /root/results/listening-ports.txt || true
    ls -la "$home/.aws/emulator/9297/" > /root/results/emulator-home-files.txt
  fi
  kill -TERM $pid; wait $pid 2>/dev/null || true
  # The guide says a clean stop deletes ~/.aws/emulator/<port>/
  [ -d "$home/.aws/emulator/9297" ] && echo "run $i: port dir still present after SIGTERM" >> /root/results/cleanup-check.txt
  rm -rf "$home"
done
cat "$OUT"

# Idle memory over 60 s
home=$(mktemp -d)
HOME=$home aws-emulator --telemetry off > /root/results/startup-log-idle.txt 2>&1 &
pid=$!
until curl -sk -o /dev/null https://127.0.0.1:9297/__admin/health; do sleep 0.05; done
START=$(date +%s)
{
  echo "t_seconds,rss_kib,threads"
  for s in 0 5 10 20 30 45 60; do
    while [ "$(date +%s)" -lt "$(( START + s ))" ]; do sleep 0.2; done
    echo "$s,$(awk '/VmRSS/{print $2}' /proc/$pid/status),$(awk '/Threads/{print $2}' /proc/$pid/status)"
  done
} > /root/results/idle-memory.csv
kill -TERM $pid; wait $pid 2>/dev/null || true
rm -rf "$home"
cat /root/results/idle-memory.csv
