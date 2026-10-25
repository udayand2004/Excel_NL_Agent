# AWS backend deployment

This deploys the FastAPI backend to a single EC2 instance in the project's
selected Region (`us-west-2`). Groq (`openai/gpt-oss-120b`) is the primary LLM;
Ollama with `qwen2.5:3b` is installed as the automatic fallback. Caddy provides
HTTPS.

The Vercel frontend is deployed separately and points at the resulting
`https://<elastic-ip>.sslip.io` URL.

## What gets created

| Resource | Purpose | Approximate cost |
| --- | --- | --- |
| `t3.medium` EC2 | FastAPI + Groq calls + Ollama fallback | ~$30/month |
| 30 GB gp3 EBS | OS, Python packages, Ollama model | ~$2.40/month |
| Elastic IP | Stable HTTPS hostname | free while attached |
| S3 bucket | Deployment artifact | pennies |
| SSM SecureString | Groq API key | free |
| IAM role/profile | S3 read + SSM access | free |
| Security group | ports 80/443 (and SSH from your current IP) | free |

The account's $100 credits cover roughly three months of this setup. Add a
budget in **AWS Settings > Billing** and in the AWS Billing console if you want
hard cost alerts.

CPU inference is not instant. The first question after the model is unloaded
can take 30-90 seconds; `OLLAMA_KEEP_ALIVE=30m` keeps it warm between questions.

## Prerequisites

- AWS CLI v2 authenticated with `aws login --profile aws-agent`
- Permissions to create EC2, IAM, S3, and SSM resources
- `curl`, `tar`, and `python3` on the machine running the provisioner

## Deploy

From the repository root:

```bash
AWS_PROFILE=aws-agent AWS_REGION=us-west-2 ./deploy/aws/provision.sh
```

The script builds `dist/excel-ai-agent.tar.gz`, uploads it to S3, stores the
Groq key from `backend/.env` in SSM Parameter Store, creates the IAM
role/security group/Elastic IP, launches the instance, waits for bootstrap and
the Ollama model download, starts Caddy, and polls `/health`.

If `GROQ_API_KEY` is not set in `backend/.env` (or the environment), the deploy
still succeeds but the backend runs with Ollama only.

The final URL and resource IDs are written to `deploy/aws/.state` (gitignored).

## Point the frontend at the backend

Edit `frontend/config.js`:

```js
window.EXCEL_AGENT_API = 'https://<elastic-ip>.sslip.io';
```

Commit and push; Vercel will redeploy automatically if the GitHub integration
is connected. Then tighten `CORS_ORIGINS` on the instance to the Vercel domain
instead of `*`:

```bash
source deploy/aws/.state
aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["sed -i \"s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://your-app.vercel.app|\" /etc/excel-agent.env","systemctl restart excel-agent"]'
```

## Update the backend

After changing backend code:

```bash
AWS_PROFILE=aws-agent AWS_REGION=us-west-2 ./deploy/aws/update.sh
```

This re-uploads the checkout, extracts it in `/opt/excel-agent`, reinstalls
requirements, and restarts `excel-agent`.

## Logs and debugging

Open a shell without exposing SSH:

```bash
source deploy/aws/.state
aws ssm start-session --target "$INSTANCE_ID"
```

Then inspect:

```bash
journalctl -u excel-agent -f
journalctl -u ollama -f
journalctl -u caddy -f
tail -100 /var/log/excel-agent-bootstrap.log
```

If SSM is unavailable, the provisioner also creates an SSH key at the path in
`deploy/aws/.state` and opens port 22 only to your public IP at deploy time.

## Tear down

```bash
AWS_PROFILE=aws-agent AWS_REGION=us-west-2 ./deploy/aws/teardown.sh
```

This terminates the instance, releases the Elastic IP, deletes the security
group, IAM role/profile, S3 bucket, and the local SSH key. The CodeCommit mirror
(if created) is intentionally left alone.

## Security notes

- The API is public and has no user authentication. Do not upload confidential
  spreadsheets until you add an authentication layer.
- Groq receives the data description, sample rows, and analysis result previews
  needed to answer a question. Do not use this deployment for regulated data
  without reviewing that data flow.
- The Groq key is stored as an SSM SecureString and injected into the backend
  environment at boot; it is never written to the deployment artifact.
- Ollama listens only on `127.0.0.1` and is never exposed directly.
- The FastAPI process listens on `127.0.0.1:8000`; only Caddy is public.
- Uploaded files are written to a temporary file, profiled, and deleted.
- Sessions are in memory and disappear when the service restarts.
- `CORS_ORIGINS=*` is used for the first deploy. Restrict it to the Vercel
  origin afterward as shown above.
