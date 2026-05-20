# Phase 3 — EC2 + ALB Deployment (Hands-On Complete Walkthrough)

**Status:** ✅ Complete
**Completed:** May 14, 2026
**Time taken:** ~1 weekend
**Region:** eu-north-1 (Frankfurt)

---

## Overview

Deployed Phase 2 code (FastAPI producer + Python worker, SQS + SNS) to real AWS infrastructure. Producer and worker now run as separate EC2 instances communicating entirely through AWS services. Public access via Application Load Balancer (ALB).

**End state:** Live URL → `http://task-processor-alb-xxx.eu-north-1.elb.amazonaws.com/jobs` (on your resume).

---

## Prerequisites Checklist

- [x] AWS account with billing alert at $5 (Budget created)
- [x] AWS CLI configured (`aws sts get-caller-identity` works)
- [x] Phase 2 code pushed to GitHub
- [x] SQS queue (`task-processor-jobs`) exists in eu-north-1
- [x] SNS topic (`task-processor-notifications`) exists in eu-north-1
- [x] IAM fundamentals understood: user vs role, trust vs permissions, Deny wins
- [x] Local SSH key access available

---

## Step-by-Step Deployment

### Step 1: Create IAM Roles

**Concept:** EC2 instances need permission to call SQS and SNS. We create roles with least-privilege policies, not users with access keys.

**Create worker role:**

1. AWS Console → IAM → Roles → **Create role**
2. Trusted entity type: **AWS service** → **EC2** → Next
3. Permissions: skip
4. Role name: `task-processor-worker-role` → Create role
5. Click into the role → **Add permissions** → **Create inline policy** → JSON tab
6. Paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:eu-north-1:YOUR-ACCOUNT-ID:task-processor-jobs"
    },
    {
      "Effect": "Allow",
      "Action": ["sns:Publish"],
      "Resource": "arn:aws:sns:eu-north-1:YOUR-ACCOUNT-ID:task-processor-notifications"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:CreateLogGroup"
      ],
      "Resource": "arn:aws:logs:eu-north-1:YOUR-ACCOUNT-ID:log-group:/task-processor/*"
    }
  ]
}
```

7. Replace `YOUR-ACCOUNT-ID` with your 12-digit account ID (top-right → Account)
8. Policy name: `worker-policy` → Create policy

**Create producer role (same steps, different policy):**

1. IAM → Roles → Create role
2. Trusted entity: AWS service → EC2 → Next
3. Role name: `task-processor-producer-role` → Create role
4. Add permissions → Create inline policy → JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:eu-north-1:YOUR-ACCOUNT-ID:task-processor-jobs"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:CreateLogGroup"
      ],
      "Resource": "arn:aws:logs:eu-north-1:YOUR-ACCOUNT-ID:log-group:/task-processor/*"
    }
  ]
}
```

5. Policy name: `producer-policy` → Create policy

**Why:** IAM roles auto-issue temporary credentials to EC2 instances. No access keys on the box. Least-privilege per service.

---

### Step 2: Create EC2 Key Pair

**Purpose:** SSH access to instances.

1. EC2 Console → Key Pairs → **Create key pair**
2. Name: `task-processor-key`
3. Type: RSA
4. Format: **.pem** (macOS/Linux) or .ppk (Windows PuTTY)
5. Create → browser downloads file
6. Move to home:

```bash
mv ~/Downloads/task-processor-key.pem ~/.ssh/
chmod 400 ~/.ssh/task-processor-key.pem
```

**Why:** `chmod 400` = read-only, protects private key from accidental deletion.

---

### Step 3: Create Security Groups

**Worker SG (worker-sg):**

1. EC2 → Security Groups → **Create security group**
2. Name: `worker-sg`
3. Description: `Worker EC2 — SSH only`
4. VPC: default
5. Inbound rules:
   - SSH | Port 22 | Source: My IP (auto-detected)
6. Outbound: default (all allowed)
7. Create

**Producer SG (producer-sg):**

