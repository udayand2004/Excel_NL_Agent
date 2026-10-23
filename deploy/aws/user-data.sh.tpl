#!/bin/bash
set -euxo pipefail

exec > >(tee -a /var/log/excel-agent-bootstrap.log) 2>&1

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  python3.12-venv python3-pip \
  unzip curl ca-certificates gnupg \
  debian-keyring debian-archive-keyring apt-transport-https

# AWS CLI v2 (used only to download the deployment artifact from S3)
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install

# SSM agent so the instance can be administered without opening SSH
curl -fsSL "https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_amd64/amazon-ssm-agent.deb" \
  -o /tmp/amazon-ssm-agent.deb
dpkg -i /tmp/amazon-ssm-agent.deb || apt-get install -f -y
systemctl enable --now amazon-ssm-agent

# Ollama (CPU inference)
curl -fsSL https://ollama.com/install.sh | sh
mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<'OLLAMA_OVERRIDE'
[Service]
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
OLLAMA_OVERRIDE
systemctl daemon-reload
systemctl enable --now ollama

# Caddy provides automatic HTTPS for the API
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get install -y caddy
systemctl stop caddy

# Application install
id -u excelagent >/dev/null 2>&1 || useradd --system --home /opt/excel-agent --shell /usr/sbin/nologin excelagent
mkdir -p /opt/excel-agent /var/lib/excel-agent

aws s3api get-object \
  --bucket "__S3_BUCKET__" \
  --key "releases/excel-ai-agent.tar.gz" \
  --region "__AWS_REGION__" \
  /tmp/excel-ai-agent.tar.gz
tar -xzf /tmp/excel-ai-agent.tar.gz -C /opt/excel-agent

python3 -m venv /opt/excel-agent/venv
/opt/excel-agent/venv/bin/pip install --upgrade pip
/opt/excel-agent/venv/bin/pip install -r /opt/excel-agent/backend/requirements.txt
chown -R excelagent:excelagent /opt/excel-agent

cat >/etc/excel-agent.env <<'APP_ENV'
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_MODEL=__OLLAMA_MODEL__
HOST=127.0.0.1
PORT=8000
CORS_ORIGINS=__CORS_ORIGINS__
MAX_UPLOAD_MB=25
SESSION_TTL_SECONDS=7200
MAX_SESSIONS=50
CODE_TIMEOUT_SECONDS=15
MAX_CODE_RETRIES=2
APP_ENV
chmod 600 /etc/excel-agent.env

cat >/etc/systemd/system/excel-agent.service <<'APP_UNIT'
[Unit]
Description=Excel AI Agent backend
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
User=excelagent
Group=excelagent
WorkingDirectory=/opt/excel-agent/backend
EnvironmentFile=/etc/excel-agent.env
ExecStart=/opt/excel-agent/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
APP_UNIT

systemctl daemon-reload
systemctl enable excel-agent

# Wait for Ollama, then pull the model and create an 8k-context variant.
for i in $(seq 1 90); do
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
ollama pull __OLLAMA_MODEL__

cat >/tmp/excel-agent.Modelfile <<'MODELFILE'
FROM __OLLAMA_MODEL__
PARAMETER num_ctx 8192
MODELFILE
ollama create __CUSTOM_MODEL__ -f /tmp/excel-agent.Modelfile
sed -i 's|^OLLAMA_MODEL=.*|OLLAMA_MODEL=__CUSTOM_MODEL__|' /etc/excel-agent.env
systemctl restart excel-agent

# Give the t3.medium some memory headroom for the model and pandas.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon /swapfile || true
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Caddy is started after the Elastic IP is associated by the provisioner.
cat >/etc/caddy/Caddyfile <<'CADDYFILE'
__PUBLIC_IP__.sslip.io {
  encode zstd gzip
  reverse_proxy 127.0.0.1:8000 {
    flush_interval -1
  }
}
CADDYFILE

touch /opt/excel-agent/ready
echo "bootstrap complete"
