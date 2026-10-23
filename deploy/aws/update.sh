#!/usr/bin/env bash
#
# Re-upload the current checkout and restart the EC2 backend.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

STATE_FILE="$ROOT_DIR/deploy/aws/.state"
if [ ! -f "$STATE_FILE" ]; then
  echo "Missing $STATE_FILE. Run provision.sh first." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$STATE_FILE"

export AWS_PROFILE AWS_REGION

ARTIFACT="$ROOT_DIR/dist/excel-ai-agent.tar.gz"
mkdir -p "$ROOT_DIR/dist"

tar -czf "$ARTIFACT" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='backend/venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='backend/.env' \
  --exclude='backend/.env.save' \
  --exclude='deploy/aws/.state' \
  --exclude='deploy/aws/*.pem' \
  --exclude='node_modules' \
  --exclude='.vercel' \
  --exclude='*.zip' \
  --exclude='dist' \
  backend frontend deploy README.md vercel.json package.json scripts

aws s3 cp "$ARTIFACT" "s3://$S3_BUCKET/releases/excel-ai-agent.tar.gz" >/dev/null

PARAMS="$(mktemp)"
cat >"$PARAMS" <<JSON
{
  "commands": [
    "set -e",
    "aws s3api get-object --bucket $S3_BUCKET --key releases/excel-ai-agent.tar.gz --region $AWS_REGION /tmp/excel-ai-agent.tar.gz",
    "tar -xzf /tmp/excel-ai-agent.tar.gz -C /opt/excel-agent",
    "/opt/excel-agent/venv/bin/pip install -r /opt/excel-agent/backend/requirements.txt",
    "chown -R excelagent:excelagent /opt/excel-agent",
    "systemctl restart excel-agent",
    "sleep 2",
    "systemctl is-active excel-agent"
  ]
}
JSON

COMMAND_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 900 \
  --parameters "$(cat "$PARAMS")" \
  --comment "Update Excel AI Agent" \
  --query 'Command.CommandId' --output text)"

for _ in $(seq 1 90); do
  STATUS="$(aws ssm get-command-invocation \
    --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
    --query 'Status' --output text 2>/dev/null || true)"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 5
done

aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' \
  --output json
[ "$STATUS" = "Success" ] || exit 1

curl -fsS --max-time 20 "$BACKEND_URL/health"
echo
echo "Update complete: $BACKEND_URL"
