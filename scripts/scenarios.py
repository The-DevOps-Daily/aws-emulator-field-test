"""Scenario suite: realistic multi-step workflows, run with boto3 against whatever endpoint
AWS_ENDPOINT_URL points at. Every sub-check records ok/fail and the exact error text.

Usage: python scenarios.py <target-name>   (writes results/<target-name>-scenarios.json)
"""
import io
import json
import os
import socket
import ssl
import sys
import time
import urllib.request
import uuid
import zipfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

TARGET = sys.argv[1] if len(sys.argv) > 1 else "aws-emulator"
OUT = os.path.join(os.environ.get("RESULTS_DIR", "/root/results"), f"{TARGET}-scenarios.json")
RUN = uuid.uuid4().hex[:8]
# One attempt only: retries would hide errors and blur timings.
# Set FT_NO_ROLE_POLICIES=1 to create the Lambda and Step Functions roles with no permissions.
ROLE_POLICIES = os.environ.get("FT_NO_ROLE_POLICIES") != "1"
CFG = Config(retries={"max_attempts": 1, "mode": "standard"}, connect_timeout=5, read_timeout=60)


def client(name):
    return boto3.client(name, config=CFG)


def err(e):
    if isinstance(e, ClientError):
        return f"{e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}"
    return f"{type(e).__name__}: {e}"


class Scenario:
    def __init__(self, name):
        self.name, self.checks, self.t0 = name, [], time.perf_counter()

    def step(self, label, fn):
        t = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as e:  # record, never hide
            ok, detail = False, err(e)
        self.checks.append({"check": label, "ok": bool(ok), "detail": str(detail)[:500],
                            "ms": round((time.perf_counter() - t) * 1000, 1)})
        return ok

    def result(self):
        n_ok = sum(c["ok"] for c in self.checks)
        status = "pass" if n_ok == len(self.checks) else ("fail" if n_ok == 0 else "partial")
        return {"scenario": self.name, "status": status, "passed": n_ok, "total": len(self.checks),
                "seconds": round(time.perf_counter() - self.t0, 2), "checks": self.checks}


def ssl_ctx():
    ca = os.environ.get("AWS_CA_BUNDLE")
    return ssl.create_default_context(cafile=ca) if ca else None


