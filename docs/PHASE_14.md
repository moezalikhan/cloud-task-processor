# Phase 14 — RDS Persistence (Complete)

> **Goal:** Replace the producer's in-memory `jobs` dict with a shared PostgreSQL database on AWS RDS, so `GET /jobs/{id}` returns real status written by the worker.
> **Status:** ✅ Complete. Verified end-to-end: POST → `queued` → worker processes → `completed` read back from shared DB.

---

## The problem this fixed

Producer and worker run as separate Fargate tasks. Each had its own process memory, so the producer's in-memory `jobs` dict never saw the worker's updates — `GET /jobs/{id}` always returned `"queued"` even after the worker finished. This is the classic "distributed services need a shared data store" problem.

**Fix:** a shared PostgreSQL database (AWS RDS) both tasks connect to over the network. Producer writes the job row on POST; worker updates status/result on completion; producer's GET reads real state from the DB.

---

## What was built

**Infrastructure**
- RDS PostgreSQL instance `task-processor-db`, `db.t4g.micro`, free tier, single-AZ, 20 GB gp3
- Engine: PostgreSQL 18.3
- Same VPC as Fargate (`vpc-076c7d2a6bbd31c5d`, default VPC), private (publicly accessible: No)
- Endpoint: `task-processor-db.clyuqi0mak34.eu-north-1.rds.amazonaws.com:5432`
- **Database name: `postgres`** (Easy create did not allow setting a custom initial DB name — note this, the connection string uses `/postgres`, not `/taskprocessor`)
- Security: reused the default SG `sg-0de24cce866193ebe`; removed the open self-referencing rule and added two inbound PostgreSQL (5432) rules sourced from the Fargate producer SG and Fargate worker SG

**Code**
- New shared DB module: `producer/db.py` and identical `worker/db.py` (two copies, matching the existing `queue_client` duplication pattern, because each image only builds from its own service dir but both need the module)
  - SQLAlchemy engine with `pool_pre_ping=True` (avoids stale-connection errors when the worker idles between SQS messages)
  - `SessionLocal` factory, `autocommit=False`
  - `Job` model mapping the `jobs` table (mirrors the old dict: job_id PK, url, notify_email, status, submitted_at, completed_at, result as JSON, error)
  - `init_db()` runs `create_all` (idempotent; creates the table on first producer boot — replaces hand-creating tables)
- `producer/main.py`: removed in-memory dict; `create_job` inserts a `Job` row then queues; `get_job` reads via `session.get(Job, str(job_id))`. The `str()` cast matters — FastAPI hands a `UUID`, the column is `String`.
- `worker/worker.py`: added `mark_completed` / `mark_failed` helpers; called after extraction succeeds/fails, before the email and before `delete_message` (so a failed DB write leaves the SQS message for redelivery). Includes a "job not found" guard so a stale message can't crash the loop.
- `requirements.txt`: added `sqlalchemy==2.0.35`, `psycopg2-binary==2.9.9`
- Rebuilt and pushed both images to ECR (`linux/amd64`)
- Registered new task-def revisions (`task-processor-producer`, `task-processor-worker:2`) with a `DATABASE_URL` env var; deployed via `update-service` and scaled to 1

**Connection string format**
```
postgresql+psycopg2://taskadmin:PASSWORD@task-processor-db.clyuqi0mak34.eu-north-1.rds.amazonaws.com:5432/postgres
```

---

## Problems hit & fixes (hard-won, worth remembering)

1. **RDS instance-class dropdown unselectable on Standard + Free tier.** The Free tier template locks the instance class, and on the Standard create form the dropdown showed greyed and threw "This field is required," refusing to bind. Switching to **Easy create** auto-selected `db.t4g.micro` (free-tier eligible, ARM/Graviton — fine for Postgres, irrelevant to the app) and got past it.

2. **Easy create skips key fields.** It picked the default VPC and default SG automatically and didn't expose an initial DB name. Trade-off accepted because the default VPC matched the Fargate VPC anyway. Consequence: DB name defaulted to `postgres` (see above).

3. **The console cost estimate (~$0.019/hr, ~$21/mo) is the POST-free-tier price**, not a current charge. Inside the 12-month free tier on single-AZ ≤20 GB, it's $0. Don't panic at the number; don't click "Upgrade account plan."

4. **`Modify` is disabled while RDS is provisioning/backing-up.** Wait for status `Available` before editing.

5. **The full `Modify` screen re-triggered the instance-class bind bug**, blocking save when trying to swap the SG. **Workaround: don't change the SG on the instance — edit the SG's inbound rules directly instead.** Cleaner and avoids the broken Modify flow entirely.

6. **Docker build context was wrong.** Ran `docker build ./producer`, but the Dockerfile does `COPY producer ./producer` and `COPY requirements.txt .` — paths relative to the **repo root**, not the service dir. Result: `"/producer": not found`, and because earlier layers were cached, a **stale image got pushed** while the build actually failed (the "pushed" line was misleading).
   - **Fix:** build from repo root with explicit Dockerfile:
     ```
     docker build --platform linux/amd64 -f producer/Dockerfile -t task-processor-producer .
     docker build --platform linux/amd64 -f worker/Dockerfile -t task-processor-worker .
     ```
   - Lesson: a "successful push" after a failed build means a cached/stale image — always confirm the COPY steps actually ran.

7. **Worker image needs the `producer` package too** — the worker imports `from producer.queue import ...`, so the worker Dockerfile copies `producer` as well as `worker`. Root build context makes this work.

8. **Docker daemon not running** gave `failed to connect to the docker API ... docker.sock: no such file`. Just start Docker Desktop; `docker info` confirms it's up.

9. **Brief "3 pending" tasks during rollout** (expected 2). It settled to 2 running once the new revisions stabilized — old revision tasks draining. Not an error if it converges to the desired count.

---

## Verification (definition of done — met)

```
POST /jobs  → {"job_id":"3caf3633-...","status":"queued",...}
GET  /jobs/3caf3633-...
→ {"status":"completed","completed_at":"...","result":{"title":"Example Domain","word_count":21,"link_count":1,...},"error":null}
```

Status came back `completed` with real worker-written metadata, read from the shared DB. Bug fixed.

---

## Security / hygiene notes

- The `DATABASE_URL` (with password) was entered **directly in the ECS console**, not from a local JSON file — so **nothing with the password touched the repo**. No scrubbing needed.
- **Future caveat:** if task-def JSON is ever exported (`aws ecs describe-task-definition > file.json`) and committed, the password comes with it. Keep a `REPLACE_ME` placeholder version in git if that's ever done.
- Stretch goal not taken: AWS Secrets Manager for the DB password (deliberately out of scope for this phase).

---

## Cost / teardown

- RDS bills independently of Fargate — scaling Fargate to 0 does **not** pause the DB.
- Fargate scaled back to 0 between sessions (usual pattern).
- To fully pause the DB: stop the RDS instance separately (auto-restarts after 7 days).

---

## What this unlocks

AWS build for the portfolio is **complete**. Next is shipping, not more infrastructure:
1. Resume + LinkedIn: add PostgreSQL, SQLAlchemy, AWS RDS; update the Cloud Task Processor bullet with the persistence layer
2. Unpause job applications (Pakistan agencies first: Devsinc, Arbisoft, 10Pearls)
3. LinkedIn post (the shared-database fix story)
4. dbt comes off hold once applications are flowing

Resist starting Phase 16 (Airflow) immediately — the portfolio cleared the bar; further building has diminishing returns unless a specific role asks for a specific skill.
