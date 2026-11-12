# A small but realistic stack: storage, queues, events, a function, a workflow, secrets.
# Pointed at the emulator through the same environment variables as the CLI (see run-terraform.sh).
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.4" }
  }
}

provider "aws" {
  # Endpoint, credentials, CA bundle and region come from AWS_* environment variables.
  s3_use_path_style = true
}

locals { name = "tf-fieldtest" }

resource "aws_s3_bucket" "assets" { bucket = "${local.name}-assets" }

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id
  rule {
    id     = "expire-tmp"
    status = "Enabled"
    filter { prefix = "tmp/" }
    expiration { days = 7 }
  }
}

resource "aws_s3_object" "readme" {
  bucket  = aws_s3_bucket.assets.id
  key     = "README.txt"
  content = "hello from terraform"
}

resource "aws_sqs_queue" "dlq" { name = "${local.name}-dlq" }

resource "aws_sqs_queue" "jobs" {
  name                       = "${local.name}-jobs"
  visibility_timeout_seconds = 60
  redrive_policy             = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 3 })
}

resource "aws_dynamodb_table" "orders" {
  name             = "${local.name}-orders"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "pk"
  range_key        = "sk"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"
  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }
  global_secondary_index {
    name            = "by-status"
    hash_key        = "status"
    projection_type = "ALL"
  }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_sns_topic" "events" { name = "${local.name}-events" }

resource "aws_sqs_queue_policy" "jobs" {
  queue_url = aws_sqs_queue.jobs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Principal = { Service = "sns.amazonaws.com" }, Action = "sqs:SendMessage",
      Resource = aws_sqs_queue.jobs.arn, Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.events.arn } } },
      { Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sqs:SendMessage",
      Resource = aws_sqs_queue.jobs.arn, Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.orders.arn } } },
    ]
  })
}

resource "aws_sns_topic_subscription" "jobs" {
  topic_arn            = aws_sns_topic.events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.jobs.arn
  raw_message_delivery = true
}

resource "aws_kms_key" "app" {
  description             = "app data key"
  deletion_window_in_days = 7
  enable_key_rotation     = true
}

resource "aws_kms_alias" "app" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.app.key_id
}

resource "aws_secretsmanager_secret" "db" {
  name       = "${local.name}/db"
  kms_key_id = aws_kms_key.app.arn
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id     = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({ username = "app", password = "not-a-real-password" })
}

resource "aws_ssm_parameter" "feature_flag" {
  name  = "/${local.name}/feature-flag"
  type  = "String"
  value = "on"
}

resource "aws_iam_role" "fn" {
  name = "${local.name}-fn"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "fn" {
  role = aws_iam_role.fn.id
  name = "queue-and-table"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.jobs.arn },
      { Effect = "Allow", Action = ["dynamodb:PutItem"], Resource = aws_dynamodb_table.orders.arn },
    ]
  })
}

data "archive_file" "fn" {
  type        = "zip"
  output_path = "${path.module}/fn.zip"
  source {
    filename = "app.py"
    content  = "def handler(event, context):\n    return {'records': len(event.get('Records', []))}\n"
  }
}

resource "aws_cloudwatch_log_group" "fn" {
  name              = "/aws/lambda/${local.name}-worker"
  retention_in_days = 7
}

resource "aws_lambda_function" "worker" {
  function_name    = "${local.name}-worker"
  role             = aws_iam_role.fn.arn
  runtime          = "python3.12"
  handler          = "app.handler"
  filename         = data.archive_file.fn.output_path
  source_code_hash = data.archive_file.fn.output_base64sha256
  timeout          = 30
  depends_on       = [aws_cloudwatch_log_group.fn]
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 10
}

resource "aws_cloudwatch_event_rule" "orders" {
  name          = "${local.name}-orders"
  event_pattern = jsonencode({ source = ["app.orders"] })
}

resource "aws_cloudwatch_event_target" "orders" {
  rule = aws_cloudwatch_event_rule.orders.name
  arn  = aws_sqs_queue.jobs.arn
}

resource "aws_iam_role" "sfn" {
  name = "${local.name}-sfn"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  role = aws_iam_role.sfn.id
  name = "invoke-worker"
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.worker.arn }]
  })
}

resource "aws_sfn_state_machine" "flow" {
  name     = "${local.name}-flow"
  role_arn = aws_iam_role.sfn.arn
  definition = jsonencode({
    StartAt = "Work"
    States = {
      Work = { Type = "Task", Resource = "arn:aws:states:::lambda:invoke",
      Parameters = { FunctionName = aws_lambda_function.worker.arn, "Payload.$" = "$" }, End = true }
    }
  })
}

output "queue_url" { value = aws_sqs_queue.jobs.id }
output "state_machine" { value = aws_sfn_state_machine.flow.arn }