def http(method, url, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, context=ssl_ctx(), timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def expect_error(code, fn):
    try:
        fn()
        return False, f"no error, expected {code}"
    except ClientError as e:
        got = e.response["Error"].get("Code")
        return got == code, f"{got}: {e.response['Error'].get('Message')}"


# ---------------------------------------------------------------- S3
def s3_scenario():
    s = Scenario("S3: objects, presigned URLs, multipart, versioning")
    s3 = client("s3")
    b = f"fieldtest-{RUN}"
    st = {}
    s.step("create bucket", lambda: (s3.create_bucket(Bucket=b) is not None, b))

    def roundtrip():
        s3.put_object(Bucket=b, Key="a.txt", Body=b"hello")
        body = s3.get_object(Bucket=b, Key="a.txt")["Body"].read()
        return body == b"hello", body
    s.step("put/get round trip", roundtrip)

    def presigned_get():
        url = s3.generate_presigned_url("get_object", Params={"Bucket": b, "Key": "a.txt"}, ExpiresIn=60)
        code, body = http("GET", url)
        return code == 200 and body == b"hello", f"HTTP {code} {body[:60]!r}"
    s.step("presigned GET", presigned_get)

    def presigned_expired():
        url = s3.generate_presigned_url("get_object", Params={"Bucket": b, "Key": "a.txt"}, ExpiresIn=1)
        time.sleep(2.5)
        code, body = http("GET", url)
        return code == 403, f"HTTP {code} {body[:160]!r}"
    s.step("expired presigned GET is refused (AWS: 403)", presigned_expired)

    def presigned_put():
        url = s3.generate_presigned_url("put_object", Params={"Bucket": b, "Key": "up.txt"}, ExpiresIn=60)
        code, _ = http("PUT", url, data=b"uploaded")
        body = s3.get_object(Bucket=b, Key="up.txt")["Body"].read()
        return code == 200 and body == b"uploaded", f"HTTP {code}, stored {body!r}"
    s.step("presigned PUT", presigned_put)

    def multipart():
        mp = s3.create_multipart_upload(Bucket=b, Key="big.bin")
        parts = []
        for n, size in enumerate([5 * 2**20, 5 * 2**20, 2**20], start=1):
            r = s3.upload_part(Bucket=b, Key="big.bin", UploadId=mp["UploadId"], PartNumber=n, Body=os.urandom(size))
            parts.append({"PartNumber": n, "ETag": r["ETag"]})
        s3.complete_multipart_upload(Bucket=b, Key="big.bin", UploadId=mp["UploadId"], MultipartUpload={"Parts": parts})
        h = s3.head_object(Bucket=b, Key="big.bin")
        ok = h["ContentLength"] == 11 * 2**20 and h["ETag"].strip('"').endswith("-3")
        return ok, f"len={h['ContentLength']} etag={h['ETag']}"
    s.step("multipart upload, 3 parts", multipart)

    def multipart_too_small():
        mp = s3.create_multipart_upload(Bucket=b, Key="small.bin")
        parts = []
        for n in (1, 2):
            r = s3.upload_part(Bucket=b, Key="small.bin", UploadId=mp["UploadId"], PartNumber=n, Body=os.urandom(2**20))
            parts.append({"PartNumber": n, "ETag": r["ETag"]})
        return expect_error("EntityTooSmall", lambda: s3.complete_multipart_upload(
            Bucket=b, Key="small.bin", UploadId=mp["UploadId"], MultipartUpload={"Parts": parts}))
    s.step("1 MiB first part is refused (AWS: EntityTooSmall)", multipart_too_small)

    def versioning():
        s3.put_bucket_versioning(Bucket=b, VersioningConfiguration={"Status": "Enabled"})
        v1 = s3.put_object(Bucket=b, Key="v.txt", Body=b"one")["VersionId"]
        s3.put_object(Bucket=b, Key="v.txt", Body=b"two")
        vs = s3.list_object_versions(Bucket=b, Prefix="v.txt").get("Versions", [])
        old = s3.get_object(Bucket=b, Key="v.txt", VersionId=v1)["Body"].read()
        st["v1"] = v1
        return len(vs) == 2 and old == b"one", f"versions={len(vs)} old={old!r}"
    s.step("versioning keeps old versions", versioning)

    def delete_marker():
        s3.delete_object(Bucket=b, Key="v.txt")
        ok1, d1 = expect_error("NoSuchKey", lambda: s3.get_object(Bucket=b, Key="v.txt"))
        old = s3.get_object(Bucket=b, Key="v.txt", VersionId=st["v1"])["Body"].read()
        return ok1 and old == b"one", f"{d1}; v1={old!r}"
    s.step("delete creates a delete marker", delete_marker)
    return s.result()


# ---------------------------------------------------------------- SQS
def sqs_scenario():
    s = Scenario("SQS: visibility timeout, DLQ, redrive back")
    sqs = client("sqs")
    st = {}

    def setup():
        st["dlq"] = sqs.create_queue(QueueName=f"ft-dlq-{RUN}")["QueueUrl"]
        st["dlq_arn"] = sqs.get_queue_attributes(QueueUrl=st["dlq"], AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        st["q"] = sqs.create_queue(QueueName=f"ft-main-{RUN}", Attributes={
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": st["dlq_arn"], "maxReceiveCount": "2"})})["QueueUrl"]
        st["q_arn"] = sqs.get_queue_attributes(QueueUrl=st["q"], AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sqs.send_message(QueueUrl=st["q"], MessageBody="job-1")
        return True, st["q"]
    s.step("queues with redrive policy", setup)

    def recv(url, vt=2):
        return sqs.receive_message(QueueUrl=url, VisibilityTimeout=vt, WaitTimeSeconds=0,
                                   MessageSystemAttributeNames=["ApproximateReceiveCount"]).get("Messages", [])

    def visibility():
        first = recv(st["q"])
        hidden = recv(st["q"])
        time.sleep(2.5)
        again = recv(st["q"])
        count = again[0]["Attributes"]["ApproximateReceiveCount"] if again else None
        ok = len(first) == 1 and len(hidden) == 0 and len(again) == 1 and count == "2"
        return ok, f"first={len(first)} while_hidden={len(hidden)} after_timeout={len(again)} receive_count={count}"
    s.step("message hides for the visibility timeout, then returns", visibility)

    def to_dlq():
        time.sleep(2.5)
        main = recv(st["q"])
        dlq = recv(st["dlq"], vt=0)
        ok = len(main) == 0 and len(dlq) == 1 and dlq[0]["Body"] == "job-1"
        return ok, f"main={len(main)} dlq={len(dlq)}"
    s.step("third receive moves it to the DLQ (maxReceiveCount=2)", to_dlq)

    def redrive_back():
        time.sleep(0.5)
        task = sqs.start_message_move_task(SourceArn=st["dlq_arn"])
        status = None
        for _ in range(40):
            tasks = sqs.list_message_move_tasks(SourceArn=st["dlq_arn"]).get("Results", [])
            status = tasks[0]["Status"] if tasks else None
            if status in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
        back = recv(st["q"])
        return status == "COMPLETED" and len(back) == 1, f"task={task.get('TaskHandle','')[:12]} status={status} back_in_main={len(back)}"
    s.step("StartMessageMoveTask redrives DLQ back to source", redrive_back)

    def fifo_dedupe():
        q = sqs.create_queue(QueueName=f"ft-{RUN}.fifo", Attributes={
            "FifoQueue": "true", "ContentBasedDeduplication": "true"})["QueueUrl"]
        for _ in range(2):
            sqs.send_message(QueueUrl=q, MessageBody="same", MessageGroupId="g1")
        msgs = sqs.receive_message(QueueUrl=q, MaxNumberOfMessages=10, WaitTimeSeconds=1).get("Messages", [])
        return len(msgs) == 1, f"received={len(msgs)} (two identical sends)"
    s.step("FIFO content-based dedupe", fifo_dedupe)
    return s.result()


# ---------------------------------------------------------------- DynamoDB
def dynamodb_scenario():
    s = Scenario("DynamoDB: conditional writes, transactions, streams")
    ddb = client("dynamodb")
    t = f"ft-orders-{RUN}"
    st = {}

    def create():
        ddb.create_table(TableName=t, BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                         StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"})
        ddb.get_waiter("table_exists").wait(TableName=t, WaiterConfig={"Delay": 1, "MaxAttempts": 30})
        st["stream"] = ddb.describe_table(TableName=t)["Table"].get("LatestStreamArn")
        return bool(st["stream"]), st["stream"]
    s.step("create table with stream", create)

    def cond_put():
        item = {"id": {"S": "o-1"}, "version": {"N": "1"}}
        ddb.put_item(TableName=t, Item=item, ConditionExpression="attribute_not_exists(id)")
        return expect_error("ConditionalCheckFailedException", lambda: ddb.put_item(
            TableName=t, Item=item, ConditionExpression="attribute_not_exists(id)"))
    s.step("second insert-if-absent fails", cond_put)

    def optimistic_lock():
        ddb.update_item(TableName=t, Key={"id": {"S": "o-1"}}, UpdateExpression="SET version = :n",
                        ConditionExpression="version = :o", ExpressionAttributeValues={":n": {"N": "2"}, ":o": {"N": "1"}})
        return expect_error("ConditionalCheckFailedException", lambda: ddb.update_item(
            TableName=t, Key={"id": {"S": "o-1"}}, UpdateExpression="SET version = :n",
            ConditionExpression="version = :o", ExpressionAttributeValues={":n": {"N": "2"}, ":o": {"N": "1"}}))
    s.step("stale optimistic-lock update fails", optimistic_lock)

    def txn_cancel():
        try:
            ddb.transact_write_items(TransactItems=[
                {"Put": {"TableName": t, "Item": {"id": {"S": "o-2"}}}},
                {"ConditionCheck": {"TableName": t, "Key": {"id": {"S": "o-1"}},
                                    "ConditionExpression": "version = :v", "ExpressionAttributeValues": {":v": {"N": "99"}}}},
            ])
            return False, "transaction committed, expected TransactionCanceledException"
        except ClientError as e:
            code = e.response["Error"]["Code"]
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            written = "Item" in ddb.get_item(TableName=t, Key={"id": {"S": "o-2"}})
            ok = code == "TransactionCanceledException" and reasons == ["None", "ConditionalCheckFailed"] and not written
            return ok, f"{code} reasons={reasons} o-2_written={written}"
    s.step("failed transaction writes nothing, reasons per item", txn_cancel)

    def txn_commit():
        ddb.transact_write_items(TransactItems=[
            {"Put": {"TableName": t, "Item": {"id": {"S": "o-3"}}}},
            {"Update": {"TableName": t, "Key": {"id": {"S": "o-1"}}, "UpdateExpression": "SET version = :n",
                        "ConditionExpression": "version = :o", "ExpressionAttributeValues": {":n": {"N": "3"}, ":o": {"N": "2"}}}},
        ])
        v = ddb.get_item(TableName=t, Key={"id": {"S": "o-1"}}, ConsistentRead=True)["Item"]["version"]["N"]
        return v == "3", f"version={v}"
    s.step("successful transaction applies both writes", txn_commit)

    def streams():
        ds = client("dynamodbstreams")
        shards = ds.describe_stream(StreamArn=st["stream"])["StreamDescription"]["Shards"]
        events = []
        for sh in shards:
            it = ds.get_shard_iterator(StreamArn=st["stream"], ShardId=sh["ShardId"],
                                       ShardIteratorType="TRIM_HORIZON")["ShardIterator"]
            for _ in range(5):
                r = ds.get_records(ShardIterator=it)
                events += [rec["eventName"] for rec in r["Records"]]
                it = r.get("NextShardIterator")
                if not it or not r["Records"]:
                    break
        # o-1 insert, update, update(txn); o-3 insert
        ok = events.count("INSERT") == 2 and events.count("MODIFY") == 2
        return ok, f"shards={len(shards)} events={events}"
    s.step("stream records INSERT and MODIFY events", streams)
    return s.result()


# ---------------------------------------------------------------- Lambda (+ IAM role)
HANDLER = '''
import json, os, socket, sys

def probe():
    # Informational: what can function code reach on the machine that runs the emulator?
    out = {"python": sys.version.split()[0], "platform": sys.platform}
    tests = {
        "read /etc/hostname": lambda: open("/etc/hostname").read().strip(),
        "os.getuid()": lambda: os.getuid(),
        "socket.gethostname()": lambda: socket.gethostname(),
        "list /": lambda: sorted(os.listdir("/"))[:8],
        "import subprocess": lambda: __import__("subprocess").__name__,
        "env AWS_REGION": lambda: os.environ.get("AWS_REGION"),
    }
    for name, fn in tests.items():
        try:
            out[name] = repr(fn())[:120]
        except Exception as e:
            out[name] = f"{type(e).__name__}: {e}"[:160]
    return out

def handler(event, context):
    if event.get("fail"):
        raise ValueError("boom")
    if event.get("probe"):
        return probe()
    if event.get("s3_bucket"):
        import boto3
        boto3.client("s3").put_object(Bucket=event["s3_bucket"], Key="from-lambda.txt", Body=b"hi")
        return {"wrote": "from-lambda.txt"}
    return {"echo": event.get("msg")}
'''


def lambda_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", HANDLER)
    return buf.getvalue()


def lambda_role(iam):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]}
    arn = iam.create_role(RoleName=f"ft-fn-{RUN}", AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
    if ROLE_POLICIES:
        iam.put_role_policy(RoleName=f"ft-fn-{RUN}", PolicyName="write-test-bucket", PolicyDocument=json.dumps({
            "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:PutObject",
                                                    "Resource": f"arn:aws:s3:::ft-lambda-{RUN}/*"}]}))
    return arn


def lambda_scenario(shared):
    s = Scenario("Lambda: create, invoke, error, cold vs warm")
    lam, iam = client("lambda"), client("iam")
    fn = f"ft-fn-{RUN}"

    def create():
        role = lambda_role(iam)
        lam.create_function(FunctionName=fn, Runtime="python3.12", Role=role, Handler="app.handler",
                            Code={"ZipFile": lambda_zip()}, Timeout=10)
        state = None
        for _ in range(60):
            state = lam.get_function_configuration(FunctionName=fn)["State"]
            if state != "Pending":
                break
            time.sleep(0.5)
        shared["fn_arn"] = lam.get_function_configuration(FunctionName=fn)["FunctionArn"]
        return state == "Active", f"state={state}"
    s.step("create function (python3.12 zip)", create)

    def invoke():
        times = []
        payload = None
        for i in range(4):
            t = time.perf_counter()
            r = lam.invoke(FunctionName=fn, Payload=json.dumps({"msg": f"hi-{i}"}))
            times.append(round((time.perf_counter() - t) * 1000, 1))
            payload = json.loads(r["Payload"].read())
        shared["lambda_ms"] = times
        shared["lambda_payload"] = payload
        return payload.get("echo") == "hi-3", f"invoke_ms={times} payload={payload}"
    s.step("invoke returns handler output (first call = cold)", invoke)

    def invoke_error():
        r = lam.invoke(FunctionName=fn, Payload=json.dumps({"fail": True}))
        body = json.loads(r["Payload"].read())
        return r.get("FunctionError") == "Unhandled" and body.get("errorType") == "ValueError", \
            f"FunctionError={r.get('FunctionError')} body={body}"
    s.step("handler exception surfaces as FunctionError", invoke_error)

    def call_s3():
        s3 = client("s3")
        b = f"ft-lambda-{RUN}"
        s3.create_bucket(Bucket=b)
        r = lam.invoke(FunctionName=fn, Payload=json.dumps({"s3_bucket": b}))
        body = json.loads(r["Payload"].read())
        try:
            stored = s3.get_object(Bucket=b, Key="from-lambda.txt")["Body"].read()
        except ClientError as e:
            stored = err(e)
        return stored == b"hi", f"FunctionError={r.get('FunctionError')} body={body} object={stored!r}"
    s.step("function calls S3 with boto3 (emulator-side SDK)", call_s3)

    r = lam.invoke(FunctionName=fn, Payload=json.dumps({"probe": True}))
    shared["lambda_probe"] = json.loads(r["Payload"].read())
    return s.result()


# ---------------------------------------------------------------- EventBridge -> SQS
def queue_policy_for(arn, service, source_arn):
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": service}, "Action": "sqs:SendMessage",
        "Resource": arn, "Condition": {"ArnEquals": {"aws:SourceArn": source_arn}}}]})


