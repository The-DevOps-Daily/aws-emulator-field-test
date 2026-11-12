"""Where does the emulator enforce permissions, and where does it let a request through that AWS
would refuse? Each case states the result AWS documents (policy evaluation logic, S3 bucket
policies, KMS key policies, SQS queue policies for EventBridge/SNS targets).

The "aws_expected" column is taken from AWS documentation. These cases were NOT run on real AWS.

Usage: python iam_gap.py <target-name>   (writes results/<target-name>-iam-gap.json)
"""
import json
import os
import sys
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

TARGET = sys.argv[1] if len(sys.argv) > 1 else "aws-emulator"
OUT = os.path.join(os.environ.get("RESULTS_DIR", "/root/results"), f"{TARGET}-iam-gap.json")
RUN = uuid.uuid4().hex[:8]
CFG = Config(retries={"max_attempts": 1}, connect_timeout=5, read_timeout=30)
ADMIN = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}
DENIED = {"AccessDenied", "AccessDeniedException", "AuthorizationError", "AuthorizationErrorException",
          "UnauthorizedOperation", "KMSAccessDeniedException"}

root = {n: boto3.client(n, config=CFG) for n in
        ("iam", "s3", "sqs", "dynamodb", "kms", "secretsmanager", "lambda", "sns", "events", "sts")}
cases = []


def make_user(name, inline=None, boundary_arn=None):
    root["iam"].create_user(UserName=name, **({"PermissionsBoundary": boundary_arn} if boundary_arn else {}))
    if inline:
        root["iam"].put_user_policy(UserName=name, PolicyName="inline", PolicyDocument=json.dumps(inline))
    k = root["iam"].create_access_key(UserName=name)["AccessKey"]
    arn = root["iam"].get_user(UserName=name)["User"]["Arn"]
    sess = boto3.session.Session(aws_access_key_id=k["AccessKeyId"], aws_secret_access_key=k["SecretAccessKey"])
    return arn, lambda svc: sess.client(svc, config=CFG)


def attempt(fn):
    try:
        fn()
        return "allow", ""
    except ClientError as e:
        code = e.response["Error"].get("Code")
        msg = e.response["Error"].get("Message", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        # Any 403 is a refusal, even when the error code is not the one AWS uses (Moto returns "403: Forbidden").
        refused = code in DENIED or status == 403
        return ("deny" if refused else f"error:{code}"), f"HTTP {status} {code}: {msg}"[:300]
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"[:300]


def record(group, case, expected, fn):
    got, detail = attempt(fn)
    cases.append({"group": group, "case": case, "aws_expected": expected, "emulator": got,
                  "matches_aws": got == expected, "detail": detail})
    print(f"{'OK  ' if got == expected else 'GAP '} [{group}] {case}: aws={expected} emulator={got} {detail[:140]}", flush=True)


# ---- fixtures created with the emulator's own key
bucket = f"iamgap-{RUN}"
root["s3"].create_bucket(Bucket=bucket)
q = root["sqs"].create_queue(QueueName=f"iamgap-{RUN}")["QueueUrl"]
table = f"iamgap-{RUN}"
root["dynamodb"].create_table(TableName=table, BillingMode="PAY_PER_REQUEST",
                              AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                              KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}])
root["dynamodb"].get_waiter("table_exists").wait(TableName=table, WaiterConfig={"Delay": 1, "MaxAttempts": 30})
key = root["kms"].create_key()["KeyMetadata"]["KeyId"]
secret = root["secretsmanager"].create_secret(Name=f"iamgap/{RUN}", SecretString="s3cr3t")["ARN"]
topic = root["sns"].create_topic(Name=f"iamgap-{RUN}")["TopicArn"]

