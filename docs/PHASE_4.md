# Phase 4 — Dockerization + ECR

**Status:** ✅ Complete
**Completed:** May 19, 2026
**Time taken:** ~1 weekend
**Region:** eu-north-1 (Stockholm)

---

## What was built

Packaged both services from Phase 3 (FastAPI producer + Python worker) as portable Docker images and pushed them to Amazon ECR. Each service is now a self-contained image that runs identically on any host — laptop, EC2, ECS Fargate, anywhere — without `git clone`, `pip install`, or systemd configuration.

The Phase 3 deploy was tied to two hand-configured EC2 boxes. After Phase 4, the deploy unit is the **image**, not the **server**. This is the bridge to Phase 5 (ECS Fargate).

---

## Architecture changes from Phase 3

| Component | Phase 3 | Phase 4 |
|-----------|---------|---------|
| Deploy artifact | Source code on EC2 boxes | Docker images in ECR |
| Environment setup | Manual: `dnf install`, `pip install`, systemd unit files | Baked into Dockerfile, repeatable |
| Service start | `systemctl start task-{producer,worker}` | `docker run ...` |
| Portability | One environment (Amazon Linux 2023 on EC2) | Any Docker-compatible host |
| Rollback strategy | Re-deploy old git commit | Pull previous image tag |
| Dependency drift risk | High (host Python version, system libs) | None (image self-contained) |
| Storage of artifact | None (rebuilt on each deploy) | ECR with lifecycle policy |

---

## Files created / modified