def eventbridge_scenario():
    s = Scenario("EventBridge rule to SQS target")
    ev, sqs = client("events"), client("sqs")
    st = {}

    def setup():
        st["q"] = sqs.create_queue(QueueName=f"ft-events-{RUN}")["QueueUrl"]
        q_arn = sqs.get_queue_attributes(QueueUrl=st["q"], AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        rule_arn = ev.put_rule(Name=f"ft-orders-{RUN}", EventPattern=json.dumps(
            {"source": ["app.orders"], "detail-type": ["OrderPlaced"], "detail": {"total": [{"numeric": [">", 100]}]}}))["RuleArn"]
        sqs.set_queue_attributes(QueueUrl=st["q"], Attributes={
            "Policy": queue_policy_for(q_arn, "events.amazonaws.com", rule_arn)})
        r = ev.put_targets(Rule=f"ft-orders-{RUN}", Targets=[{"Id": "q", "Arn": q_arn}])
        return r["FailedEntryCount"] == 0, rule_arn
    s.step("rule with numeric pattern + SQS target", setup)

    def deliver():
        entries = [
            {"Source": "app.orders", "DetailType": "OrderPlaced", "Detail": json.dumps({"total": 250})},
            {"Source": "app.orders", "DetailType": "OrderPlaced", "Detail": json.dumps({"total": 20})},
            {"Source": "app.billing", "DetailType": "OrderPlaced", "Detail": json.dumps({"total": 999})},
        ]
        r = ev.put_events(Entries=entries)
        got = []
        deadline = time.time() + 10
        while time.time() < deadline and len(got) < 2:
            got += sqs.receive_message(QueueUrl=st["q"], MaxNumberOfMessages=10, WaitTimeSeconds=1).get("Messages", [])
        totals = [json.loads(m["Body"])["detail"]["total"] for m in got]
        return r["FailedEntryCount"] == 0 and totals == [250], f"delivered totals={totals}"
    s.step("only the matching event is delivered", deliver)
    return s.result()


# ---------------------------------------------------------------- SNS fan-out
def sns_scenario():
    s = Scenario("SNS fan-out to two SQS queues, raw + filter policy")
    sns, sqs = client("sns"), client("sqs")
    st = {}

    def setup():
        st["topic"] = sns.create_topic(Name=f"ft-topic-{RUN}")["TopicArn"]
        for name in ("all", "only-a"):
            url = sqs.create_queue(QueueName=f"ft-{name}-{RUN}")["QueueUrl"]
            arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
            sqs.set_queue_attributes(QueueUrl=url, Attributes={"Policy": queue_policy_for(arn, "sns.amazonaws.com", st["topic"])})
            attrs = {"RawMessageDelivery": "true"} if name == "all" else {"FilterPolicy": json.dumps({"type": ["a"]})}
            sns.subscribe(TopicArn=st["topic"], Protocol="sqs", Endpoint=arn, Attributes=attrs)
            st[name] = url
        return True, st["topic"]
    s.step("topic + 2 queue subscriptions", setup)

    def fanout():
        for t in ("a", "b"):
            sns.publish(TopicArn=st["topic"], Message=f"msg-{t}",
                        MessageAttributes={"type": {"DataType": "String", "StringValue": t}})

        def drain(url, want):
            got = []
            deadline = time.time() + 8
            while time.time() < deadline and len(got) < want:
                got += sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1).get("Messages", [])
            return got
        raw = sorted(m["Body"] for m in drain(st["all"], 2))
        filt = [json.loads(m["Body"]).get("Message") for m in drain(st["only-a"], 2)]
        return raw == ["msg-a", "msg-b"] and filt == ["msg-a"], f"raw_queue={raw} filtered_queue={filt}"
    s.step("raw delivery to one queue, filter policy on the other", fanout)
    return s.result()


