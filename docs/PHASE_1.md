# Phase 1 — Local FastAPI + In-Memory Queue

> Goal: working producer + worker on your laptop. No AWS. One weekend.

---

## Scope

Build a URL metadata extraction service running entirely on localhost. Producer accepts jobs over HTTP, in-memory queue holds them, worker thread processes them, results retrievable via HTTP.

**In scope:** FastAPI producer, in-memory queue, background worker thread, URL fetch + metadata extraction, status state machine.

**Out of scope:** AWS, Docker, persistence, auth, retries, multiple workers.

---

## Project structure

```
cloud-task-processor/
├── README.md
├── PROGRESS.md
├── PHASE_1.md            # what you built, what broke, what you learned
├── .gitignore
├── requirements.txt
│
├── producer/
│   ├── __init__.py
│   ├── main.py           # FastAPI app + lifespan
│   ├── models.py         # Pydantic models
│   └── queue.py          # LocalQueue
│
├── worker/
│   ├── __init__.py
│   ├── worker.py         # poll loop
│   └── extractor.py      # URL fetch + parse
│
└── tests/
    ├── __init__.py
    └── test_extractor.py
```

---

## Dependencies (`requirements.txt`)

```
fastapi
uvicorn[standard]
pydantic
requests
beautifulsoup4
lxml
pytest
```

---

## Endpoints

| Method | Path | Body | Response | Status |
|--------|------|------|----------|--------|
| GET | `/health` | — | `{"status":"healthy"}` | 200 |
| POST | `/jobs` | `{url, notify_email?}` | `{job_id, status, submitted_at}` | 201 |
| GET | `/jobs/{job_id}` | — | full job state | 200 / 404 |

---

## Pydantic models (`producer/models.py`)

**`JobCreateRequest`**
- `url: HttpUrl`
- `notify_email: str | None = None`

**`JobCreateResponse`**
- `job_id: UUID`
- `status: str` (always `"queued"`)
- `submitted_at: datetime`

**`JobStatusResponse`**
- `job_id: UUID`
- `status: str`
- `submitted_at: datetime`
- `completed_at: datetime | None`
- `result: dict | None`
- `error: str | None`

---

## Queue (`producer/queue.py`)

**Class `LocalQueue`** wrapping `collections.deque`:

- `send(payload: dict) -> str` — append, return generated `message_id`
- `receive() -> tuple[str, dict] | None` — popleft if not empty, else None
- `delete(receipt_handle: str) -> None` — no-op (popleft already removed)

Single shared instance lives in `producer/main.py` module scope.

---

## Extractor (`worker/extractor.py`)

**`extract_metadata(url: str, timeout: int = 10) -> dict`**

Returns:
```python
{
  "url": str,
  "status_code": int,
  "title": str | None,
  "description": str | None,
  "word_count": int,
  "link_count": int,
  "image_count": int,
}
```

Logic:
- `requests.get(url, timeout=timeout, headers={"User-Agent": "cloud-task-processor/0.1"})`
- `response.raise_for_status()`
- Parse with `BeautifulSoup(html, "lxml")`
- Title from `soup.title.string`
- Description from `<meta name="description">` content
- Word count from `soup.get_text(separator=" ", strip=True).split()`
- Link/image counts from `find_all("a", href=True)` / `find_all("img", src=True)`

Raises `ExtractionError(str)` on `requests.RequestException` or HTTP error.

---

## Worker (`worker/worker.py`)

**`run_worker(queue, jobs, stop_event)`** — blocking function for thread target.

```
while not stop_event.is_set():
    msg = queue.receive()
    if msg is None:
        time.sleep(5); continue
    receipt, payload = msg
    job_id = UUID(payload["job_id"])
    jobs[job_id]["status"] = "processing"
    try:
        result = extract_metadata(payload["url"])
        jobs[job_id].update(status="completed", completed_at=now(), result=result)
    except ExtractionError as e:
        jobs[job_id].update(status="failed", completed_at=now(), error=str(e))
    queue.delete(receipt)
```

---

## Wiring (`producer/main.py`)

**Module-scope shared state:**
- `jobs: dict[UUID, dict] = {}`
- `queue = LocalQueue()`
- `stop_event = threading.Event()`

**Lifespan:**
- On startup: spawn `Thread(target=run_worker, args=(queue, jobs, stop_event), daemon=True).start()`
- On shutdown: `stop_event.set()`

**`POST /jobs` handler:**
1. Generate `job_id = uuid4()`
2. Build job record with `status="queued"`, `submitted_at=now()`
3. `jobs[job_id] = {...}`
4. `queue.send({"job_id": str(job_id), "url": str(req.url), "notify_email": req.notify_email})`
5. Return `JobCreateResponse`

**`GET /jobs/{job_id}` handler:**
1. `job = jobs.get(job_id)`
2. If None → `raise HTTPException(404, "Job not found")`
3. Return as `JobStatusResponse`

---

## Status state machine

```
queued → processing → completed
                   ↘ failed
```

Set by worker. No retries in Phase 1.

---

## Validation behavior

- Invalid URL on POST → 422 (Pydantic via `HttpUrl`)
- Missing body → 422
- Unknown `job_id` on GET → 404
- Worker timeout / 5xx / DNS failure → job ends in `failed` with error string

---

## Tests (`tests/test_extractor.py`)

```python
def test_extract_metadata_live():
    result = extract_metadata("https://example.com")
    assert result["status_code"] == 200
    assert result["word_count"] > 0
    assert "Example" in (result["title"] or "")

def test_extract_metadata_invalid_url():
    with pytest.raises(ExtractionError):
        extract_metadata("https://this-domain-does-not-exist-xyz123.com")
```

Run: `pytest tests/ -v`

---

## Manual acceptance test

1. `uvicorn producer.main:app --reload`
2. Open `http://localhost:8000/docs`
3. POST `/jobs` with `{"url":"https://example.com"}` → get `job_id`
4. Wait ~6 seconds
5. GET `/jobs/{job_id}` → status `completed`, result includes title `"Example Domain"`
6. POST `/jobs` with `{"url":"https://invalid-domain-xyz123.com"}` → wait → GET shows `failed` with error message

All six steps must pass before phase is done.

---

## Build order

1. `models.py` — no deps
2. `queue.py` — no deps
3. `extractor.py` — uses `requests` + `bs4`
4. `worker.py` — uses queue + extractor
5. `main.py` — wires everything
6. `test_extractor.py`

Each file testable in isolation before moving on.

---

## Definition of done

- [ ] All 3 endpoints work via `/docs`
- [ ] POST → wait → GET shows extracted metadata
- [ ] Invalid URL ends as `failed`, not crash
- [ ] Pytest green
- [ ] `README.md` explains what + how to run
- [ ] `PHASE_1.md` written: what broke, what you learned
- [ ] Pushed to GitHub

---

## Rules

- No AI for code. AI only for "what concept am I missing?" after 2 hrs stuck.
- Read FastAPI docs (sections: First Steps → Path Params → Query Params → Request Body → Response Model) before coding.
- Search exact error strings, not "how to do X."
- Commit after each working file.

---

## Stretch (optional, only if ahead)

- Convert `extract_metadata` calls to `httpx.AsyncClient` and one async endpoint
- Add `pytest` test for the producer using `httpx.AsyncClient` against the FastAPI app
- Add request logging middleware

---

## What this unlocks for Phase 2

Same files, same interfaces. Only `queue.py` gets a second class (`SQSQueue`) and `worker.py` gets a notifier. The threading hack disappears because producer and worker become separate processes again, communicating through real SQS.