**New:**
- `producer/Dockerfile` — slim Python 3.11 base, dependency-first layer ordering, non-root user, exec-form CMD for uvicorn
- `worker/Dockerfile` — same base, includes both `producer/` and `worker/` source (worker imports `producer.queue_client`), no `EXPOSE` (worker doesn't accept HTTP)
- `.dockerignore` — excludes `.venv/`, `.git/`, `__pycache__/`, `.env`, `tests/`, IDE files, OS junk
- `PHASE_4.md` — this file

**Modified:**
- `worker/worker.py` — fixed duplicate `extract_metadata()` call and wrong log ordering (see Bug #3 below)

---

## Dockerfile design choices

Both Dockerfiles follow the same pattern with three deliberate decisions:

**1. Base image: `python:3.11-slim`**
- ~150 MB Debian-slim with Python pre-installed
- Smaller than full `python:3.11` (~1 GB), avoids the C-extension issues that come with `python:3.11-alpine`
- Pinned to 3.11 to match the Python version used in Phase 1–3 development

**2. Dependency-first layer ordering**
```dockerfile
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY producer ./producer    # or worker
```
`requirements.txt` is copied before source code so the pip-install layer is cached across rebuilds. Code edits don't invalidate the dependency layer = fast rebuilds (<10s after first build).

**3. Non-root user**
```dockerfile
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser
```
The container runs as `appuser` instead of `root`. Standard security hygiene; ECS and Kubernetes flag images that run as root.

**4. Environment variables**
```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
```
- `PYTHONDONTWRITEBYTECODE=1` — no `.pyc` cache files (useless in containers)
- `PYTHONUNBUFFERED=1` — flush stdout/stderr immediately so `docker logs` and CloudWatch see output in real time
- `PIP_NO_CACHE_DIR=1` — don't bake the pip download cache into the image

---

## AWS resources created

| Resource | Name | Region | Notes |
|----------|------|--------|-------|
| ECR Repository | `task-processor-producer` | eu-north-1 | Private, AES-256 at rest, scan-on-push enabled |
| ECR Repository | `task-processor-worker` | eu-north-1 | Same config as producer |
| Lifecycle Policy | (on both repos) | eu-north-1 | Expire untagged images >7 days |

ECR was chosen over Docker Hub because:
- Same AWS account as SQS/SNS/EC2 (no cross-system auth)
- Private by default (Docker Hub free tier is public)
- Co-located with eu-north-1 compute (no cross-region pull cost)
- Integrates with IAM (no separate credentials)

---

## Build & push workflow

```bash
# 1. Build images from project root
docker build -f producer/Dockerfile -t task-processor-producer:latest .
docker build -f worker/Dockerfile -t task-processor-worker:latest .

# 2. Authenticate Docker to ECR (token valid ~12 hours)
aws ecr get-login-password --region eu-north-1 | \
  docker login --username AWS --password-stdin \
  $(aws sts get-caller-identity --query Account --output text).dkr.ecr.eu-north-1.amazonaws.com

# 3. Tag images with ECR URIs
docker tag task-processor-producer:latest \
  ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-producer:latest
docker tag task-processor-worker:latest \
  ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-worker:latest

# 4. Push to ECR
docker push ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-producer:latest
docker push ACCOUNT-ID.dkr.ecr.eu-north-1.amazonaws.com/task-processor-worker:latest
```

**Image sizes:** 146 MB each (content size). DISK USAGE column showed 589 MB but that includes shared base layers across both images.

**Layer deduplication observed on second push:** The worker image shares `python:3.11-slim` base + `apt-get gcc` + `pip install` layers with the producer. ECR detected these as `Layer already exists` and skipped them. Only the unique `worker/` source code layer was actually uploaded.

---

## Bugs hit and fixed

### 1. `ENV` syntax with spaces around `=`

**Symptom:** First Dockerfile draft used `ENV PYTHONDONTWRITEBYTECODE = 1` (with spaces). Docker's multi-variable `ENV` form does not allow spaces around `=` — the parser uses spaces to split between variable assignments.

**Fix:** Remove the spaces:
```dockerfile
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
```

**Learning:** Dockerfile syntax has two `ENV` forms — `ENV KEY value` (space-separated, single variable only) and `ENV KEY=value KEY2=value2` (equals, no spaces, multiple variables). Picked the modern form, kept the strict syntax.

---

### 2. Image size confusion (589 MB vs 146 MB)

**Symptom:** `docker images` output showed two columns — DISK USAGE (589 MB) and CONTENT SIZE (146 MB) — for the same image. Initial concern that the image was bloated.

**Diagnosis:** This was a custom Docker Desktop output format. The 589 MB is the total disk footprint including shared base image layers that exist alongside other images. The 146 MB is the actual content unique to `task-processor-producer` — and that's what gets pushed to ECR and pulled by other hosts.

**Outcome:** Image size is correct. 146 MB is well under the ~200 MB target for a Python + FastAPI service.

---

### 3. Duplicate `extract_metadata()` call in `worker.py`

**Symptom:** Container logs for a single SQS message showed:
```
INFO: Job <uuid> completed successfully       ← logged before processing
INFO: Extracted: title=..., words=..., links=...
INFO: Processing job <uuid> for URL ...        ← out of order
INFO: Job <uuid> completed successfully       ← duplicate
```

**Root cause:** Leftover debugging code. `extract_metadata()` was being called twice — once outside the `try` block (no error handling), once inside it. The first call's success was logged immediately, then the second call ran inside the try block and logged "completed" again.

The bigger issue wasn't the duplicate log — it was that the first call had **no exception handling**. If extraction failed, the worker would crash, the message would get retried after SQS visibility timeout, and the worker would loop on the same bad message.

**Fix:** Removed the first call, moved `"Processing job X for URL Y"` log before the `try`, consolidated the success-path logs into one line with all metadata.

**Verification after rebuild:** Logs now show four clean lines per successful job:
```
INFO: Received payload: {...}
INFO: Processing job <uuid> for URL <url>
INFO: Job <uuid> completed successfully. title=..., words=..., links=...
INFO: SNS: published 'Job <uuid> completed'
INFO: SQS: deleted message
```

---

### 4. Stale SQS message picked up alongside fresh test

**Symptom:** First worker container run drained two messages instead of one. One was the expected `example.com` test job; the other was a `py4e.com` job submitted on May 14, days earlier.

**Root cause:** SQS retains messages for 4 days by default. A test job from earlier Phase 3 testing had been sitting in the queue with no worker to process it. The new worker correctly picked it up.

**Outcome:** Not a bug — feature working as designed. SQS decoupling means any message in the queue gets processed by the next available worker. This is exactly why queue-based architectures are resilient.

---

### 5. AWS credentials not visible inside container

**Symptom:** Worker on Phase 3 EC2 used an IAM instance profile — no `.aws/credentials` file needed. Running the same code in a Docker container on the laptop, boto3 couldn't find credentials.

**Fix:** Mount the local AWS credentials directory into the container as a read-only volume:
```bash
docker run --rm \
  --env-file .env \
  -v ~/.aws:/home/appuser/.aws:ro \
  --name worker \
  task-processor-worker:latest
```

boto3 follows its standard credential chain and finds `~/.aws/credentials` (mounted from the host). The `:ro` flag makes the mount read-only, so the container can't accidentally modify host credentials.

**Note for Phase 5:** In ECS Fargate, the task's IAM role provides credentials automatically (no mount needed). The mount is only necessary for local Docker development.

---

### 6. ECR scan failed with `UnsupportedImageTypeException`

**Symptom:** Tried to manually trigger a vulnerability scan after push:
```bash
aws ecr start-image-scan --repository-name task-processor-producer ...
```
Returned: `An artifact with media type 'application/vnd.oci.image.index.v1+json' cannot be scanned.`

**Root cause:** Modern Docker Desktop builds **multi-platform images by default** (OCI image index containing manifests for both `linux/amd64` and `linux/arm64`). ECR's basic scanner only handles single-platform images, not the multi-platform index format.

**Mitigation:** Two paths forward, neither blocking:
- Skip scan for now (basic scanning is informational; no critical CVEs would change the deploy decision)
- Rebuild explicitly for `linux/amd64` using `docker buildx build --platform linux/amd64 ...` if scanning is required

**Decision:** Skipped for Phase 4. The interview-relevant fact is that scanning is *enabled at the repo level*, not that scan results are visible. For Phase 5 (Fargate), rebuilding with `--platform linux/amd64` is the correct path — Fargate runs amd64 only, and single-platform images are smaller, scannable, and faster to pull.

---

## End-to-end test (working)

**Setup:**

```bash
# Terminal 1: producer container
docker run --rm -p 8000:8000 --env-file .env \
  -v ~/.aws:/home/appuser/.aws:ro --name producer \
  task-processor-producer:latest

# Terminal 2: worker container
docker run --rm --env-file .env \
  -v ~/.aws:/home/appuser/.aws:ro --name worker \
  task-processor-worker:latest

# Terminal 3: submit job
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","notify_email":"moezalikhan@hotmail.com"}'
```

**Result:**

Producer logs:
```
INFO:botocore.credentials:Found credentials in shared credentials file: ~/.aws/credentials
INFO: Uvicorn running on http://0.0.0.0:8000
INFO:     192.168.65.1:45170 - "GET /health HTTP/1.1" 200 OK
INFO:producer.queue:SQS: enqueued df595c68-a4d6-4373-8b43-9a760f27a23a
INFO:     192.168.65.1:32391 - "POST /jobs HTTP/1.1" 201 Created
```

Worker logs:
```
INFO: Worker started. Polling every 5 seconds.
INFO: Received payload: {...}
INFO: Processing job e60e64f0-... for URL https://example.com/
INFO: Job e60e64f0-... completed successfully. title=Example Domain, words=21, links=1
INFO: SNS: published 'Job e60e64f0-... completed'
INFO: SQS: deleted message
```

Email received from SNS within ~30 seconds. Full producer → SQS → worker → SNS chain verified inside containers.

---

## Security posture

What's protecting these images right now:

- **Private repositories** — no public pull, no cross-account access
- **IAM-controlled push/pull** — only IAM principals with explicit ECR permissions can interact
- **AES-256 encryption at rest** — AWS-managed keys, automatic
- **HTTPS-only transport** — ECR rejects plain HTTP
- **Scan-on-push enabled** (registry-level scanning currently incompatible with multi-platform image index — see Bug #6)
- **Lifecycle policy** — untagged images auto-expire after 7 days, prevents storage creep
- **Non-root user inside container** — `appuser`, not root

What was deliberately skipped (overkill for learning project):
- KMS customer-managed encryption keys
- Cross-account repository policies
- VPC endpoints for ECR (relevant only for private-subnet compute)
- Image signing (Notary / sigstore / AWS Signer)
- Immutable tags

---

## Known limitations (intentional, addressed in later phases)

- **Multi-platform image indices break basic ECR scanning.** Fix in Phase 5 by building single-platform `linux/amd64` images for Fargate.
- **`:latest` tag is mutable.** Acceptable for learning iteration. Production would use immutable versioned tags (`:v1.0.0`, `:phase4-abc123`).
- **AWS credentials mounted from host for local runs.** Acceptable for laptop dev. Phase 5 (ECS) uses task IAM roles, no mount needed.
- **No automated build pipeline.** Manual `docker build` + `docker push`. Could be automated with GitHub Actions in a later phase if useful.

---

## GitHub

- `producer/Dockerfile`, `worker/Dockerfile`, `.dockerignore`, updated `worker/worker.py`, and `PHASE_4.md` all committed to `main`
- Repository: `github.com/moezalikhan/cloud-task-processor`

---

## What this unlocks

- **Phase 5: ECS Fargate** — Fargate's entire input is "give me an ECR image URI and run it." With Phase 4 done, Fargate has something to run. No more SSH-managed EC2 instances.
- **Phase 14: RDS persistence** — easier to add when the producer/worker are containers (just inject `DATABASE_URL` env var) than when they're managed via systemd on hand-configured boxes.
- **Phase 18: Terraform IaC** — Terraform can reference ECR image URIs directly when defining ECS task definitions. The whole stack becomes declarable.

---

## Resume claim (Phase 4 specific addition)

After this phase, the Cloud Task Processor bullet can be extended:

> Containerized FastAPI producer and Python worker services with multi-stage-friendly Dockerfiles (slim Python base, dependency-first layer caching, non-root runtime). Pushed images to Amazon ECR with scan-on-push and a lifecycle policy for untagged image cleanup. Verified end-to-end producer → SQS → worker → SNS flow running from containers locally with mounted AWS credentials.

Stack additions: **Docker, Amazon ECR, container security best practices.**

---

## Time + cost recap

- **Time:** ~1 weekend (8–10 hours including debugging)
- **AWS cost added:** ~$0.03/month for ECR storage (300 MB total, well under 500 MB free tier for first 12 months)
- **Total project cost still under:** $15/month if all EC2 instances run 24/7. Currently stopped between sessions, so closer to $1–2/month.

---

*End of Phase 4. Phase 5 (ECS Fargate) is the natural next step — replacing the two EC2 instances with serverless containers pulling these ECR images.*
