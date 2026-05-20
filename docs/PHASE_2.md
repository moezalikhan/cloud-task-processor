# Phase 2 — SQS + SNS Integration

**Status:** ✅ Complete
**Completed:** May 12, 2026
**Time taken:** ~1 weekend

---

## What was built

Replaced the in-memory queue and logger notifier from Phase 1 with real AWS services. Producer and worker now run as **separate processes** communicating exclusively through AWS SQS, with SNS handling email notifications on job completion.

---

## Architecture changes from Phase 1

| Component | Phase 1 | Phase 2 |
|-----------|---------|---------|
| Queue | In-memory `deque` | AWS SQS |
| Notifier | Python logger | AWS SNS (email) |
| Producer/Worker | Same process (threads) | Separate processes |
| Communication | Shared memory | Network (boto3 → AWS) |
| Job state lookup | Works (`GET /jobs/{id}`) | Broken — always "queued" (fixed in Phase 14) |

---

## Files created / modified

**Modified:**
- `producer/queue.py` — added `QueueClient` abstract class, `SQSQueue`, and `get_queue_client()` factory
- `producer/main.py` — removed threading, uses `get_queue_client()`, renamed method calls to `send_message()`
- `producer/config.py` — added `sqs_queue_url`, `sns_topic_arn`, `poll_interval_seconds`, `request_timeout_seconds`
- `worker/worker.py` — converted from threaded function to standalone script with `main()` entry point, added invalid-message handling
- `requirements.txt` — added `boto3`, `pydantic-settings`, pinned versions

**New:**
- `worker/config.py` — pydantic settings for worker
- `worker/notifier.py` — `Notifier` abstract class, `LogNotifier`, `SNSNotifier`, `get_notifier()` factory
- `.env` — AWS region, queue URL, topic ARN, mode toggles
- `PHASE_2.md` — this file

---

## AWS resources created

- **SQS Queue:** `task-processor-jobs`
  - Standard type
  - Visibility timeout: 60s
  - Receive message wait time: 20s (long polling)
  - Region: `eu-north-1`
- **SNS Topic:** `task-processor-notifications`
  - Standard type
  - Email subscription confirmed
- **IAM:** new user `admin-moez` with `AdministratorAccess` (local dev only; replaced by instance profiles in Phase 8+)
- **AWS CLI configured** with access keys on local machine

---

## Bugs hit and fixed

1. **Pydantic Settings validation error** — `Settings` class missing fields that were in `.env` (`sns_topic_arn`, `poll_interval_seconds`, `request_timeout_seconds`). Added missing fields with defaults.
2. **LocalQueue test failed across processes** — initially tested with `USE_LOCAL_QUEUE=true`; producer and worker got separate `LocalQueue()` instances, so messages never crossed. This is the entire reason Phase 2 requires SQS.
3. **`KeyError: 'job_id'` on stale queue messages** — CLI test messages (`{"test":"hello"}`) sitting in queue had no `job_id`. Added validation to skip and delete malformed messages.
4. **`JSONDecodeError` on stale messages** — old messages had non-JSON body. Purged queue from AWS Console.
5. **Worker not logging on startup** — missing `logging.basicConfig(level=logging.INFO)` at top of `worker.py`. Added it.
6. **SNS subscription auto-deactivating** — recreated topic + subscription twice; eventually worked. Moved on rather than debugging further.
7. **Typos caught during code review:** `self.queueurl` vs `self.queue_url`, `json.dump` vs `json.dumps`, `message_id - response[...]` (minus instead of equals).

---

## End-to-end test (working)

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://cleverhumanizer.ai/","notify_email":"moezalics@gmail.com"}'
```

Worker logs:
```
INFO: Worker started. Polling every 5 seconds.
INFO: Received payload: {'job_id': '20ce44a5-...', 'url': 'https://cleverhumanizer.ai/', ...}
INFO: Processing job 20ce44a5-... for URL https://cleverhumanizer.ai/
INFO: Job 20ce44a5-... completed successfully
INFO: Extracted: title=100% Free AI Text Humanizer | Clever AI Humanizer, words=2266, links=37
INFO: SNS: published 'Job 20ce44a5-... completed'
INFO: SQS: deleted message
```

SNS email received.

---

## Known limitations (intentional, fixed in later phases)

- `GET /jobs/{job_id}` always returns `status="queued"` — producer's in-memory `jobs` dict isn't shared with worker's process. Fixed in Phase 14 with RDS persistence.
- AWS access keys stored on local machine in `~/.aws/credentials`. Replaced by EC2 IAM instance profiles in Phase 8.
- No retries on extraction failures. Added in Phase 14+.
- No dead-letter queue. Optional, can add in Phase 12.

---

## GitHub

- Repo: `github.com/moezalikhan/cloud-task-processor`
- New SSH key created (`id_ed25519_github`) to keep GitHub separate from GitLab (Gosign) auth
- `~/.ssh/config` updated with per-host `IdentityFile` directives
- Phase 2 pushed to `main`

---

## What this unlocks

- Phase 3: deploy producer + worker to EC2 instances (no code changes, just deployment)
- Phase 4: Dockerize and push images to ECR
- Phase 5: ECS Fargate

---

## Resume claim (after Phase 3 ships)

Don't claim Phase 2 alone — it's still localhost. Wait until Phase 3 puts this behind a live AWS ELB URL before adding to resume.