# ---- 1. A user with no policies at all (AWS: implicit deny everywhere except GetCallerIdentity)
_, none = make_user(f"no-policy-{RUN}")
g = "user with no policies"
record(g, "sts:GetCallerIdentity", "allow", lambda: none("sts").get_caller_identity())
record(g, "s3:ListAllMyBuckets", "deny", lambda: none("s3").list_buckets())
record(g, "s3:PutObject", "deny", lambda: none("s3").put_object(Bucket=bucket, Key="x", Body=b"x"))
record(g, "sqs:SendMessage", "deny", lambda: none("sqs").send_message(QueueUrl=q, MessageBody="x"))
record(g, "dynamodb:PutItem", "deny", lambda: none("dynamodb").put_item(TableName=table, Item={"id": {"S": "x"}}))
record(g, "kms:Encrypt", "deny", lambda: none("kms").encrypt(KeyId=key, Plaintext=b"x"))
record(g, "secretsmanager:GetSecretValue", "deny", lambda: none("secretsmanager").get_secret_value(SecretId=secret))
record(g, "sns:Publish", "deny", lambda: none("sns").publish(TopicArn=topic, Message="x"))
record(g, "events:PutEvents", "deny", lambda: none("events").put_events(
    Entries=[{"Source": "t", "DetailType": "t", "Detail": "{}"}]))
record(g, "iam:CreateUser", "deny", lambda: none("iam").create_user(UserName=f"escalate-{RUN}"))

# ---- 2. Identity policy logic: explicit deny, permissions boundary, condition keys
g = "identity policies"
_, admin = make_user(f"admin-{RUN}", inline=ADMIN)
record(g, "admin user: s3:PutObject (baseline)", "allow",
       lambda: admin("s3").put_object(Bucket=bucket, Key="admin.txt", Body=b"x"))

deny_put = {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "*", "Resource": "*"},
    {"Effect": "Deny", "Action": "s3:PutObject", "Resource": "*"}]}
_, denied = make_user(f"admin-deny-{RUN}", inline=deny_put)
record(g, "Allow * plus explicit Deny s3:PutObject", "deny",
       lambda: denied("s3").put_object(Bucket=bucket, Key="d.txt", Body=b"x"))

boundary = root["iam"].create_policy(PolicyName=f"read-only-boundary-{RUN}", PolicyDocument=json.dumps({
    "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"], "Resource": "*"}]}))["Policy"]["Arn"]
_, bounded = make_user(f"bounded-{RUN}", inline=ADMIN, boundary_arn=boundary)
record(g, "Allow * but permissions boundary allows only reads: s3:PutObject", "deny",
       lambda: bounded("s3").put_object(Bucket=bucket, Key="b.txt", Body=b"x"))

cond = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:PutObject", "Resource": "*",
                                                "Condition": {"StringEquals": {"s3:x-amz-server-side-encryption": "aws:kms"}}}]}
_, conditional = make_user(f"cond-{RUN}", inline=cond)
record(g, "Allow PutObject only with SSE-KMS: put without SSE", "deny",
       lambda: conditional("s3").put_object(Bucket=bucket, Key="c1.txt", Body=b"x"))
record(g, "Allow PutObject only with SSE-KMS: put with SSE-KMS", "allow",
       lambda: conditional("s3").put_object(Bucket=bucket, Key="c2.txt", Body=b"x", ServerSideEncryption="aws:kms"))

