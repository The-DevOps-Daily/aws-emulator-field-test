#!/usr/bin/env bash
# Run the same scenario suite and IAM matrix against Moto (server mode) and Floci, each in its
# default configuration. Writes results/<target>-scenarios.json, <target>-iam-gap.json, compare-startup.txt.
set -uo pipefail
OUT=/root/results/compare-startup.txt
: > "$OUT"
pkill -TERM -x aws-emulator 2>/dev/null || true

use_http_env() {
  unset AWS_CA_BUNDLE AWS_SHARED_CREDENTIALS_FILE AWS_CONFIG_FILE AWS_PROFILE AWS_SESSION_TOKEN NODE_EXTRA_CA_CERTS
  export AWS_ENDPOINT_URL=$1 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
  export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true AWS_DISABLE_HOST_PREFIX_INJECTION=true
}

wait_ready() { # $1 url, prints ms
  local t0=$2
  for i in $(seq 600); do
    curl -s -o /dev/null "$1" && { echo $(( ($(date +%s%N) - t0) / 1000000 )); return 0; }
    sleep 0.05
  done
  echo timeout
}

# ---- Moto server
use_http_env http://127.0.0.1:5000
t0=$(date +%s%N)
nohup /opt/venv/bin/moto_server -H 127.0.0.1 -p 5000 > /root/results/moto-server.log 2>&1 &
mpid=$!
echo "moto_version: $(/opt/venv/bin/python -c 'import moto; print(moto.__version__)')" >> "$OUT"
echo "moto_ready_ms: $(wait_ready http://127.0.0.1:5000/moto-api/ "$t0")" >> "$OUT"
echo "moto_rss_kib_idle: $(awk '/VmRSS/{print $2}' /proc/$mpid/status)" >> "$OUT"
timeout 600 /opt/venv/bin/python /root/scripts/scenarios.py moto | tee /root/results/moto-scenarios.txt
timeout 300 /opt/venv/bin/python /root/scripts/iam_gap.py moto > /root/results/moto-iam-gap.txt 2>&1; tail -1 /root/results/moto-iam-gap.txt
echo "moto_rss_kib_after: $(awk '/VmRSS/{print $2}' /proc/$mpid/status)" >> "$OUT"
kill $mpid; wait $mpid 2>/dev/null

# ---- Floci
use_http_env http://127.0.0.1:4566
docker pull -q floci/floci:2.4.0 > /dev/null 2>&1 || docker pull -q floci/floci:latest > /dev/null
IMG=$(docker image ls --format '{{.Repository}}:{{.Tag}}' floci/floci | head -1)
echo "floci_image: $IMG $(docker image inspect "$IMG" --format '{{index .RepoDigests 0}} size={{.Size}}')" >> "$OUT"
t0=$(date +%s%N)
docker run -d --rm --name floci -p 127.0.0.1:4566:4566 -v /var/run/docker.sock:/var/run/docker.sock "$IMG" > /dev/null
echo "floci_ready_ms: $(wait_ready http://127.0.0.1:4566/ "$t0")" >> "$OUT"
sleep 2
echo "floci_mem_idle: $(docker stats --no-stream --format '{{.MemUsage}}' floci)" >> "$OUT"
timeout 600 /opt/venv/bin/python /root/scripts/scenarios.py floci | tee /root/results/floci-scenarios.txt
timeout 300 /opt/venv/bin/python /root/scripts/iam_gap.py floci > /root/results/floci-iam-gap.txt 2>&1; tail -1 /root/results/floci-iam-gap.txt
echo "floci_mem_after: $(docker stats --no-stream --format '{{.MemUsage}}' floci)" >> "$OUT"
docker logs floci > /root/results/floci-server.log 2>&1
docker stop floci > /dev/null
cat "$OUT"