1. Create security group
2. Name: `producer-sg`
3. Description: `Producer EC2 — SSH + FastAPI`
4. Inbound rules:
   - SSH | Port 22 | Source: My IP
   - Custom TCP | Port 8000 | Source: 0.0.0.0/0 (temporary, tightened in Step 9)
5. Create

**ALB SG (alb-sg):** Created during ALB setup (Step 8).

---

### Step 4: Launch Worker EC2

1. EC2 → Instances → **Launch instances**
2. Name: `task-processor-worker`
3. AMI: Amazon Linux 2023 (free tier)
4. Instance type: t2.micro or t3.micro (free tier)
5. Key pair: `task-processor-key`
6. Network settings:
   - VPC: default
   - Auto-assign public IP: **Enable**
   - Security group: `worker-sg`
7. Storage: 8 GiB gp3
8. Advanced details → IAM instance profile: `task-processor-worker-role`
9. **Launch instance**

Wait ~1 minute. Note the **Public IPv4 address** (e.g. `3.248.xxx.xxx`).

**Verify SSH + IAM role:**

```bash
ssh -i ~/.ssh/task-processor-key.pem ec2-user@<public-ip>
aws sts get-caller-identity
```

Should return the worker role ARN, not your user.

---

### Step 5: Deploy Worker Code

SSH'd into worker instance:

```bash
sudo dnf update -y
sudo dnf install -y git python3.11 python3.11-pip
git clone https://github.com/yourusername/cloud-task-processor.git
cd cloud-task-processor
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Create .env:**

```bash
nano .env
```

Paste:

```env
USE_LOCAL_QUEUE=false
USE_LOCAL_NOTIFIER=false
AWS_REGION=eu-north-1
SQS_QUEUE_URL=https://sqs.eu-north-1.amazonaws.com/YOUR-ACCOUNT-ID/task-processor-jobs
SNS_TOPIC_ARN=arn:aws:sns:eu-north-1:YOUR-ACCOUNT-ID:task-processor-notifications
LOG_LEVEL=INFO
```

Save: Ctrl+O, Enter, Ctrl+X.

**Create systemd service:**

```bash
sudo nano /etc/systemd/system/task-worker.service
```

Paste:

```ini
[Unit]
Description=Cloud Task Processor Worker
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/cloud-task-processor
EnvironmentFile=/home/ec2-user/cloud-task-processor/.env
ExecStart=/home/ec2-user/cloud-task-processor/.venv/bin/python -m worker.worker
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable task-worker
sudo systemctl start task-worker
sudo systemctl status task-worker
```

**Verify:**

```bash
sudo journalctl -u task-worker -f
```

Should show: `Worker started. Polling every 5 seconds.`

---

### Step 6: Launch Producer EC2

Same as worker, with these differences:

1. Name: `task-processor-producer`
2. Security group: `producer-sg`
3. IAM instance profile: `task-processor-producer-role`

Launch, note public IP. SSH in:

```bash
ssh -i ~/.ssh/task-processor-key.pem ec2-user@<public-ip>
aws sts get-caller-identity
```

---

### Step 7: Deploy Producer Code

SSH'd into producer:

```bash
sudo dnf update -y
sudo dnf install -y git python3.11 python3.11-pip
git clone https://github.com/yourusername/cloud-task-processor.git
cd cloud-task-processor
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Create .env** (same as worker):

```bash
nano .env
```

```env
USE_LOCAL_QUEUE=false
USE_LOCAL_NOTIFIER=false
AWS_REGION=eu-north-1
SQS_QUEUE_URL=https://sqs.eu-north-1.amazonaws.com/YOUR-ACCOUNT-ID/task-processor-jobs
SNS_TOPIC_ARN=arn:aws:sns:eu-north-1:YOUR-ACCOUNT-ID:task-processor-notifications
LOG_LEVEL=INFO
```

**Create systemd service:**

```bash
sudo nano /etc/systemd/system/task-producer.service
```

Paste:

```ini
[Unit]
Description=Cloud Task Processor Producer
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/cloud-task-processor
EnvironmentFile=/home/ec2-user/cloud-task-processor/.env
ExecStart=/home/ec2-user/cloud-task-processor/.venv/bin/uvicorn producer.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable task-producer
sudo systemctl start task-producer
sudo systemctl status task-producer
```

**Verify:**

```bash
curl http://localhost:8000/health
```

Should return: `{"status":"healthy","queue":"sqs"}`

---

### Step 8: Create Target Group

**Purpose:** ALB needs to know where to route traffic.

1. EC2 → Target Groups → **Create target group**
2. Type: **Instances**
3. Name: `producer-tg`
4. Protocol: HTTP, Port: 8000
5. VPC: default
6. Health check path: `/health`
7. Next
8. Register targets: select producer instance, port 8000
9. **Create target group**

---

### Step 9: Create Application Load Balancer

1. EC2 → Load Balancers → **Create load balancer**
2. Type: **Application Load Balancer**
3. Name: `task-processor-alb`
4. Scheme: **Internet-facing**
5. VPC: default
6. Subnets: select 2+ availability zones
7. Security groups: **Create new** → name `alb-sg` → Inbound: HTTP 80 + HTTPS 443 from 0.0.0.0/0 → Create
8. Listener: HTTP:80 → Forward to `producer-tg`
9. **Create load balancer**

Wait 2-3 minutes for status Active. Copy the **DNS name** (e.g. `task-processor-alb-xxx.eu-north-1.elb.amazonaws.com`).

**Test ALB:**

```bash
curl http://<alb-dns-name>/health
```

Should return healthy.

---

### Step 10: End-to-End Test

From your laptop:

```bash
curl -X POST http://<alb-dns-name>/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://news.ycombinator.com","notify_email":"moezalikhan@hotmail.com"}'
```

**What happens:**
1. ALB receives POST, routes to producer on port 8000
2. Producer returns `job_id` immediately (async)
3. Producer queues message to SQS
4. Worker polls SQS every 5 seconds, picks up message
5. Worker fetches URL, extracts metadata
6. Worker publishes to SNS
7. SNS sends email to `moezalikhan@hotmail.com`

**Watch worker logs:**

SSH into worker:

```bash
sudo journalctl -u task-worker -f
```

You should see:
```
Processing job <uuid> for URL ...
Extracted: title=..., words=..., links=...
SNS: published '...'
```

**Check email:** Should arrive within 30 seconds.

---

### Step 11: Tighten Security + Stop Instances

**Tighten producer-sg:**

1. EC2 → Security Groups → `producer-sg`
2. Edit inbound rules
3. Delete the old 0.0.0.0/0 rule on port 8000
4. Add new rule: Custom TCP, port 8000, source = `alb-sg` (select from dropdown)
5. Save

Now only the ALB can reach the producer directly.

**Stop instances (save costs):**

1. EC2 → Instances
2. Select both producer + worker (checkboxes)
3. Instance state → **Stop**

Stopped = ~$0/month compute. Restart in one click when needed.

---

## Architecture Summary

```
Internet user
    ↓ (HTTPS)
ALB (port 80)
    ↓ (HTTP:8000, internal)
Producer EC2 (FastAPI)
    ↓ (boto3 SQS send)
SQS Queue
    ↓ (boto3 SQS receive)
Worker EC2 (Python poll loop)
    ↓ (boto3 SNS publish)
SNS Topic
    ↓ (Email)
User inbox
```