# ---------------------------------------------------------------- KMS
def kms_scenario():
    s = Scenario("KMS: encrypt, decrypt, context, data keys")
    kms = client("kms")
    st = {}

    def create():
        st["key"] = kms.create_key(Description="field test")["KeyMetadata"]["KeyId"]
        kms.create_alias(AliasName=f"alias/ft-{RUN}", TargetKeyId=st["key"])
        return True, st["key"]
    s.step("create key + alias", create)

    def roundtrip():
        ct = kms.encrypt(KeyId=f"alias/ft-{RUN}", Plaintext=b"secret", EncryptionContext={"app": "orders"})["CiphertextBlob"]
        st["ct"] = ct
        pt = kms.decrypt(CiphertextBlob=ct, EncryptionContext={"app": "orders"})
        return pt["Plaintext"] == b"secret" and st["key"] in pt["KeyId"], f"keyid={pt['KeyId'][-36:]}"
    s.step("encrypt/decrypt round trip via alias", roundtrip)

    s.step("wrong encryption context is refused (AWS: InvalidCiphertextException)", lambda: expect_error(
        "InvalidCiphertextException", lambda: kms.decrypt(CiphertextBlob=st["ct"], EncryptionContext={"app": "billing"})))

    def tampered():
        ct = bytearray(st["ct"])
        ct[-5] ^= 0xFF
        return expect_error("InvalidCiphertextException", lambda: kms.decrypt(
            CiphertextBlob=bytes(ct), EncryptionContext={"app": "orders"}))
    s.step("tampered ciphertext is refused", tampered)

    def datakey():
        dk = kms.generate_data_key(KeyId=st["key"], KeySpec="AES_256")
        back = kms.decrypt(CiphertextBlob=dk["CiphertextBlob"])["Plaintext"]
        return len(dk["Plaintext"]) == 32 and back == dk["Plaintext"], f"plaintext_len={len(dk['Plaintext'])}"
    s.step("GenerateDataKey + decrypt", datakey)

    def disabled():
        kms.disable_key(KeyId=st["key"])
        r = expect_error("DisabledException", lambda: kms.encrypt(KeyId=st["key"], Plaintext=b"x"))
        kms.enable_key(KeyId=st["key"])
        return r
    s.step("disabled key refuses Encrypt (AWS: DisabledException)", disabled)
    return s.result()


