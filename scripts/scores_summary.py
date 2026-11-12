"""Summarise the fidelity scores AWS publishes with a release (scores.json.gz on the releases page).
Usage: python3 scores_summary.py results/scores-v1.0.322.json.gz  (writes results/scores-summary.json)
"""
import collections
import gzip
import json
import sys

data = json.load(gzip.open(sys.argv[1]))
units = data["units"]
by_outcome = collections.Counter(u["outcome"] for u in units)
by_kind = collections.Counter((u.get("kind"), u["outcome"]) for u in units)
per_service = collections.defaultdict(collections.Counter)
for u in units:
    for s in u.get("services", []):
        per_service[s][u["outcome"]] += 1

ours = ["sqs", "s3#AmazonS3", "dynamodb", "lambda", "eventbridge", "sns", "kms", "secretsmanager", "states", "iam", "sts"]
picked = {}
for name, c in per_service.items():
    short = name.split("#")[0].replace("com.amazonaws.", "")
    if any(short == o.split("#")[0] for o in ours):
        total = sum(c.values())
        picked[name] = {"units": total, **c, "pass_rate": round(c.get("pass", 0) / total, 3)}

overall_total = sum(by_outcome.values())
summary = {
    "commit": data.get("commit"), "scoredAt": data.get("scoredAt"),
    "units": overall_total, "by_outcome": dict(by_outcome),
    "pass_rate": round(by_outcome.get("pass", 0) / overall_total, 3),
    "by_kind": {f"{k}:{o}": n for (k, o), n in sorted(by_kind.items(), key=lambda x: str(x))},
    "services_scored": len(per_service),
    "our_services": dict(sorted(picked.items())),
    "top_level_keys": list(data.keys()),
}
with open(sys.argv[2] if len(sys.argv) > 2 else "/root/results/scores-summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print(json.dumps(summary, indent=2))
