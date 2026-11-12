#!/usr/bin/env bash
# (a) Start with the page cache dropped, so the 926 MiB binary is read from disk (5 runs).
# (b) Split RSS into anonymous vs file-backed pages: the binary is memory-mapped, so plain
#     RSS includes binary pages the kernel can drop and share.
# (c) Check whether a clean SIGTERM stop removes ~/.aws/emulator/<port>/ as the guide says.
set -euo pipefail
OUT=/root/results/cold-cache-start.csv
echo "run,ready_ms,rss_kib,rss_anon_kib,rss_file_kib" > "$OUT"
for i in 1 2 3 4 5; do
  home=$(mktemp -d)
  sync; echo 3 > /proc/sys/vm/drop_caches
  t0=$(date +%s%N)
  HOME=$home aws-emulator --telemetry off > /dev/null 2>&1 &
  pid=$!
  until curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:9297/__admin/health 2>/dev/null | grep -q 200; do sleep 0.005; done
  t1=$(date +%s%N)
  read -r rss anon file < <(awk '/^VmRSS/{r=$2} /^RssAnon/{a=$2} /^RssFile/{f=$2} END{print r, a, f}' /proc/$pid/status)
  echo "$i,$(( (t1 - t0) / 1000000 )),$rss,$anon,$file" >> "$OUT"
  kill -TERM $pid; wait $pid 2>/dev/null || true
  rm -rf "$home"
done
cat "$OUT"

# Cleanup check, with a pause after the process exits
home=$(mktemp -d)
HOME=$home aws-emulator --telemetry off > /root/results/cleanup-run.log 2>&1 &
pid=$!
until curl -sk -o /dev/null https://127.0.0.1:9297/__admin/health; do sleep 0.05; done
sleep 2
kill -TERM $pid
wait $pid; code=$?
sleep 2
{
  echo "exit_code_after_sigterm: $code"
  echo "port_dir_after_stop:"
  ls -la "$home/.aws/emulator/" 2>&1
  ls -la "$home/.aws/emulator/9297/" 2>&1 || echo "(9297 dir removed)"
} | tee /root/results/cleanup-check.txt
tail -3 /root/results/cleanup-run.log >> /root/results/cleanup-check.txt
rm -rf "$home"