# ---------------------------------------------------------------- Secrets Manager
def secrets_scenario():
    s = Scenario("Secrets Manager: versions, stages, deletion")
    sm = client("secretsmanager")
    name = f"ft/db-{RUN}"

    def create():
        sm.create_secret(Name=name, SecretString=json.dumps({"password": "v1"}))
        return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])["password"] == "v1", "v1"
    s.step("create + get", create)

    def stages():
        sm.put_secret_value(SecretId=name, SecretString=json.dumps({"password": "v2"}))
        cur = json.loads(sm.get_secret_value(SecretId=name)["SecretString"])["password"]
        prev = json.loads(sm.get_secret_value(SecretId=name, VersionStage="AWSPREVIOUS")["SecretString"])["password"]
        return cur == "v2" and prev == "v1", f"AWSCURRENT={cur} AWSPREVIOUS={prev}"
    s.step("new version moves AWSCURRENT, keeps AWSPREVIOUS", stages)

    def deletion():
        sm.delete_secret(SecretId=name, RecoveryWindowInDays=7)
        ok, d = expect_error("InvalidRequestException", lambda: sm.get_secret_value(SecretId=name))
        sm.restore_secret(SecretId=name)
        back = json.loads(sm.get_secret_value(SecretId=name)["SecretString"])["password"]
        return ok and back == "v2", f"{d}; after restore={back}"
    s.step("scheduled deletion blocks reads, restore brings it back", deletion)
    return s.result()


