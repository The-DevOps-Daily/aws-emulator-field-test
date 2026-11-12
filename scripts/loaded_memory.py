"""Memory under a realistic test-suite load: fill S3, DynamoDB and SQS, then delete it all.
Reads RSS of the emulator process from /proc (same machine). Sequential single client:
this measures memory growth and per-call latency, not maximum throughput.

Usage: python loaded_memory.py <pid>   (writes results/loaded-memory.json)
"""
import json
import os
import statistics
import sys
import time

import boto3
from botocore.config import Config

PID = int(sys.argv[1])
OUT = os.path.join(os.environ.get("RESULTS_DIR", "/root/results"), "loaded-memory.json")
CFG = Config(retries={"max_attempts": 1}, max_pool_connections=4)
s3, ddb, sqs = (boto3.client(n, config=CFG) for n in ("s3", "dynamodb", "sqs"))


def mem():
    vals = {}
    with open(f"/proc/{PID}/status") as f:
        for line in f:
            k, _, v = line.partition(":")
            if k in ("VmRSS", "RssAnon", "RssFile", "VmHWM"):
                vals[k] = int(v.split()[0])
    return {k: round(v / 1024, 1) for k, v in vals.items()}  # MiB


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 2)


steps = [{"step": "baseline", "mem_mib": mem()}]


def timed(label, n_calls, fn, payload_mib):
    lat = []
    t0 = time.perf_counter()
    for i in range(n_calls):
        t = time.perf_counter()
        fn(i)
        lat.append((time.perf_counter() - t) * 1000)
    wall = time.perf_counter() - t0
    steps.append({"step": label, "calls": n_calls, "payload_mib": payload_mib, "wall_s": round(wall, 1),
                  "call_ms_p50": pct(lat, 0.5), "call_ms_p95": pct(lat, 0.95), "mem_mib": mem()})
    print(json.dumps(steps[-1]), flush=True)


# S3: 600 objects of 100 KiB (a first run with 1,000 hit the emulator's per-bucket cap at object 655)
s3.create_bucket(Bucket="load-test")
blob = os.urandom(100 * 1024)
timed("s3: 600 x 100 KiB PutObject", 600,
      lambda i: s3.put_object(Bucket="load-test", Key=f"obj-{i:05d}", Body=blob), round(600 * 100 / 1024, 1))
try:
    s3.put_object(Bucket="load-test", Key="big-10mib", Body=os.urandom(10 * 2**20))
    steps.append({"step": "s3 cap probe: +10 MiB object", "result": "stored"})
except Exception as e:
    steps.append({"step": "s3 cap probe: +10 MiB object", "result": str(e)})
print(json.dumps(steps[-1]), flush=True)

# DynamoDB: 20,000 items of about 1 KiB, 25 per BatchWriteItem
ddb.create_table(TableName="load-test", BillingMode="PAY_PER_REQUEST",
                 AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                 KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}])
ddb.get_waiter("table_exists").wait(TableName="load-test", WaiterConfig={"Delay": 1, "MaxAttempts": 30})
pad = "x" * 1000


def ddb_batch(i):
    items = [{"PutRequest": {"Item": {"id": {"S": f"item-{i * 25 + j:06d}"}, "pad": {"S": pad}}}} for j in range(25)]
    r = ddb.batch_write_item(RequestItems={"load-test": items})
    assert not r.get("UnprocessedItems"), "unprocessed items"


timed("dynamodb: 20000 x 1 KiB items (800 BatchWriteItem)", 800, ddb_batch, round(20000 / 1024, 1))
count = ddb.scan(TableName="load-test", Select="COUNT")
steps[-1]["first_scan_page_count"] = count["Count"]

# SQS: 10,000 messages of 1 KiB, 10 per SendMessageBatch
qurl = sqs.create_queue(QueueName="load-test")["QueueUrl"]
body = "m" * 1024


def sqs_batch(i):
    r = sqs.send_message_batch(QueueUrl=qurl, Entries=[{"Id": str(j), "MessageBody": body} for j in range(10)])
    assert not r.get("Failed"), r.get("Failed")


# A first run with 10,000 messages stopped at the emulator's 1,000-message queue cap.
timed("sqs: 900 x 1 KiB messages (90 SendMessageBatch)", 90, sqs_batch, round(900 / 1024, 2))
try:
    for _ in range(11):
        sqs_batch(0)
    steps.append({"step": "sqs cap probe: +110 messages", "result": "stored"})
except Exception as e:
    steps.append({"step": "sqs cap probe: +110 messages", "result": str(e)})
print(json.dumps(steps[-1]), flush=True)
steps[-2]["approx_messages"] = sqs.get_queue_attributes(
    QueueUrl=qurl, AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]["ApproximateNumberOfMessages"]

# Delete everything and see whether memory comes back
t0 = time.perf_counter()
s3.delete_objects(Bucket="load-test", Delete={"Objects": [{"Key": f"obj-{i:05d}"} for i in range(600)] + [{"Key": "big-10mib"}]})
s3.delete_bucket(Bucket="load-test")
ddb.delete_table(TableName="load-test")
sqs.purge_queue(QueueUrl=qurl)
sqs.delete_queue(QueueUrl=qurl)
time.sleep(15)
steps.append({"step": "after deleting everything + 15 s", "wall_s": round(time.perf_counter() - t0, 1), "mem_mib": mem()})
print(json.dumps(steps[-1]), flush=True)

with open(OUT, "w") as f:
    json.dump({"pid": PID, "steps": steps}, f, indent=2)
print(f"-> {OUT}")
