#!/usr/bin/env bash
# Two saved probes:
#  1. Caps: fill one bucket with 100 KiB objects and one queue with 1 KiB messages until the emulator refuses.
#  2. S3 Control host: can a hosts-file entry make 000000000000.127.0.0.1 reach the emulator?
set -uo pipefail
source /root/scripts/env-aws-emulator.sh
bash /root/scripts/start-aws-emulator.sh > /dev/null
/opt/venv/bin/python - <<'EOF' | tee /root/results/caps-probe.json
import json, os, boto3
from botocore.exceptions import ClientError
s3, sqs = boto3.client("s3"), boto3.client("sqs")
out = {}
s3.create_bucket(Bucket="cap-probe")
blob = os.urandom(100 * 1024)
for i in range(2000):
    try:
        s3.put_object(Bucket="cap-probe", Key=f"obj-{i:05d}", Body=blob)
    except ClientError as e:
        out["s3"] = {"stored_objects": i, "error": f"{e.response['Error']['Code']}: {e.response['Error']['Message']}"}
        break
q = sqs.create_queue(QueueName="cap-probe")["QueueUrl"]
for i in range(2000):
    try:
        sqs.send_message(QueueUrl=q, MessageBody="m" * 1024)
    except ClientError as e:
        out["sqs"] = {"stored_messages": i, "error": f"{e.response['Error']['Code']}: {e.response['Error']['Message']}"}
        break
print(json.dumps(out, indent=2))
EOF

OUT=/root/results/s3control-host-probe.txt
{
  echo "\$ echo '127.0.0.1 000000000000.127.0.0.1' >> /etc/hosts"
  echo "127.0.0.1 000000000000.127.0.0.1" >> /etc/hosts
  echo "\$ getent hosts 000000000000.127.0.0.1"
  getent hosts 000000000000.127.0.0.1; echo "[exit $?]"
  echo "\$ curl -sk https://000000000000.127.0.0.1:9297/__admin/health"
  curl -sk -o /dev/null https://000000000000.127.0.0.1:9297/__admin/health; echo "[exit $?]"
  echo "\$ curl -sk --resolve 000000000000.127.0.0.1:9297:127.0.0.1 https://000000000000.127.0.0.1:9297/__admin/health"
  curl -sk -o /dev/null -w '%{http_code}' --resolve 000000000000.127.0.0.1:9297:127.0.0.1 https://000000000000.127.0.0.1:9297/__admin/health; echo " [exit $?]"
  echo "--- certificate subjectAltName"
  openssl x509 -in "$AWS_CA_BUNDLE" -noout -ext subjectAltName
} > "$OUT" 2>&1
sed -i '/000000000000.127.0.0.1/d' /etc/hosts
cat "$OUT"
pkill -TERM -x aws-emulator