# ---------------------------------------------------------------- Step Functions
def sfn_scenario(shared):
    s = Scenario("Step Functions: Choice, Wait, Lambda task, Fail")
    sfn, iam = client("stepfunctions"), client("iam")
    st = {}
    fn_arn = shared.get("fn_arn", "arn:aws:lambda:us-east-1:000000000000:function:missing")
    definition = {
        "StartAt": "Classify",
        "States": {
            "Classify": {"Type": "Choice", "Choices": [{"Variable": "$.n", "NumericGreaterThan": 5, "Next": "Big"}],
                         "Default": "Reject"},
            "Big": {"Type": "Wait", "Seconds": 1, "Next": "CallLambda"},
            "CallLambda": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                           "Parameters": {"FunctionName": fn_arn, "Payload": {"msg.$": "$.label"}},
                           "OutputPath": "$.Payload", "End": True},
            "Reject": {"Type": "Fail", "Error": "TooSmall", "Cause": "n <= 5"},
        },
    }

    def create():
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "states.amazonaws.com"},
                                                         "Action": "sts:AssumeRole"}]}
        role = iam.create_role(RoleName=f"ft-sfn-{RUN}", AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        if ROLE_POLICIES:
            iam.put_role_policy(RoleName=f"ft-sfn-{RUN}", PolicyName="invoke-fn", PolicyDocument=json.dumps({
                "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "lambda:InvokeFunction",
                                                        "Resource": fn_arn}]}))
        st["sm"] = sfn.create_state_machine(name=f"ft-flow-{RUN}", definition=json.dumps(definition), roleArn=role)["stateMachineArn"]
        return True, st["sm"]
    s.step("create state machine", create)

    def run(inp):
        ex = sfn.start_execution(stateMachineArn=st["sm"], input=json.dumps(inp))["executionArn"]
        d = None
        for _ in range(60):
            d = sfn.describe_execution(executionArn=ex)
            if d["status"] != "RUNNING":
                break
            time.sleep(0.5)
        return d

    def happy():
        d = run({"n": 7, "label": "big-order"})
        out = json.loads(d.get("output") or "{}")
        return d["status"] == "SUCCEEDED" and out.get("echo") == "big-order", \
            f"status={d['status']} output={out} error={d.get('error')} cause={str(d.get('cause'))[:300]}"
    s.step("Choice -> Wait -> Lambda task succeeds", happy)

    def fail_path():
        d = run({"n": 2, "label": "small"})
        return d["status"] == "FAILED" and d.get("error") == "TooSmall", f"status={d['status']} error={d.get('error')}"
    s.step("Fail state ends the execution with its error", fail_path)
    return s.result()


