#!/usr/bin/env bash
#
# Provision the Excel AI Agent backend on EC2 (us-west-2 by default).
#
# Creates:
#   - an S3 bucket + deployment artifact
#   - an IAM role/instance profile for S3 read + SSM
#   - a security group (80/443, optional SSH from your current IP)
#   - an Elastic IP
#   - a t3.medium Ubuntu 24.04 instance running FastAPI + Ollama
#   - automatic HTTPS via Caddy and <elastic-ip>.sslip.io
#
# Usage:
#   AWS_PROFILE=aws-agent AWS_REGION=us-west-2 ./deploy/aws/provision.sh
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

AWS_PROFILE="${AWS_PROFILE:-aws-agent}"
AWS_REGION="${AWS_REGION:-us-west-2}"
STACK_NAME="${STACK_NAME:-excel-ai-agent}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
CUSTOM_MODEL="${CUSTOM_MODEL:-excel-agent-qwen}"
VOLUME_SIZE="${VOLUME_SIZE:-30}"
CORS_ORIGINS="${CORS_ORIGINS:-*}"
SSH_CIDR="${SSH_CIDR:-}"

export AWS_PROFILE AWS_REGION

for required in aws tar curl python3; do
  command -v "$required" >/dev/null 2>&1 || {
    echo "Missing required command: $required" >&2
    exit 1
  }
done

AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
S3_BUCKET="${S3_BUCKET:-excel-ai-agent-deploy-${AWS_ACCOUNT_ID}-${AWS_REGION}}"
ROLE_NAME="${ROLE_NAME:-ExcelAIAgentEC2Role}"
PROFILE_NAME="${PROFILE_NAME:-ExcelAIAgentEC2Profile}"
SG_NAME="${SG_NAME:-excel-ai-agent-sg}"
KEY_NAME="${KEY_NAME:-excel-ai-agent-key-$(date +%Y%m%d%H%M%S)}"
STATE_FILE="$ROOT_DIR/deploy/aws/.state"
ARTIFACT="$ROOT_DIR/dist/excel-ai-agent.tar.gz"
USER_DATA="$ROOT_DIR/dist/excel-ai-agent-user-data.sh"
BLOCK_DEVICE_MAP="$ROOT_DIR/dist/excel-ai-agent-block-device.json"

mkdir -p "$ROOT_DIR/dist"

existing_instance="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$STACK_NAME" \
            "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)"
if [ -n "$existing_instance" ] && [ "$existing_instance" != "None" ]; then
  echo "An instance tagged '$STACK_NAME' already exists: $existing_instance"
  echo "Run deploy/aws/teardown.sh first, or use deploy/aws/update.sh for a code update."
  exit 1
fi

echo "==> Building deployment artifact"
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

echo "==> Creating S3 bucket and uploading artifact"
if ! aws s3api head-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1; then
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$S3_BUCKET" \
    --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
  aws s3api put-bucket-encryption --bucket "$S3_BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
fi
aws s3 cp "$ARTIFACT" "s3://$S3_BUCKET/releases/excel-ai-agent.tar.gz" >/dev/null

echo "==> Creating IAM role and instance profile"
TRUST_FILE="$(mktemp)"
cat >"$TRUST_FILE" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"Service": "ec2.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$TRUST_FILE" >/dev/null
fi
aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null 2>&1 || true

