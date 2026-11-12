#!/usr/bin/env bash
# What the aws/aws-emulator and aws-emulator-evaluation-suite repositories actually contain.
# Expects clones in /root/src. Writes results/repo-stats.txt.
set -uo pipefail
OUT=/root/results/repo-stats.txt
E=/root/src/aws-emulator
S=/root/src/aws-emulator-evaluation-suite
{
echo "== aws-emulator @ $(git -C $E rev-parse --short HEAD) ($(git -C $E log -1 --format=%cI))"
echo "commits: $(git -C $E rev-list --count HEAD)"
echo "first_commit: $(git -C $E log --reverse --format='%cI %an: %s' | head -1)"
echo "-- commits by author"
git -C $E log --format='%an <%ae>' | sort | uniq -c | sort -rn
echo "-- commits by committer"
git -C $E log --format='%cn' | sort | uniq -c | sort -rn
echo "-- message prefix (text before the first colon)"
git -C $E log --format='%s' | sed -E 's/:.*//' | sort | uniq -c | sort -rn | head -12
echo "-- Producer trailer"
git -C $E log --format='%b' | grep -oE '^Producer: .*' | sort | uniq -c | sort -rn
echo "-- busiest days"
git -C $E log --format='%cd' --date=short | sort | uniq -c | sort -rn | head -5
echo "-- commits per week"
git -C $E log --format='%cd' --date=format:'%G-W%V' | sort | uniq -c
echo "-- tree"
echo "service_crates: $(ls -d $E/services/service-* | wc -l)"
echo "smithy_models: $(find $E/services -path '*smithy-model*' -name '*.json' | wc -l)"
echo "rs_files_excl_vendor: $(find $E -name '*.rs' -not -path "$E/vendor/*" -not -path '*/.git/*' | wc -l)"
echo "rs_lines_excl_vendor: $(find $E -name '*.rs' -not -path "$E/vendor/*" -not -path '*/.git/*' -print0 | xargs -0 cat | wc -l)"
echo "rs_lines_vendor: $(find $E/vendor -name '*.rs' -print0 | xargs -0 cat | wc -l)"
echo "op_files (services/*/src/ops/*.rs): $(find $E/services -path '*/src/ops/*.rs' | wc -l)"
echo "service_test_files (services/*/tests/*): $(find $E/services -path '*/tests/*' -type f | wc -l)"
echo "issues_do_not_work: $(ls $E/issues/things-that-do-not-work | wc -l)"
echo "issues_now_work: $(ls $E/issues/things-that-now-work | wc -l)"
echo "-- top-level hidden dirs"
ls -la $E/.emulator $E/.github 2>&1 | head -30
find $E/.github -type f | head -20
echo "-- largest service crates by .rs lines"
for d in $E/services/service-*; do echo "$(find $d -name '*.rs' -print0 | xargs -0 cat 2>/dev/null | wc -l) $(basename $d)"; done | sort -rn | head -8
echo "-- open issue files mentioning IAM gaps we hit"
grep -ril "permissions boundar" $E/issues/things-that-do-not-work | wc -l | sed 's/^/permissions boundary: /'
grep -ril "session polic" $E/issues/things-that-do-not-work | wc -l | sed 's/^/session policy: /'
grep -ril "ListAllMyBuckets\|ListBuckets" $E/issues/things-that-do-not-work | wc -l | sed 's/^/ListBuckets: /'
grep -ril "terraform" $E/issues/things-that-do-not-work | wc -l | sed 's/^/terraform: /'
grep -ril "s3control\|s3 control" $E/issues/things-that-do-not-work | wc -l | sed 's/^/s3control: /'
echo "-- emulator caps found in source (messages)"
grep -rhoE "this emulator (keeps|holds) [^\"]{0,80}" $E/services $E/core 2>/dev/null | sort | uniq -c | sort -rn | head -20
grep -rn "67108864\|64 \* 1024 \* 1024\|MAX_BUCKET_BYTES\|1000 messages\|MAX_QUEUE_MESSAGES" $E/services/service-s3*/src $E/services/service-sqs/src 2>/dev/null | head -10
echo
echo "== aws-emulator-evaluation-suite @ $(git -C $S rev-parse --short HEAD)"
echo "commits: $(git -C $S rev-list --count HEAD)"
git -C $S log --format='%an' | sort | uniq -c | sort -rn
echo "fidelity_tests_manifest_rows: $(wc -l < $S/manifest.jsonl)"
echo "check_files: $(find $S/behavioral -name 'check_*.py' | wc -l)"
echo "services_with_tests: $(ls -d $S/behavioral/*/ | wc -l)"
echo "cross_service_dirs: $(ls -d $S/behavioral/*__*/ 2>/dev/null | wc -l)"
echo "-- tests per service (top 10)"
for d in $S/behavioral/*/; do echo "$(find $d -maxdepth 1 -name 'check_*.py' | wc -l) $(basename $d)"; done | sort -rn | head -10
echo "-- tests for services in our scenario set"
for s in s3 sqs dynamodb lambda events sns kms secretsmanager stepfunctions iam sts; do
  echo "$s $(find $S/behavioral/$s -maxdepth 1 -name 'check_*.py' 2>/dev/null | wc -l)"; done
} > "$OUT" 2>&1
cat "$OUT"
