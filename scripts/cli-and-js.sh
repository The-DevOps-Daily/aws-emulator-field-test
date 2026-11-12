#!/usr/bin/env bash
# AWS CLI and JavaScript SDK v3 smoke run against the emulator. Writes results/cli-session.txt and results/js-sdk.txt.
set -uo pipefail
source /root/scripts/env-aws-emulator.sh
OUT=/root/results/cli-session.txt
run() { echo "\$ $*" >> "$OUT"; "$@" >> "$OUT" 2>&1; echo "[exit $?]" >> "$OUT"; }
: > "$OUT"
run aws --version
run aws sts get-caller-identity --output text
run aws s3 mb s3://cli-demo
echo "release notes" > /tmp/notes.txt
run aws s3 cp /tmp/notes.txt s3://cli-demo/notes.txt
run aws s3 ls s3://cli-demo/
URL=$(aws s3 presign s3://cli-demo/notes.txt --expires-in 60)
echo "\$ curl --cacert \$AWS_CA_BUNDLE \"\$(aws s3 presign s3://cli-demo/notes.txt --expires-in 60)\"" >> "$OUT"
curl -s --cacert "$AWS_CA_BUNDLE" "$URL" >> "$OUT"; echo "[exit $?]" >> "$OUT"
run aws sqs create-queue --queue-name cli-demo --query QueueUrl --output text
run aws dynamodb create-table --table-name ab --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH --billing-mode PAY_PER_REQUEST
run aws ec2 describe-regions --query 'length(Regions)'
run aws s3control list-access-points --account-id 000000000000
cat "$OUT"

mkdir -p /root/js && cd /root/js
[ -d node_modules/@aws-sdk/client-s3 ] || npm init -y >/dev/null && npm install --silent @aws-sdk/client-s3 @aws-sdk/client-dynamodb @aws-sdk/client-sqs > /dev/null 2>&1
cp /root/scripts/js-smoke.mjs /root/js/
node /root/js/js-smoke.mjs 2>&1 | tee /root/results/js-sdk.txt