S3_POLICY_FILE="$(mktemp)"
cat >"$S3_POLICY_FILE" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::${S3_BUCKET}/releases/*"]
    }
  ]
}
JSON
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name ExcelAIAgentS3Read \
  --policy-document "file://$S3_POLICY_FILE" >/dev/null

if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null
fi
aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" \
  --role-name "$ROLE_NAME" >/dev/null 2>&1 || true
sleep 10

echo "==> Creating security group"
VPC_ID="$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)"
if [ -z "$VPC_ID" ] || [ "$VPC_ID" = "None" ]; then
  echo "No default VPC found in $AWS_REGION" >&2
  exit 1
fi

SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  SG_ID="$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "Excel AI Agent HTTP/HTTPS" \
    --vpc-id "$VPC_ID" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$SG_NAME}]" \
    --query GroupId --output text)"
fi
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
  --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
  --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null 2>&1 || true

if [ -z "$SSH_CIDR" ]; then
  SSH_CIDR="$(curl -4 -fsS https://checkip.amazonaws.com 2>/dev/null | tr -d '\n')/32" || SSH_CIDR=""
fi
KEY_ARGS=()
KEY_PATH=""
if [ -n "$SSH_CIDR" ]; then
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  KEY_PATH="$HOME/.ssh/${KEY_NAME}.pem"
  aws ec2 create-key-pair --key-name "$KEY_NAME" \
    --query KeyMaterial --output text >"$KEY_PATH"
  chmod 600 "$KEY_PATH"
  KEY_ARGS=(--key-name "$KEY_NAME")
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
    --protocol tcp --port 22 --cidr "$SSH_CIDR" >/dev/null 2>&1 || true
  echo "    SSH access restricted to $SSH_CIDR (key: $KEY_PATH)"
fi

echo "==> Allocating Elastic IP"
EIP_ALLOC="$(aws ec2 describe-addresses \
  --filters "Name=tag:Name,Values=${STACK_NAME}-eip" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
if [ -z "$EIP_ALLOC" ] || [ "$EIP_ALLOC" = "None" ]; then
  EIP_ALLOC="$(aws ec2 allocate-address \
    --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${STACK_NAME}-eip}]" \
    --query AllocationId --output text)"
fi
EIP_PUBLIC="$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" \
  --query 'Addresses[0].PublicIp' --output text)"

echo "==> Resolving Ubuntu 24.04 AMI"
AMI_ID="$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameter.Value' --output text 2>/dev/null || true)"
if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
  AMI_ID="$(aws ec2 describe-images --owners 099720109477 \
    --filters 'Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*' \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text)"
fi

echo "==> Rendering user-data"
sed \
  -e "s|__S3_BUCKET__|$S3_BUCKET|g" \
  -e "s|__AWS_REGION__|$AWS_REGION|g" \
  -e "s|__OLLAMA_MODEL__|$OLLAMA_MODEL|g" \
  -e "s|__CUSTOM_MODEL__|$CUSTOM_MODEL|g" \
  -e "s|__PUBLIC_IP__|$EIP_PUBLIC|g" \
  -e "s|__CORS_ORIGINS__|$CORS_ORIGINS|g" \
  "$ROOT_DIR/deploy/aws/user-data.sh.tpl" >"$USER_DATA"

cat >"$BLOCK_DEVICE_MAP" <<JSON
[
  {
    "DeviceName": "/dev/sda1",
    "Ebs": {
      "VolumeSize": $VOLUME_SIZE,
      "VolumeType": "gp3",
      "DeleteOnTermination": true,
      "Encrypted": true
    }
  }
]
JSON

SUBNET_ID="$(aws ec2 describe-subnets \
  --filters Name=default-for-az,Values=true \
  --query 'Subnets[0].SubnetId' --output text)"

echo "==> Launching $INSTANCE_TYPE instance (this takes a moment)"
RUN_ARGS=(
  --image-id "$AMI_ID"
  --instance-type "$INSTANCE_TYPE"
  --iam-instance-profile "Name=$PROFILE_NAME"
  --security-group-ids "$SG_ID"
  --subnet-id "$SUBNET_ID"
  --associate-public-ip-address
  --user-data "file://$USER_DATA"
  --block-device-mappings "file://$BLOCK_DEVICE_MAP"
  --metadata-options 'HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2'
  --credit-specification 'CpuCredits=standard'
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$STACK_NAME},{Key=Project,Value=excel-ai-agent}]"
)
if [ ${#KEY_ARGS[@]} -gt 0 ]; then
  RUN_ARGS+=("${KEY_ARGS[@]}")
fi
INSTANCE_ID="$(aws ec2 run-instances "${RUN_ARGS[@]}" \
  --query 'Instances[0].InstanceId' --output text)"

aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ec2 associate-address --instance-id "$INSTANCE_ID" \
  --allocation-id "$EIP_ALLOC" >/dev/null

echo "==> Waiting for SSM agent"
SSM_ONLINE=""
for _ in $(seq 1 60); do
  SSM_ONLINE="$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)"
  if [ "$SSM_ONLINE" = "Online" ]; then
    break
  fi
  sleep 10
done
if [ "$SSM_ONLINE" != "Online" ]; then
  echo "SSM agent did not come online. Check the instance and /var/log/cloud-init-output.log." >&2
  exit 1
fi

echo "==> Waiting for bootstrap + Ollama model download (10-25 minutes)"
SSM_PARAMS="$(mktemp)"
cat >"$SSM_PARAMS" <<'JSON'
{
  "commands": [
    "for i in $(seq 1 240); do if [ -f /opt/excel-agent/ready ]; then echo READY; exit 0; fi; sleep 10; done; echo TIMEOUT; tail -100 /var/log/excel-agent-bootstrap.log; exit 1"
  ]
}
JSON
BOOTSTRAP_COMMAND_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 2700 \
  --parameters "$(cat "$SSM_PARAMS")" \
  --comment "Wait for Excel AI Agent bootstrap" \
  --query 'Command.CommandId' --output text)"

BOOTSTRAP_STATUS="Pending"
for _ in $(seq 1 180); do
  BOOTSTRAP_STATUS="$(aws ssm get-command-invocation \
    --command-id "$BOOTSTRAP_COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' --output text 2>/dev/null || true)"
  case "$BOOTSTRAP_STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 10
done

BOOTSTRAP_OUTPUT="$(aws ssm get-command-invocation \
  --command-id "$BOOTSTRAP_COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' \
  --output json)"
echo "$BOOTSTRAP_OUTPUT"
if [ "$BOOTSTRAP_STATUS" != "Success" ]; then
  echo "Bootstrap failed. Inspect /var/log/excel-agent-bootstrap.log via SSM." >&2
  exit 1
fi

echo "==> Starting Caddy and requesting the HTTPS certificate"
START_PARAMS="$(mktemp)"
cat >"$START_PARAMS" <<'JSON'
{
  "commands": [
    "systemctl enable caddy",
    "systemctl restart caddy",
    "sleep 5",
    "systemctl is-active caddy",
    "journalctl -u caddy --no-pager -n 20 || true"
  ]
}
JSON
START_COMMAND_ID="$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --timeout-seconds 300 \
  --parameters "$(cat "$START_PARAMS")" \
  --comment "Start Caddy" \
  --query 'Command.CommandId' --output text)"
for _ in $(seq 1 30); do
  START_STATUS="$(aws ssm get-command-invocation \
    --command-id "$START_COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' --output text 2>/dev/null || true)"
  case "$START_STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 5
done
aws ssm get-command-invocation \
  --command-id "$START_COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' \
  --output json || true

BACKEND_URL="https://${EIP_PUBLIC}.sslip.io"
echo "==> Waiting for $BACKEND_URL/health"
HEALTH_OK=""
for _ in $(seq 1 60); do
  if curl -fsS --max-time 10 "$BACKEND_URL/health" -o /tmp/excel-agent-health.json; then
    HEALTH_OK="yes"
    break
  fi
  sleep 10
done
if [ -n "$HEALTH_OK" ]; then
  cat /tmp/excel-agent-health.json
  echo
else
  echo "HTTPS health check did not succeed yet. Check Caddy with SSM." >&2
fi

cat >"$STATE_FILE" <<STATE
AWS_PROFILE=$AWS_PROFILE
AWS_REGION=$AWS_REGION
INSTANCE_ID=$INSTANCE_ID
INSTANCE_TYPE=$INSTANCE_TYPE
EIP_ALLOC=$EIP_ALLOC
EIP_PUBLIC=$EIP_PUBLIC
BACKEND_URL=$BACKEND_URL
S3_BUCKET=$S3_BUCKET
SECURITY_GROUP=$SG_ID
ROLE_NAME=$ROLE_NAME
PROFILE_NAME=$PROFILE_NAME
KEY_PATH=$KEY_PATH
STATE
chmod 600 "$STATE_FILE"

echo
echo "Deployment complete"
echo "  Backend URL : $BACKEND_URL"
echo "  Instance    : $INSTANCE_ID ($INSTANCE_TYPE, $AWS_REGION)"
echo "  Elastic IP  : $EIP_PUBLIC"
echo "  Artifact    : s3://$S3_BUCKET/releases/excel-ai-agent.tar.gz"
echo "  State file  : $STATE_FILE"
