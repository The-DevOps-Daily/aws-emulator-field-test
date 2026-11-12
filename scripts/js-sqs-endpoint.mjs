// Does the JS SDK v3 SQS client stay on the emulator? The emulator returns AWS-shaped queue URLs
// (https://sqs.<region>.amazonaws.com/<account>/<name>), and the SDK's default
// useQueueUrlAsEndpoint=true sends queue calls to that host.
import { SQSClient, CreateQueueCommand, SendMessageCommand, ReceiveMessageCommand } from "@aws-sdk/client-sqs";

async function attempt(label, client) {
  try {
    const { QueueUrl } = await client.send(new CreateQueueCommand({ QueueName: "js-endpoint-test" }));
    await client.send(new SendMessageCommand({ QueueUrl, MessageBody: "hello" }));
    const r = await client.send(new ReceiveMessageCommand({ QueueUrl, WaitTimeSeconds: 1 }));
    return { label, ok: true, queueUrl: QueueUrl, body: r.Messages?.[0]?.Body };
  } catch (e) {
    return { label, ok: false, error: `${e.name}: ${e.message}`, cause: e.cause?.code ?? e.code ?? null };
  }
}

const out = [];
out.push(await attempt("default client", new SQSClient({})));
out.push(await attempt("useQueueUrlAsEndpoint: false", new SQSClient({ useQueueUrlAsEndpoint: false })));
console.log(JSON.stringify({ node: process.version, hostsBlock: process.env.HOSTS_BLOCK ?? "off", out }, null, 2));
