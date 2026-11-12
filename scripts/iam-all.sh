#!/usr/bin/env bash
# Run the same iam_gap.py against all three targets, each freshly started, default configs.
set -uo pipefail
# AWS Emulator
( source /root/scripts/env-aws-emulator.sh
  bash /root/scripts/start-aws-emulator.sh
  /opt/venv/bin/python /root/scripts/iam_gap.py aws-emulator > /root/results/aws-emulator-iam-gap.txt 2>&1
  tail -1 /root/results/aws-emulator-iam-gap.txt
  pkill -TERM -x aws-emulator )

http_env() {
  unset AWS_CA_BUNDLE AWS_SHARED_CREDENTIALS_FILE AWS_CONFIG_FILE AWS_PROFILE AWS_SESSION_TOKEN
  export AWS_ENDPOINT_URL=$1 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
  export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true AWS_DISABLE_HOST_PREFIX_INJECTION=true
}
# Moto
( http_env http://127.0.0.1:5000
  /opt/venv/bin/moto_server -H 127.0.0.1 -p 5000 > /root/results/moto-server-iam.log 2>&1 & pid=$!
  until curl -s -o /dev/null http://127.0.0.1:5000/moto-api/; do sleep 0.2; done
  /opt/venv/bin/python /root/scripts/iam_gap.py moto > /root/results/moto-iam-gap.txt 2>&1
  tail -1 /root/results/moto-iam-gap.txt
  kill $pid )
# Floci
( http_env http://127.0.0.1:4566
  docker run -d --rm --name floci-iam -p 127.0.0.1:4566:4566 floci/floci:2.4.0 > /dev/null
  until curl -s -o /dev/null http://127.0.0.1:4566/; do sleep 0.2; done
  /opt/venv/bin/python /root/scripts/iam_gap.py floci > /root/results/floci-iam-gap.txt 2>&1
  tail -1 /root/results/floci-iam-gap.txt
  docker stop floci-iam > /dev/null )
