// JavaScript SDK v3 against the emulator. Needs NODE_EXTRA_CA_CERTS (set by env-aws-emulator.sh).
import { S3Client, CreateBucketCommand, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { DynamoDBClient, CreateTableCommand, PutItemCommand, GetItemCommand, waitUntilTableExists } from "@aws-sdk/client-dynamodb";
import { SQSClient, CreateQueueCommand, SendMessageCommand, ReceiveMessageCommand } from "@aws-sdk/client-sqs";

const results = [];
async function check(name, fn) {
  const t = performance.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail, ms: Math.round(performance.now() - t) });
  } catch (e) {
    results.push({ name, ok: false, detail: `${e.name}: ${e.message}`, ms: Math.round(performance.now() - t) });
  }
}

const s3 = new S3Client({ forcePathStyle: true });
const ddb = new DynamoDBClient({});
const sqs = new SQSClient({});

await check("S3 put/get", async () => {
  await s3.send(new CreateBucketCommand({ Bucket: "js-demo" }));
  await s3.send(new PutObjectCommand({ Bucket: "js-demo", Key: "k", Body: "from-js" }));
  const r = await s3.send(new GetObjectCommand({ Bucket: "js-demo", Key: "k" }));
  const body = await r.Body.transformToString();
  if (body !== "from-js") throw new Error(`body=${body}`);
  return body;
});
await check("DynamoDB put/get", async () => {
  await ddb.send(new CreateTableCommand({
    TableName: "js-demo", BillingMode: "PAY_PER_REQUEST",
    AttributeDefinitions: [{ AttributeName: "id", AttributeType: "S" }],
    KeySchema: [{ AttributeName: "id", KeyType: "HASH" }],
  }));
  await waitUntilTableExists({ client: ddb, maxWaitTime: 30 }, { TableName: "js-demo" });
  await ddb.send(new PutItemCommand({ TableName: "js-demo", Item: { id: { S: "1" }, v: { S: "x" } } }));
  const r = await ddb.send(new GetItemCommand({ TableName: "js-demo", Key: { id: { S: "1" } } }));
  return JSON.stringify(r.Item);
});
await check("SQS send/receive", async () => {
  const { QueueUrl } = await sqs.send(new CreateQueueCommand({ QueueName: "js-demo" }));
  await sqs.send(new SendMessageCommand({ QueueUrl, MessageBody: "hello" }));
  const r = await sqs.send(new ReceiveMessageCommand({ QueueUrl, WaitTimeSeconds: 1 }));
  return r.Messages?.[0]?.Body;
});
console.log(JSON.stringify({ node: process.version, results }, null, 2));
