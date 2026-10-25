#!/usr/bin/env bash
#
# Destroy the EC2 backend created by provision.sh.
# This also deletes the S3 deployment bucket and the IAM role/profile.
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STATE_FILE="$ROOT_DIR/deploy/aws/.state"

if [ ! -f "$STATE_FILE" ]; then
  echo "Missing $STATE_FILE. Nothing to tear down." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$STATE_FILE"

export AWS_PROFILE AWS_REGION

echo "==> Terminating $INSTANCE_ID"
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" 2>/dev/null || true

if [ -n "${EIP_ALLOC:-}" ]; then
  echo "==> Releasing Elastic IP $EIP_PUBLIC"
  aws ec2 release-address --allocation-id "$EIP_ALLOC" >/dev/null 2>&1 || true
fi

if [ -n "${SECURITY_GROUP:-}" ]; then
  echo "==> Deleting security group $SECURITY_GROUP"
  for _ in $(seq 1 12); do
    aws ec2 delete-security-group --group-id "$SECURITY_GROUP" >/dev/null 2>&1 && break
    sleep 5
  done
fi

echo "==> Deleting IAM instance profile and role"
aws iam remove-role-from-instance-profile \
  --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME" >/dev/null 2>&1 || true
aws iam delete-instance-profile \
  --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1 || true
aws iam delete-role-policy \
  --role-name "$ROLE_NAME" --policy-name ExcelAIAgentS3Read >/dev/null 2>&1 || true
aws iam detach-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore >/dev/null 2>&1 || true
aws iam delete-role --role-name "$ROLE_NAME" >/dev/null 2>&1 || true

if [ -n "${S3_BUCKET:-}" ]; then
  echo "==> Deleting S3 bucket $S3_BUCKET"
  aws s3 rm "s3://$S3_BUCKET" --recursive >/dev/null 2>&1 || true
  aws s3api delete-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1 || true
fi

if [ -n "${GROQ_PARAM_NAME:-}" ]; then
  echo "==> Deleting SSM parameter $GROQ_PARAM_NAME"
  aws ssm delete-parameter --name "$GROQ_PARAM_NAME" >/dev/null 2>&1 || true
fi

if [ -n "${KEY_PATH:-}" ] && [ -f "$KEY_PATH" ]; then
  rm -f "$KEY_PATH"
fi

rm -f "$STATE_FILE"
echo "Teardown complete."