def main():
    shared = {}
    started = time.perf_counter()
    results = []
    for fn in (s3_scenario, sqs_scenario, dynamodb_scenario, lambda_scenario, eventbridge_scenario,
               sns_scenario, kms_scenario, secrets_scenario, sfn_scenario):
        try:
            r = fn(shared) if fn in (lambda_scenario, sfn_scenario) else fn()
        except Exception as e:
            r = {"scenario": fn.__name__, "status": "error", "error": err(e), "checks": []}
        results.append(r)
        print(f"{r['status']:8} {r.get('passed', 0)}/{r.get('total', 0)}  {r['scenario']}", flush=True)
        for c in r["checks"]:
            if not c["ok"]:
                print(f"           FAIL {c['check']}: {c['detail']}", flush=True)
    summary = {"target": TARGET, "role_policies": ROLE_POLICIES, "endpoint": os.environ.get("AWS_ENDPOINT_URL"), "run": RUN,
               "seconds": round(time.perf_counter() - started, 1),
               "checks_passed": sum(c["ok"] for r in results for c in r["checks"]),
               "checks_total": sum(len(r["checks"]) for r in results),
               "lambda_invoke_ms": shared.get("lambda_ms"), "lambda_probe": shared.get("lambda_probe"),
               "results": results}
    with open(OUT, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n{summary['checks_passed']}/{summary['checks_total']} checks passed in {summary['seconds']} s -> {OUT}")


if __name__ == "__main__":
    main()
