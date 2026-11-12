# aws-emulator-field-test

A hands-on test of [AWS Emulator](https://github.com/aws/aws-emulator) v1.0.322 (released 2026-11-11) on a
throwaway Ubuntu 24.04 machine: install size, start time, memory, 40 workflow checks across 9 services,
32 permission cases, the AWS CLI, the JavaScript SDK v3, and Terraform with the AWS provider 6.x and 5.x.
The same scenario suite and permission matrix also ran against Moto and Floci in their default setups.

Write-up: https://devops-daily.com/posts/aws-emulator-hands-on-test

Nothing here touches a real AWS account. Every request goes to a local emulator. One JavaScript SDK
default does send an SQS request to AWS, and `scripts/js-sqs-endpoint.mjs` exists to prove and fix that.

## Rerun it

You need a disposable Linux x86_64 machine (we used a 4 vCPU / 8 GB droplet). Do not use a machine
that holds real AWS credentials. Run as root, from the repo checkout copied to `/root`:

```bash
mkdir -p /root/results
cp -r scripts terraform /root/

bash /root/scripts/00-setup-host.sh                 # AWS CLI v2, Terraform, Node 22, boto3, Moto, Docker
bash /root/scripts/01-install.sh                    # download, verify signature, install the .deb; sizes
bash /root/scripts/02-cold-start-and-idle-memory.sh # 10 starts to first healthy response; idle RSS
bash /root/scripts/03-cold-cache-and-cleanup.sh     # 5 starts with the page cache dropped; RSS split; cleanup

source /root/scripts/env-aws-emulator.sh            # env vars from the "Pointing your AWS SDK" guide
bash /root/scripts/start-aws-emulator.sh            # fresh emulator, telemetry off
/opt/venv/bin/python /root/scripts/scenarios.py aws-emulator
FT_NO_ROLE_POLICIES=1 /opt/venv/bin/python /root/scripts/scenarios.py variant-roles-without-policies-aws-emulator

bash /root/scripts/start-aws-emulator.sh
/opt/venv/bin/python /root/scripts/iam_gap.py aws-emulator

bash /root/scripts/start-aws-emulator.sh
/opt/venv/bin/python /root/scripts/loaded_memory.py "$(cat /root/emulator.pid)"

bash /root/scripts/start-aws-emulator.sh && bash /root/scripts/run-terraform.sh     # provider 6.x
bash /root/scripts/start-aws-emulator.sh && bash /root/scripts/run-terraform-v5.sh  # provider 5.x
bash /root/scripts/start-aws-emulator.sh && bash /root/scripts/cli-and-js.sh
node /root/js/js-sqs-endpoint.mjs                   # after cli-and-js.sh installed the SDK

bash /root/scripts/compare.sh                       # Moto server + Floci, same suites, default configs

git clone https://github.com/aws/aws-emulator.git /root/src/aws-emulator
git clone https://github.com/aws/aws-emulator-evaluation-suite.git /root/src/aws-emulator-evaluation-suite
bash /root/scripts/repo_stats.sh
curl -fsSLo /root/results/scores-v1.0.322.json.gz \
  https://github.com/aws/aws-emulator/releases/download/v1.0.322/scores.json.gz
python3 -I /root/scripts/scores_summary.py /root/results/scores-v1.0.322.json.gz /root/results/scores-summary.json

# back on your laptop, with results/ copied next to scripts/
python3 scripts/post_numbers.py                     # every number the post quotes -> results/post-numbers.json
```

Each emulator start is fresh, so every suite runs against empty state.

## What each script does

| Script | What it measures |
| --- | --- |
| `00-setup-host.sh` | Installs the clients. Writes `results/host-versions.txt`. |
| `01-install.sh` | `.deb` size, GPG signature check, installed size, binary type. |
| `02-cold-start-and-idle-memory.sh` | Time from exec to HTTP 200 on `/__admin/health`, 10 runs; RSS over 60 s idle. |
| `03-cold-cache-and-cleanup.sh` | The same with the page cache dropped; anonymous vs file-backed RSS; what a SIGTERM leaves behind. |
| `scenarios.py` | 40 checks in 9 workflows: S3, SQS, DynamoDB, Lambda, EventBridge, SNS, KMS, Secrets Manager, Step Functions. Every failure keeps the exact error. |
| `iam_gap.py` | 32 permission cases. `aws_expected` comes from AWS documentation; these cases were not run on real AWS. |
| `loaded_memory.py` | RSS while filling S3, DynamoDB and SQS, and after deleting it all. Also probes the per-bucket and per-queue caps. |
| `run-terraform.sh`, `run-terraform-v5.sh` | init, apply, a second plan (drift check) and destroy for `terraform/main.tf`. |
| `cli-and-js.sh`, `js-smoke.mjs`, `js-sqs-endpoint.mjs` | AWS CLI session, JS SDK v3 smoke test, and the SQS queue-URL endpoint check (with and without a hosts-file block). |
| `compare.sh` | Moto 5.2.5 server and Floci 2.4.0, default configs, same two suites. |
| `repo_stats.sh`, `scores_summary.py` | Commit authors, tree size, issue files, fidelity test counts; the release's own fidelity scores. |
| `post_numbers.py` | Derives the numbers the post quotes, stamped with a hash of `results/`. |

## Kept on purpose

- `results/discarded-run1-aws-emulator-scenarios.json`: the first scenario run. Our Lambda handler probed
  the host on every call, so the sandbox refused it and the echo check failed. That was our test's fault.
- `results/variant-toplevel-import-subprocess-aws-emulator-scenarios.json`: a handler with
  `import subprocess` at module level fails to load (`No module named '_signal'`).
- `results/variant-roles-without-policies-aws-emulator-scenarios.json`: the run where our Lambda and Step
  Functions roles had no permissions. The emulator refused both calls, as AWS would.

## License

MIT
