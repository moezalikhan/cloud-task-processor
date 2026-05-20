# Phase 5 — ECS Fargate Migration

**Status:** ✅ Complete
**Completed:** May 20, 2026
**Time taken:** ~1 long session (single day, three working sessions back-to-back)
**Region:** eu-north-1 (Stockholm)

---

## What was built

Migrated the Phase 3/4 producer + worker from two hand-configured EC2 instances to ECS Fargate — AWS's serverless container runtime. Same Docker images from Phase 4, same SQS/SNS/ALB plumbing, no servers to SSH into, patch, or restart.

The deploy unit is now a **task** (a running container that ECS schedules and supervises), not a server (Phase 3) or even a server-with-an-image (Phase 4). ECS Service objects watch over each container and auto-restart on crash, register IPs in the target group, and handle rolling deploys.

---

## Architecture changes from Phase 4

| Component | Phase 4 | Phase 5 |
|-----------|---------|---------|
| Compute | EC2 instances with `docker run` (in theory; Phase 3 was still systemd) | ECS Fargate tasks |
| Process supervision | systemd | ECS service controller |
| IAM model | One EC2 instance profile per service | Two roles per service: execution role (shared) + task role (per service) |
| Target group type | `instances` (`producer-tg`) | `ip` (`producer-tg-ip`) |
| Deploy artifact | ECR image (already true since Phase 4) | ECR image + task definition JSON |
| Network mode | Host networking on EC2 | `awsvpc` (each task gets its own ENI + IP) |
| OS patching | My problem | AWS's problem |
| SSH access | Required | None |
| Image platform | Multi-platform OCI index (Bug #6 from Phase 4) | Single-platform `linux/amd64` |

---

## Files created / modified

**New:**
- `infrastructure/ecs/worker-task-def.json` — Fargate task definition for worker service
- `infrastructure/ecs/producer-task-def.json` — Fargate task definition for producer service
- `PHASE_5.md` — this file

**Modified:**
- `PHASE_5_DEPLOYMENT_GUIDE.md` — walkthrough used to execute the migration (separate spec file, kept alongside completion notes)

**Unchanged (intentional):**
- All application code in `producer/` and `worker/`
- `producer/Dockerfile`, `worker/Dockerfile`
- `requirements.txt`
- SQS queue, SNS topic, ALB, ECR repos

Phase 5 was a pure infrastructure migration. Zero application code changed.

---

## AWS resources created

| Resource | Name | Purpose |
|----------|------|---------|
| ECS Cluster | `task-processor-cluster` | Logical grouping for Fargate tasks |
| CloudWatch Log Group | `/ecs/task-processor-producer` | Producer task logs, 7-day retention |
| CloudWatch Log Group | `/ecs/task-processor-worker` | Worker task logs, 7-day retention |
| IAM Role | `ecsTaskExecutionRole` | Used by ECS to pull ECR images + ship logs (shared across both services) |
| IAM Role | `task-processor-producer-task-role` | In-container permissions for producer (SQS SendMessage) |
| IAM Role | `task-processor-worker-task-role` | In-container permissions for worker (SQS ReceiveMessage + SNS Publish) |
| Service-linked Role | `AWSServiceRoleForECS` | Required by ECS itself, account-wide (one-time setup) |
| Security Group | `fargate-producer-sg` | Inbound 8000 from `alb-sg` |
| Security Group | `fargate-worker-sg` | Outbound only (worker initiates all connections) |
| ECS Task Definition | `task-processor-worker:1` | 256 CPU / 512 MB, image from ECR, env vars baked in |
| ECS Task Definition | `task-processor-producer:1` | Same shape, plus port 8000 mapping |
| ECS Service | `task-processor-worker-service-r1e76pmo` | Desired count 1, public subnets, public IP enabled |
| ECS Service | `task-processor-producer-svc` | Desired count 1, wired to `producer-tg-ip` |
| Target Group | `producer-tg-ip` | IP target type, port 8000, `/health` health check |

---

## Bugs hit and fixed

### 1. ECS service-linked role not created (cluster creation failed)

**Symptom:** First attempt to create the ECS cluster from the AWS Console failed with:
```
Resource handler returned message: "Invalid request provided: CreateCluster Invalid Request:
Unable to assume the service linked role. Please verify that the ECS service linked role exists."
```

**Root cause:** ECS requires an account-wide service-linked role (`AWSServiceRoleForECS`) to exist before it can manage any clusters. AWS normally auto-creates this role on first ECS action, but the auto-creation failed silently for me — likely a transient IAM propagation issue combined with the fact that I was attempting the action while signed in to the **root** account in the browser, which has a slightly different code path for service-linked role provisioning than a regular IAM user.

**Fix:** Manually create the service-linked role via CLI (as `admin-moez`, not root):
```bash
aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com
```

**Lesson:** Never use the root account for day-to-day work. Console actions taken while logged in as root use root's IAM principal under the hood, even though the UI looks identical to a regular admin user's experience. Phase 3 and 4 were done entirely as `admin-moez`; doing Phase 5 as root introduced a subtle behavioral difference that took half an hour to diagnose.

---

### 2. CloudFormation leftover stack blocking cluster recreation

**Symptom:** After fixing the service-linked role and retrying, cluster creation failed again with:
```
A CloudFormation stack already exists for a failed cluster with the same name.
Choose a different cluster name or delete the Infra-ECS-Cluster-task-processor-cluster-67c7f642 stack
through the CloudFormation console.
```

**Root cause:** ECS uses CloudFormation behind the scenes to provision clusters. The earlier failed attempt (Bug #1) left behind a half-created CloudFormation stack in `CREATE_FAILED` state. CloudFormation refuses to create a second stack with the same logical name, even though the underlying cluster doesn't exist.

**Fix:** Delete the failed stack first:
1. CloudFormation Console → eu-north-1
2. Find `Infra-ECS-Cluster-task-processor-cluster-67c7f642` (CREATE_FAILED status)
3. Delete → wait ~60 seconds for it to disappear
4. Retry cluster creation

**Lesson:** AWS console wizards often hide CloudFormation underneath. When a wizard-driven creation fails, the leftover stack is usually the first thing to clean up before retrying. This is a pattern that recurs across ECS, EKS, OpenSearch, and other services.

---

### 3. IAM user denied access to Billing console

**Symptom:** After signing in as `admin-moez`, trying to view the Billing and Cost Management console returned an access-denied error, despite the user having `AdministratorAccess`.

**Root cause:** AWS has an **account-level** setting separate from IAM permissions called "IAM user and role access to Billing information." By default it is OFF. When OFF, IAM users cannot see billing data regardless of which IAM policies they have attached. Only the root account can toggle this setting.

**Wrong attempted fix:** Created and attached an additional `Billing` policy to `admin-moez`. This had no effect — the gate is at the account level, not the policy level.

**Correct fix:**
1. Sign in as **root**
2. Top-right account menu → Account
3. Scroll to "IAM user and role access to Billing information"
4. Edit → check "Activate IAM access" → Update
5. Sign out, sign back in as `admin-moez`. Billing is now accessible.

**Lesson:** Some AWS settings are gated at the account level, not by IAM. The Billing console is the most common one. Activate this on day one of any new AWS account so you don't trip on it later.

---

### 4. Target group dropdown disabled in ECS service wizard

**Symptom:** While creating the producer ECS service, the load balancing section showed `producer-tg-ip` in the target group dropdown but it was **disabled (greyed out)** with a tooltip explaining "this target group is not associated with any load balancer listener."

**Root cause:** The current ECS service wizard requires the target group to *already* be wired up to an ALB listener before the wizard will allow you to select it. This is a behavioral change from earlier versions of the ECS console where the wizard would associate the target group for you. The walkthrough I was following (which I had written based on older docs) assumed the old behavior.

**Workaround attempted but rejected:** Associating the target group with the production HTTP:80 listener *before* the Fargate task was running would have caused the ALB to route real traffic to an empty target group, producing 503 errors for any user during the migration window. Unsafe.

**Correct fix (the "parallel listener" pattern):**
1. Added a temporary HTTP:8080 listener to `task-processor-alb`, defaulting to `producer-tg-ip`
2. Added port 8080 to `alb-sg` inbound rules (temporary)
3. With the listener association now in place, the ECS service wizard let me select `producer-tg-ip`
4. Created the producer ECS service; Fargate task came up healthy
5. Verified Fargate producer worked end-to-end on port 8080 while EC2 producer continued serving real traffic on port 80
6. Did the actual cutover (Step 15) by changing the port 80 listener's default action to `producer-tg-ip`
7. Cleanup: deleted the temporary 8080 listener and the port 8080 inbound rule

**Lesson:** Real production migrations always use a parallel-path pattern (canary, blue/green, or a second listener like this) so the new path can be verified before any traffic shifts to it. The "instant switch" pattern in my original walkthrough was unsafe; the AWS console change actually forced me into a safer approach. Worth keeping this pattern in mind for Phase 6 onward — never assume "the change is instant and reversible" is safe without a way to validate before swapping.

---

### 5. ALB returning 503 during Step 10 smoke test

**Symptom:** After deploying the Fargate worker (Session 2 done) and submitting a job via curl to the ALB, got:
```html
<html>
<head><title>503 Service Temporarily Unavailable</title></head>
<body><center><h1>503 Service Temporarily Unavailable</h1></center></body>
</html>
```

**Root cause:** Misalignment between the walkthrough's assumptions and my actual state. The walkthrough assumed the EC2 producer was still running during Session 2 — the smoke test was supposed to hit the EC2 producer (still on `producer-tg`), have it queue to SQS, and let the Fargate worker pick up the message in parallel with the EC2 worker.

In reality, I had stopped the EC2 instances between Phase 4 and Phase 5 (cost-saving habit). With EC2 stopped, `producer-tg` had zero healthy targets, ALB had nothing to forward to, returned 503.

**Fix:** Started the EC2 producer back up. ALB had a healthy target again. Smoke test worked. The EC2 producer stayed running for the rest of Phase 5 until the cutover in Step 15.

**Lesson:** Walkthroughs are written with assumptions about state that may not match reality. When something doesn't work, check the upstream prerequisite is actually in the state the doc expects before debugging deeper. A 503 from an ALB is almost always "no healthy targets" — quick fix is to check the target group's Targets tab first.

**Alternative I considered:** Instead of restarting EC2, I could have skipped Step 10's curl entirely and tested the Fargate worker by sending a message directly to SQS via CLI:
```bash
aws sqs send-message --queue-url ... --message-body '{"job_id":"test-fargate-1","url":"...","notify_email":"..."}'
```
This bypasses the producer entirely. Cleaner smoke test for the worker in isolation. Worth using this approach for future "verify just the worker" steps.

---

### 6. ECS service name got auto-suffixed with random characters

**Symptom:** Created the worker service intending the name `task-processor-worker-svc`. The console accepted my input but the created service appeared as `task-processor-worker-service-r1e76pmo`.

**Root cause:** The current ECS console has a default deployment configuration that appends a random suffix to service names — likely a side effect of an internal "service revision" or "blue/green deployment" feature being on by default. The toggle to disable this in the wizard is not obvious; it may only be controllable via CLI when creating the service.

**Decision:** Left as-is. Cosmetic issue only. The service works fine, and renaming an ECS service requires deleting and recreating it (downtime + IAM/networking re-wiring). Not worth the effort for an aesthetics issue.

**Lesson:** Modern AWS console wizards are increasingly opinionated and append things to your inputs. If exact naming matters (e.g. for Terraform import compatibility or scripting), create resources via CLI from day one. For learning projects, accept the cosmetic noise and move on.

---

### 7. Image manifest format incompatibility (carried over from Phase 4)

**Symptom:** While preparing for Phase 5, attempted to manually scan the producer ECR image:
```
An artifact with media type 'application/vnd.oci.image.index.v1+json' cannot be scanned.
```
Then after rebuilding with `--platform linux/amd64`:
```
exporting attestation manifest sha256:1298693a...
exporting manifest list sha256:a690ad09...
```
Image was still a manifest list, not single-platform.

**Root cause:** Docker Desktop's buildx, even with `--platform linux/amd64` specified, defaults to producing a **manifest list** wrapping the image plus a **provenance attestation**. ECR's basic scanner only handles single-platform images, not manifest lists.

**Fix:** Added `--provenance=false` to the buildx command:
```bash
docker buildx build --platform linux/amd64 --provenance=false \
  -f producer/Dockerfile \
  -t $ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-producer:latest \
  --push .
```
This produced a true single-platform manifest:
```
MediaType: application/vnd.docker.distribution.manifest.v2+json
```

**Note:** Even after the fix, I couldn't actually run the scan because ECR's basic scanner has a quota of one scan per image per 24 hours, and my earlier failed attempts had exhausted it (`LimitExceededException: The scan quota per image has been exceeded`). Not a blocker — Phase 5 doesn't depend on scanning, just on the image being Fargate-compatible. The manifest format check was sufficient verification.

**Lesson:** Modern Docker tooling defaults toward multi-platform builds and supply-chain attestations because they're best practice for production. AWS basic services are still catching up. When pushing to ECR for use with basic scanning, explicitly disable manifest lists and provenance unless you've upgraded to Enhanced Scanning.

---

### 8. Placeholder substitution mistake in buildx command

**Symptom:** First attempt at the buildx push command failed with:
```
ERROR: failed to push ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-producer:latest:
unexpected status from HEAD request to https://ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/v2/...: 401 Unauthorized
```

**Root cause:** Typo. I copied the literal string `ACCOUNT-ID` from the walkthrough command instead of substituting it with my actual 12-digit AWS account ID. Docker tried to push to a non-existent registry hostname.

**Fix:** Used the shell variable pattern to make substitution automatic:
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
docker buildx build --platform linux/amd64 --provenance=false \
  -f producer/Dockerfile \
  -t $ACCOUNT_ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-producer:latest \
  --push .
```

**Lesson:** When walkthroughs contain placeholders like `ACCOUNT-ID`, `YOUR-REGION`, etc., always resolve them dynamically (`$(aws sts get-caller-identity ...)`) rather than searching-and-replacing. The shell substitution pattern is self-documenting, repeatable, and doesn't suffer from "I forgot to update one instance" bugs.

---

## End-to-end test (working, after cutover)

**Final state verification on port 80 (production listener, post-cutover):**

```bash
$ curl http://task-processor-alb-944861494.eu-north-1.elb.amazonaws.com/health
{"status":"healthy","queue":"sqs"}

$ curl -X POST http://task-processor-alb-944861494.eu-north-1.elb.amazonaws.com/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://news.ycombinator.com","notify_email":"moezalikhan@hotmail.com"}'
{"job_id":"0807aa39-29a2-46b6-bf9c-56d26ccc2bce","status":"queued","submitted_at":"2026-05-20T07:34:35.435312+00:00"}
```

**Fargate worker CloudWatch log stream:**
```
12:28  INFO:__main__:Worker started. Polling every 5 seconds.
12:34  INFO:__main__:Received payload: {'job_id': '0807aa39-...', 'url': 'https://news.ycombinator.com/', ...}
12:34  INFO:__main__:Processing job 0807aa39-... for URL https://news.ycombinator.com/
12:34  INFO:__main__:Job 0807aa39-... completed successfully. title=Hacker News, words=731, links=229
12:34  INFO:worker.notifier:SNS: published 'Job 0807aa39-... completed'
12:34  INFO:producer.queue:SQS: deleted message
```

SNS email received. ALB → Fargate producer → SQS → Fargate worker → SNS chain fully verified.

---

## Architecture after Phase 5

```
Internet user
    ↓ (HTTP)
ALB :80 (task-processor-alb)
    ↓ (forwards to producer-tg-ip)
Fargate Producer Task                  [/ecs/task-processor-producer]
   (image: ECR producer:latest, linux/amd64)
   (256 CPU / 512 MB, public subnet, ENI with public IP)
    ↓ (boto3 SQS send via task role)
SQS Queue (task-processor-jobs)
    ↓ (boto3 SQS receive via task role)
Fargate Worker Task                    [/ecs/task-processor-worker]
   (image: ECR worker:latest, linux/amd64)
   (256 CPU / 512 MB, public subnet)
    ↓ (boto3 SNS publish via task role)
SNS Topic (task-processor-notifications)
    ↓
Email subscriber (moezalikhan@hotmail.com)
```

No EC2 instances. No SSH. No systemd. No `dnf install`. No `git pull` on a server.

---

## Cost snapshot

**Recurring (while running):**
- 2 × Fargate tasks at 0.25 vCPU + 0.5 GB, 24/7: ~$18–20/month
- ALB (cannot be stopped, only deleted): ~$16/month baseline + negligible LCU
- ECR storage (~300 MB total): ~$0.03/month
- CloudWatch Logs (with 7-day retention): negligible at this traffic level
- SQS / SNS: negligible
- **Total running 24/7: ~$35/month**

**Cost-control posture chosen for the project:**
- Fargate desired count set to 0 between active sessions (saves ~$20/month)
- ALB stays running (~$16/month is the floor cost of "live AWS URL on resume")
- EC2 instances stopped (Phase 3 leftovers, kept for ~1 week as rollback insurance, then terminated)
- **Idle cost when not actively demoing: ~$16/month**

**To scale Fargate down to zero (no compute charges):**
```bash
aws ecs update-service --cluster task-processor-cluster \
  --service task-processor-worker-service-r1e76pmo --desired-count 0 --region eu-north-1
aws ecs update-service --cluster task-processor-cluster \
  --service task-processor-producer-svc --desired-count 0 --region eu-north-1
```

**To scale back up before applications batch:**
Same commands with `--desired-count 1`. Tasks come up in ~2 minutes.

---

## Security posture

What's protecting the workload:

- **Task-level IAM roles** — producer and worker have separate task roles with least-privilege policies. Producer can only `sqs:SendMessage` to one queue. Worker can only `sqs:ReceiveMessage`/`DeleteMessage` and `sns:Publish` to specific resources. Either being compromised limits blast radius.
- **No long-lived credentials on the workload** — Fargate task roles issue temporary credentials automatically via the ECS metadata endpoint. No `~/.aws/credentials` on the running container.
- **Security group segmentation** — producer security group only allows port 8000 from `alb-sg`. Worker security group has no inbound rules at all. ALB is the only public ingress.
- **Image scan-on-push enabled at the ECR repo level** (basic scanner; quota-limited, not visible right now but configured)
- **Private ECR repositories** — IAM-controlled access, AES-256 at rest, HTTPS-only pull
- **Non-root user inside containers** — `appuser` (set up in Phase 4)
- **CloudWatch Logs with 7-day retention** — controlled storage cost, but logs are still queryable for recent debugging

What was deliberately skipped:

- HTTPS on the ALB (no domain configured yet; HTTP is acceptable for portfolio project)
- VPC endpoints for ECR / SQS / SNS (would reduce data transfer costs and improve security in private subnet setups, but tasks are in public subnets here for cost reasons)
- KMS customer-managed encryption keys
- AWS Secrets Manager for env vars (currently env vars are baked into task definitions; safe for non-secret values like queue URLs and topic ARNs, would need to move to Secrets Manager when Phase 14 adds database credentials)

---

## Known limitations (intentional, addressed in later phases)

- **`GET /jobs/{job_id}` still returns `status="queued"`** — producer's in-memory `jobs` dict isn't shared across tasks or with the worker. Will be fixed in Phase 14 with RDS persistence.
- **ECS service name has auto-appended random suffix** (`task-processor-worker-service-r1e76pmo`). Cosmetic only. See Bug #6.
- **No autoscaling.** Desired count hardcoded to 1. CloudWatch alarm + auto-scaling policy can be added in a later phase to scale on SQS queue depth.
- **No CI/CD pipeline.** Images built and pushed manually. GitHub Actions or AWS CodePipeline could automate this; not blocking for the current portfolio goal.
- **EC2 instances from Phase 3 still exist (stopped).** Will terminate after 1 week of stable Fargate operation as rollback insurance, then clean up the old IAM roles, security groups, and target group.

---

## GitHub

- Repo: `github.com/moezalikhan/cloud-task-processor`
- New files committed to `main`:
  - `infrastructure/ecs/worker-task-def.json`
  - `infrastructure/ecs/producer-task-def.json`
  - `PHASE_5_DEPLOYMENT_GUIDE.md`
  - `PHASE_5.md`
- **Note on safety of committing task definition JSONs:** AWS account IDs and ARNs are present in these files. Account IDs are officially classified as non-sensitive by AWS — they appear in every public AWS doc example. The only thing in a task definition that would be sensitive is a hardcoded secret in the `environment` block, and none are present (all env vars here are queue URLs, topic ARNs, region, and log levels — all non-secret).

---

## What this unlocks

- **Phase 14 (RDS persistence):** simple to add — register a new task definition revision with a `DATABASE_URL` env var pointing at the RDS endpoint. ECS rolling-deploys the change automatically. No SSH, no config files, no systemd reload.
- **Phase 16 (Airflow):** Airflow can submit jobs to the producer ALB endpoint as a DAG task. Or for tighter integration, Airflow can write directly to SQS via boto3.
- **Phase 18 (Terraform):** the manual ECS setup done here is the exact same set of resources Terraform would declare. Re-implementing as IaC is mechanical translation. The task definition JSONs are already version-controlled and Terraform-compatible.

---

## Resume claim

Extend the Cloud Task Processor bullet (replacing the Phase 3 EC2-specific claim):

> Migrated a containerized FastAPI producer + Python worker workload from self-managed EC2 to ECS Fargate with task-level IAM roles, IP-based ALB target groups, and CloudWatch Logs integration. Eliminated server provisioning, OS patching, and systemd configuration from the operational footprint while preserving the existing SQS/SNS/ALB architecture. Validated zero-downtime cutover using a parallel-listener pattern (new Fargate path on temporary port 8080, swap on port 80 default action only after end-to-end verification).

Stack additions to skills section:
- **Amazon ECS, AWS Fargate, ECS task definitions, IP-type ALB target groups, ECS task vs execution role split.**

---

## Time + cost recap

- **Time:** single long day (~6–8 hours including writing, debugging the IAM/CloudFormation/console-quirks bugs, and verification). About what Phase 3 took.
- **AWS cost added during Phase 5:** ~$1 for the Fargate compute during testing. The ALB and ECR were already running.
- **Total Phase 5 cleanup state:** Fargate scaled to 0 between sessions. ALB stays. EC2 stopped. Idle cost holds at ~$16/month (the ALB floor).

---

## Cleanup checklist

- [x] Cutover from `producer-tg` (instances) to `producer-tg-ip` (IP) verified
- [x] Temporary HTTP:8080 listener on ALB deleted
- [x] Temporary port 8080 rule on `alb-sg` removed
- [x] EC2 instances stopped (kept for rollback)
- [x] All Phase 5 files committed and pushed
- [ ] Wait 1 week of stable Fargate, then terminate EC2 instances
- [ ] After EC2 termination: delete `producer-tg` (old instances target group)
- [ ] Delete EC2-era security groups: `worker-sg`, `producer-sg`
- [ ] Delete EC2-era IAM roles: `task-processor-producer-role`, `task-processor-worker-role`
- [ ] Update LinkedIn About / Featured to reflect Fargate + ECR additions to the stack
- [ ] Update repo README so anyone landing on it sees the polished version of the project

---

*End of Phase 5. Phase 14 (RDS persistence) is the natural next step — it fixes the stale `GET /jobs/{id}` bug, adds a real database to the stack, and is the cheapest phase to deploy now that everything is containerized.*