Each component has an IAM role granting only the permissions it needs:
- Producer: `sqs:SendMessage`
- Worker: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sns:Publish`

---

## Hands-On Learnings

**What worked:**

- IAM roles auto-provide temporary credentials to EC2 — no access keys stored on instances
- systemd services auto-restart on crash, survive reboots
- ALB provides stable DNS (IPs change, but `task-processor-alb-xxx.eu-north-1.elb.amazonaws.com` is permanent)
- Health check path `/health` lets ALB detect if producer is down
- SQS/SNS decouple producer and worker — can scale each independently

**What broke (and how we fixed it):**

1. **git: command not found** → installed with `sudo dnf install -y git`
2. **python3.11: command not found** → installed with `sudo dnf install -y python3.11 python3.11-pip`
3. **SSH using git@github.com** → used HTTPS clone instead (no SSH keys on instance)
4. **SNS email not arriving** → subscription deactivated due to email provider rejection; solved with different email provider (hotmail works, others may not)
5. **Port 8000 source was 0.0.0.0/0** → tightened to `alb-sg` so only ALB can reach producer

---

## AWS Resources Created

| Resource | Name | Region | Purpose |
|----------|------|--------|---------|
| EC2 Instance | task-processor-worker | eu-north-1b | Worker process |
| EC2 Instance | task-processor-producer | eu-north-1b | FastAPI producer |
| IAM Role | task-processor-worker-role | global | Worker permissions |
| IAM Role | task-processor-producer-role | global | Producer permissions |
| Security Group | worker-sg | eu-north-1 | SSH only to worker |
| Security Group | producer-sg | eu-north-1 | SSH + port 8000 from ALB |
| Security Group | alb-sg | eu-north-1 | HTTP:80, HTTPS:443 from internet |
| ALB | task-processor-alb | eu-north-1 | Public entry point |
| Target Group | producer-tg | eu-north-1 | Routes to producer EC2 |
| Key Pair | task-processor-key | eu-north-1 | SSH access |

---

## Resume Claim

After Phase 3:

> Built a distributed URL processing service on AWS using FastAPI (producer) behind an Application Load Balancer, Python worker fleet on EC2, SQS for asynchronous task queuing, and SNS for email notifications. Implemented least-privilege IAM roles per service, systemd service management for auto-restart and boot persistence, and end-to-end integration testing. Deployed to eu-north-1 with estimated cost ~$15/month running 24/7.

**Real AWS URL:** `http://task-processor-alb-xxx.eu-north-1.elb.amazonaws.com/jobs`

---

## Commands Reference

**SSH into instance:**
```bash
ssh -i ~/.ssh/task-processor-key.pem ec2-user@<public-ip>
```

**Check systemd service:**
```bash
sudo systemctl status task-worker
sudo systemctl status task-producer
sudo journalctl -u task-worker -f   # live logs
```

**Test producer health:**
```bash
curl http://localhost:8000/health
```

**Test full flow:**
```bash
curl -X POST http://<alb-dns>/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","notify_email":"you@email.com"}'
```

**Stop instances (save costs):**
```bash
# Via CLI (alternative to console)
aws ec2 stop-instances --instance-ids i-xxx i-yyy --region eu-north-1
```

**Start instances:**
```bash
aws ec2 start-instances --instance-ids i-xxx i-yyy --region eu-north-1
```

---

## What's Next

- **Phase 4:** Dockerize both services, push images to ECR
- **Phase 5:** Replace EC2 with ECS on Fargate (serverless containers)
- **Phase 6:** Lambda variant of worker
- **Phase 14:** RDS persistence (job state now in-memory only)
- **Phase 16:** Airflow orchestration
- **Phase 17:** dbt + warehouse

---

## Common Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| `Unable to locate credentials` | EC2 missing IAM role | Attach instance profile with role |
| `git: command not found` | Package not installed | `sudo dnf install -y git` |
| `python3.11: command not found` | Python not installed | `sudo dnf install -y python3.11 python3.11-pip` |
| SSH returns `Permission denied (publickey)` | Wrong key or instance not ready | Verify key pair name, wait for instance to boot |
| ALB health check failing | Producer not listening or path wrong | SSH and verify `curl localhost:8000/health` works |
| SNS email not arriving | Provider rejection or wrong subscription | Recreate subscription, try different email (hotmail/gmail) |
| Worker not picking up messages | IAM policy missing or wrong queue URL | Verify role has `sqs:ReceiveMessage`, check .env queue URL |

---

*End of Phase 3 deployment guide. Save this for future reference or to onboard new team members.*