# ---- 3. Resource-based policies
g = "resource policies"
admin2_arn, admin2 = make_user(f"admin2-{RUN}", inline=ADMIN)
pol_bucket = f"iamgap-pol-{RUN}"
root["s3"].create_bucket(Bucket=pol_bucket)
root["s3"].put_bucket_policy(Bucket=pol_bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Deny", "Principal": {"AWS": admin2_arn}, "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{pol_bucket}/*"}]}))
record(g, "bucket policy denies this admin user: s3:PutObject", "deny",
       lambda: admin2("s3").put_object(Bucket=pol_bucket, Key="p.txt", Body=b"x"))

deny_all_bucket = f"iamgap-denyall-{RUN}"
root["s3"].create_bucket(Bucket=deny_all_bucket)
root["s3"].put_bucket_policy(Bucket=deny_all_bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Deny", "Principal": "*", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{deny_all_bucket}/*",
    "Condition": {"Bool": {"aws:SecureTransport": "true"}}}]}))
record(g, "bucket policy denies everyone over HTTPS: emulator's own key PutObject", "deny",
       lambda: root["s3"].put_object(Bucket=deny_all_bucket, Key="p.txt", Body=b"x"))

acct = root["sts"].get_caller_identity()["Account"]
kp_key = root["kms"].create_key(Policy=json.dumps({"Version": "2012-10-17", "Statement": [
    {"Sid": "root", "Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{acct}:root"}, "Action": "kms:*", "Resource": "*"},
    {"Sid": "deny-admin2", "Effect": "Deny", "Principal": {"AWS": admin2_arn}, "Action": "kms:Encrypt", "Resource": "*"}]}))["KeyMetadata"]["KeyId"]
record(g, "KMS key policy denies this admin user: kms:Encrypt", "deny",
       lambda: admin2("kms").encrypt(KeyId=kp_key, Plaintext=b"x"))

root["secretsmanager"].put_resource_policy(SecretId=secret, ResourcePolicy=json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Deny", "Principal": {"AWS": admin2_arn}, "Action": "secretsmanager:GetSecretValue", "Resource": "*"}]}))
record(g, "secret resource policy denies this admin user: GetSecretValue", "deny",
       lambda: admin2("secretsmanager").get_secret_value(SecretId=secret))


# ---- 3b. A same-account resource policy grant is enough on its own (AWS allows)
g = "resource policies"
grant_arn, granted = make_user(f"granted-{RUN}")
grant_bucket = f"iamgap-grant-{RUN}"
root["s3"].create_bucket(Bucket=grant_bucket)
root["s3"].put_bucket_policy(Bucket=grant_bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Principal": {"AWS": grant_arn}, "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{grant_bucket}/*"}]}))
record(g, "no identity policy, bucket policy allows this user: s3:PutObject", "allow",
       lambda: granted("s3").put_object(Bucket=grant_bucket, Key="g.txt", Body=b"x"))

# ---- 3c. Scoping, NotAction, more S3 reads by the no-policy user
g = "identity policies"
_, scoped = make_user(f"scoped-{RUN}", inline={"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{bucket}/*"}]})
record(g, "PutObject allowed on bucket A only: put to bucket A", "allow",
       lambda: scoped("s3").put_object(Bucket=bucket, Key="s.txt", Body=b"x"))
record(g, "PutObject allowed on bucket A only: put to bucket B", "deny",
       lambda: scoped("s3").put_object(Bucket=grant_bucket, Key="s.txt", Body=b"x"))
_, notaction = make_user(f"notaction-{RUN}", inline={"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "NotAction": "s3:*", "Resource": "*"}]})
record(g, "Allow NotAction s3:*: sqs:SendMessage", "allow",
       lambda: notaction("sqs").send_message(QueueUrl=q, MessageBody="x"))
record(g, "Allow NotAction s3:*: s3:PutObject", "deny",
       lambda: notaction("s3").put_object(Bucket=bucket, Key="n.txt", Body=b"x"))
g = "user with no policies"
record(g, "s3:ListBucket (ListObjectsV2)", "deny", lambda: none("s3").list_objects_v2(Bucket=bucket))
record(g, "s3:GetObject", "deny", lambda: none("s3").get_object(Bucket=bucket, Key="admin.txt"))

# ---- 3d. Roles: trust policies and session policies
g = "roles and sessions"
lambda_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                        "Action": "sts:AssumeRole"}]}
svc_role = root["iam"].create_role(RoleName=f"svc-only-{RUN}", AssumeRolePolicyDocument=json.dumps(lambda_trust))["Role"]["Arn"]
record(g, "admin user assumes a role that trusts only lambda.amazonaws.com", "deny",
       lambda: admin("sts").assume_role(RoleArn=svc_role, RoleSessionName="probe"))
acct_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{acct}:root"},
                                                      "Action": "sts:AssumeRole"}]}
acct_role = root["iam"].create_role(RoleName=f"acct-trust-{RUN}", AssumeRolePolicyDocument=json.dumps(acct_trust))["Role"]["Arn"]
root["iam"].put_role_policy(RoleName=f"acct-trust-{RUN}", PolicyName="admin", PolicyDocument=json.dumps(ADMIN))
record(g, "no-policy user assumes a role that trusts the account (needs sts:AssumeRole itself)", "deny",
       lambda: none("sts").assume_role(RoleArn=acct_role, RoleSessionName="probe"))


def escalate_then_write():
    # Follow the path to the end: if the no-policy user gets role credentials, do they work?
    c = none("sts").assume_role(RoleArn=acct_role, RoleSessionName="escalate")["Credentials"]
    s3 = boto3.client("s3", config=CFG, aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                      aws_session_token=c["SessionToken"])
    s3.put_object(Bucket=bucket, Key="escalated.txt", Body=b"x")
    boto3.client("iam", config=CFG, aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                 aws_session_token=c["SessionToken"]).create_user(UserName=f"made-by-escalation-{RUN}")


record(g, "no-policy user: assume the account-trusted admin role, then s3:PutObject + iam:CreateUser", "deny",
       escalate_then_write)


def session_put():
    c = admin("sts").assume_role(RoleArn=acct_role, RoleSessionName="narrow", Policy=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"}]}))["Credentials"]
    s3 = boto3.client("s3", config=CFG, aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                      aws_session_token=c["SessionToken"])
    s3.put_object(Bucket=bucket, Key="sess.txt", Body=b"x")


record(g, "admin role assumed with a GetObject-only session policy: s3:PutObject", "deny", session_put)

# ---- 4. Service-to-service delivery that needs a queue policy on real AWS
def delivered(url, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        if root["sqs"].receive_message(QueueUrl=url, WaitTimeSeconds=1).get("Messages"):
            return True
    return False


def ev_no_policy():
    url = root["sqs"].create_queue(QueueName=f"iamgap-ev-{RUN}")["QueueUrl"]
    arn = root["sqs"].get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    root["events"].put_rule(Name=f"iamgap-{RUN}", EventPattern=json.dumps({"source": ["iamgap"]}))
    root["events"].put_targets(Rule=f"iamgap-{RUN}", Targets=[{"Id": "q", "Arn": arn}])
    root["events"].put_events(Entries=[{"Source": "iamgap", "DetailType": "t", "Detail": "{}"}])
    return delivered(url)


def sns_no_policy():
    url = root["sqs"].create_queue(QueueName=f"iamgap-sns-{RUN}")["QueueUrl"]
    arn = root["sqs"].get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    root["sns"].subscribe(TopicArn=topic, Protocol="sqs", Endpoint=arn)
    root["sns"].publish(TopicArn=topic, Message="x")
    return delivered(url)


g = "service-to-service"
for case, fn in (("EventBridge rule -> SQS queue with no queue policy", ev_no_policy),
                 ("SNS topic -> SQS queue with no queue policy", sns_no_policy)):
    try:
        got = "delivered" if fn() else "not delivered"
        detail = ""
    except ClientError as e:
        got, detail = "error", str(e)[:300]
    cases.append({"group": g, "case": case, "aws_expected": "not delivered", "emulator": got,
                  "matches_aws": got == "not delivered", "detail": detail})
    print(f"{'OK  ' if got == 'not delivered' else 'GAP '} [{g}] {case}: aws=not delivered emulator={got}", flush=True)

summary = {"target": TARGET, "run": RUN, "cases": len(cases), "matches_aws": sum(c["matches_aws"] for c in cases),
           "gaps": [c["case"] for c in cases if not c["matches_aws"]], "results": cases,
           "note": "aws_expected is taken from AWS documentation; these cases were not run on real AWS."}
with open(OUT, "w") as f:
    json.dump(summary, f, indent=2)
print(f"\n{summary['matches_aws']}/{summary['cases']} cases match documented AWS behaviour -> {OUT}")
