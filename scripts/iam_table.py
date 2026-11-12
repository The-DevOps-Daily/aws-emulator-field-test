"""Print the IAM comparison as a Markdown table (bold = differs from what AWS documents).
Usage: python3 scripts/iam_table.py   (run from the repo root)
"""
import json

e = json.load(open("results/aws-emulator-iam-gap.json"))["results"]
m = {c["case"]: c for c in json.load(open("results/moto-iam-gap.json"))["results"]}
f = {c["case"]: c for c in json.load(open("results/floci-iam-gap.json"))["results"]}


def cell(c):
    return c["emulator"] if c["matches_aws"] else f"**{c['emulator']}**"


print("| Case | AWS documents | AWS Emulator | Floci | Moto |")
print("| --- | --- | --- | --- | --- |")
for c in e:
    print(f"| {c['group']}: {c['case']} | {c['aws_expected']} | {cell(c)} | {cell(f[c['case']])} | {cell(m[c['case']])} |")
print()
print("matches (emulator, floci, moto):", sum(c["matches_aws"] for c in e),
      sum(x["matches_aws"] for x in f.values()), sum(x["matches_aws"] for x in m.values()), "of", len(e))
