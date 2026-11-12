"""Derive every number the post quotes from the raw result files, into results/post-numbers.json.
Usage: python3 scripts/post_numbers.py   (run from the repo root, after pulling results/)
"""
import csv
import hashlib
import json
import pathlib
import statistics

R = pathlib.Path("results")


def kv(path):
    out = {}
    for line in (R / path).read_text().splitlines():
        if ": " in line and not line.startswith(("---", " ")):
            k, v = line.split(": ", 1)
            out.setdefault(k.strip(), v.strip())
    return out


def col(path, name):
    with open(R / path) as f:
        return [int(r[name]) for r in csv.DictReader(f)]


inst = kv("install.txt")
warm = col("cold-start.csv", "ready_ms")
cold = col("cold-cache-start.csv", "ready_ms")
anon = col("cold-cache-start.csv", "rss_anon_kib")
rss = col("cold-start.csv", "rss_kib_at_ready")
load = json.loads((R / "loaded-memory.json").read_text())
scen = json.loads((R / "aws-emulator-scenarios.json").read_text())
iam = json.loads((R / "aws-emulator-iam-gap.json").read_text())
scores = json.loads((R / "scores-summary.json").read_text())

numbers = {
    "emulator_version": inst["version"],
    "deb_mib": round(int(inst["deb_bytes"]) / 2**20, 1),
    "binary_bytes": int((R / "install.txt").read_text().split("--- largest files")[1].split()[0]),
    "installed_mib": round(int(inst["installed_bytes_opt"]) / 2**20, 1),
    "start_warm_cache_ms": {"runs": len(warm), "min": min(warm), "median": statistics.median(warm), "max": max(warm)},
    "start_cold_cache_ms": {"runs": len(cold), "min": min(cold), "median": statistics.median(cold), "max": max(cold)},
    "rss_at_ready_mib_median": round(statistics.median(rss) / 1024, 1),
    "rss_anon_at_ready_mib_median": round(statistics.median(anon) / 1024, 1),
    "load_steps": [{k: s.get(k) for k in ("step", "calls", "payload_mib", "call_ms_p50", "call_ms_p95", "mem_mib", "result")}
                   for s in load["steps"]],
    "scenarios": {"passed": scen["checks_passed"], "total": scen["checks_total"], "seconds": scen["seconds"],
                  "per_scenario": [(r["scenario"], r["status"], r["passed"], r["total"]) for r in scen["results"]],
                  "lambda_invoke_ms": scen["lambda_invoke_ms"], "lambda_probe": scen["lambda_probe"]},
    "iam": {"matches": iam["matches_aws"], "cases": iam["cases"], "gaps": iam["gaps"]},
    "caps_probe": json.loads((R / "caps-probe.json").read_text()),
    "release_scores": {k: scores[k] for k in ("units", "by_outcome", "pass_rate", "services_scored")},
    "release_scores_our_services": {k.split("#")[0].replace("com.amazonaws.", ""): v["pass_rate"]
                                    for k, v in scores["our_services"].items()},
}
for target in ("moto", "floci"):
    for kind in ("scenarios", "iam-gap"):
        p = R / f"{target}-{kind}.json"
        if p.exists():
            d = json.loads(p.read_text())
            if kind == "scenarios":
                numbers[f"{target}_scenarios"] = {"passed": d["checks_passed"], "total": d["checks_total"],
                                                  "per_scenario": [(r["scenario"], r["status"], r.get("passed"), r.get("total"))
                                                                   for r in d["results"]]}
            else:
                numbers[f"{target}_iam"] = {"matches": d["matches_aws"], "cases": d["cases"], "gaps": d["gaps"]}

# Stamp: hash of every raw result file the numbers came from
h = hashlib.sha256()
for p in sorted(R.glob("*")):
    if p.is_file() and p.name != "post-numbers.json":
        h.update(p.name.encode())
        h.update(p.read_bytes())
numbers["results_sha256"] = h.hexdigest()
(R / "post-numbers.json").write_text(json.dumps(numbers, indent=2) + "\n")
print(json.dumps(numbers, indent=2))
