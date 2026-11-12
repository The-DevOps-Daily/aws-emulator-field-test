#!/usr/bin/env bash
# init, apply, plan again (drift check), destroy. Timings and exit codes go to results/terraform-v5-summary.txt.
set -uo pipefail
source /root/scripts/env-aws-emulator.sh
# Same stack as terraform/main.tf, with the AWS provider pinned to 5.x
mkdir -p /root/terraform-v5
sed 's/version = "~> 6.0"/version = "~> 5.0"/' /root/terraform/main.tf > /root/terraform-v5/main.tf
cd /root/terraform-v5
rm -rf .terraform* terraform.tfstate* fn.zip
OUT=/root/results/terraform-v5-summary.txt
: > "$OUT"
step() {
  local name=$1; shift
  local t0=$(date +%s.%N)
  "$@" > "/root/results/terraform-v5-$name.log" 2>&1
  local code=$?
  local t1=$(date +%s.%N)
  echo "$name: exit=$code seconds=$(echo "$t1 - $t0" | bc)" | tee -a "$OUT"
  return $code
}
step init terraform init -input=false -no-color
step apply terraform apply -auto-approve -input=false -no-color
echo "resources_in_state: $(terraform state list 2>/dev/null | wc -l)" | tee -a "$OUT"
# exit 0 = no changes, 2 = changes pending (drift or a value the emulator returns differently)
step plan-after-apply terraform plan -detailed-exitcode -input=false -no-color
grep -E '^\s+# .* (will|must) be' /root/results/terraform-v5-plan-after-apply.log | tee -a "$OUT"
step destroy terraform destroy -auto-approve -input=false -no-color
terraform version | head -1 >> "$OUT"
grep -E 'Installed hashicorp' /root/results/terraform-v5-init.log >> "$OUT"
