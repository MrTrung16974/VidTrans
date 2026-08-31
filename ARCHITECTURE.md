# VidTrans Architecture

## Direction

VidTrans is developed as a modular monolith. The API, pipeline and workers may
run in one deployment today, but their contracts must remain separate so heavy
stages can move to dedicated workers later without changing the public API.

The current processing flow is:

```text
file upload / TikTok-Douyin URL -> persistent queue -> source downloader
             -> resource coordinator
             -> OCR + ASR -> reconcile -> translate -> subtitle layout
             -> social summary + TTS -> audio mix -> render -> artifacts
```

## Current modules

```text
backend/
├── main.py                    # FastAPI compatibility entry point
├── app/
│   └── config.py              # Runtime paths and FFmpeg resolution
├── application/
│   ├── job_scheduler.py       # Persistent bounded background worker pool
│   └── job_service.py         # Batch/job lifecycle application boundary
├── domain/
│   └── models.py              # Processing-mode and request contracts
├── infrastructure/
│   ├── job_store.py           # Persistent SQLite job repository
│   └── social_video_downloader.py # Allow-listed yt-dlp adapter and download limits
├── pipeline/
│   ├── ocr.py                 # Burned-subtitle OCR and ASR reconciliation
│   ├── subtitle_layout.py     # Per-cue placement, sizing and mask metadata
│   ├── tiktok.py              # Local social-post provider and artifacts
│   └── voice_routing.py       # Pitch-based or manual TTS voice routing
│   └── translation.py         # Batched translation, retry and review fallback metadata
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
- atomic queue claims prevent two workers from running one job;
- queued and running jobs support cooperative cancellation;
- batch and job list queries support pagination, status filters and search.

`VIDTRANS_RECOVER_INTERRUPTED_JOBS=1` requeues unfinished jobs at startup. The local
scheduler uses atomic SQLite claims and configurable worker concurrency. CPU-heavy
pipelines may overlap while Whisper and OCR are protected by dedicated semaphores.

## Module boundaries to preserve

- API routes validate input and expose status; they do not implement OCR,
  Whisper, translation, TTS or FFmpeg algorithms.
- Pipeline stages consume and return versioned data structures.
- Providers wrap external libraries and network services.
- Social-content generation implements a provider contract and writes separate
  `.tiktok.txt` and versioned `.tiktok.json` artifacts. Its failure does not fail
  video rendering.
- Infrastructure owns persistence, files, subprocesses and queues.
- Heavy work never runs directly in the request-response path.
- Remote source URLs are allow-listed at the API boundary and downloaded inside workers;
  the persisted request is updated to the resolved local file for restart/retry.
- OCR geometry is normalized to video coordinates before translation. Rendering
  consumes `subtitle_layout` metadata and falls back to a safe bottom position
  when a cue has no reliable visual location.

## Planned extraction order

1. Persistent job repository and restart behavior. **Implemented.**
2. Application configuration, job service and typed processing-request model.
   **Implemented.**
3. Persistent local scheduler, batch API, cancellation and retry. **Implemented.**
4. OCR bbox preservation and per-cue subtitle layout. **Implemented.**
5. Responsive batch creator and job dashboard. **Implemented.**
6. Media/FFmpeg adapter extracted from `main.py`.
7. Translation and TTS provider interfaces.
8. Stage-level cache keys and checkpoint resume.
9. Redis-backed workers for multi-host deployment.
10. Interactive subtitle review editor and background inpainting.

Every extraction must keep existing endpoints and the three processing modes
working until a versioned replacement API is ready.
