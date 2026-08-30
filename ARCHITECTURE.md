# VidTrans Architecture

## Direction

VidTrans is developed as a modular monolith. The API, pipeline and workers may
run in one deployment today, but their contracts must remain separate so heavy
stages can move to dedicated workers later without changing the public API.

The target processing flow is:

```text
upload -> preprocess -> OCR + ASR -> reconcile -> translate
       -> subtitle/TTS -> audio mix -> render -> export
```

## Current modules

```text
backend/
├── main.py                    # FastAPI compatibility entry point
├── app/
│   └── config.py              # Runtime paths and FFmpeg resolution
├── application/
│   └── job_service.py         # Job lifecycle application boundary
├── domain/
│   └── models.py              # Processing-mode and request contracts
├── infrastructure/
│   └── job_store.py           # Persistent SQLite job repository
├── pipeline/
│   └── ocr.py                 # Burned-subtitle OCR and ASR reconciliation
└── tests/
```

`main.py` remains the compatibility entry point while responsibilities are
extracted incrementally. Configuration and job persistence now enter through
`app.config` and `application.job_service`; new infrastructure or provider
logic must not be added directly to API route functions.

## Job state

Job state is stored in `backend/work/jobs.sqlite3`, which is already covered by
the existing Docker volume and `.gitignore` rules.

Properties:

- one SQLite connection per operation;
- atomic read/merge/write updates with `BEGIN IMMEDIATE`;
- WAL mode for readers while a background task updates progress;
- JSON payload keeps the current `/status/{job_id}` response compatible;
- unfinished in-process jobs are requeued and restarted from their persisted request after restart.

`VIDTRANS_RECOVER_INTERRUPTED_JOBS=1` is correct for the current single-process
deployment. It restarts a persisted job from the beginning when its source upload
is still available. It must be disabled when multiple API/worker processes are
added; at that point, recovery belongs to the queue coordinator and job leases.

## Module boundaries to preserve

- API routes validate input and expose status; they do not implement OCR,
  Whisper, translation, TTS or FFmpeg algorithms.
- Pipeline stages consume and return versioned data structures.
- Providers wrap external libraries and network services.
- Infrastructure owns persistence, files, subprocesses and queues.
- Heavy work never runs directly in the request-response path.

## Planned extraction order

1. Persistent job repository and restart behavior. **Implemented.**
2. Application configuration, job service and typed processing-request model.
   **Implemented.**
3. Typed `Job`, `SubtitleCue`, `Artifact` and `StageResult` models.
4. Media/FFmpeg adapter extracted from `main.py`.
5. Translation and TTS provider interfaces.
6. Pipeline orchestrator with stage artifacts, cache keys and resume.
7. Redis-backed workers when deployment can run more than one process.
8. Review API and subtitle editor.

Every extraction must keep existing endpoints and the three processing modes
working until a versioned replacement API is ready.
