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
│   ├── database.py            # PostgreSQL connection/dialect adapter
│   ├── job_store.py           # Persistent PostgreSQL job repository
│   ├── artifact_io.py         # Atomic JSON artifact writes
│   ├── media_probe.py         # Single-pass source media metadata probe
│   ├── stage_checkpoint.py    # Per-job fingerprint/checksum checkpoints
│   └── social_video_downloader.py # Allow-listed yt-dlp adapter and download limits
├── pipeline/
│   ├── ocr.py                 # Burned-subtitle OCR and ASR reconciliation
│   ├── subtitle_layout.py     # Per-cue placement, sizing and mask metadata
│   ├── stage_metrics.py       # Content-free timing and real-time-factor metrics
│   ├── tiktok.py              # Local social-post provider and artifacts
│   └── voice_routing.py       # Pitch-based or manual TTS voice routing
│   └── translation.py         # Batched translation, retry and review fallback metadata
└── tests/
```

`main.py` remains the compatibility entry point while responsibilities are
extracted incrementally. Configuration and job persistence now enter through
`app.config` and `application.job_service`; new infrastructure or provider
logic must not be added directly to API route functions.

## Story → comic video

A second pipeline turns prose into a narrated comic video. It is an ordinary job
(`job_action = "comic_video"`), so queueing, cancellation, progress events, retry,
quota and ownership are shared with video translation.

```text
TXT/DOCX/text -> story_text (normalise, chunk) -> comic_storyboard (LLM JSON + Pydantic
validation + bounded repair, or sentence splitter) -> storyboard.json (versioned, editable)
-> comic_video: character references -> scene images -> voice -> subtitle cues
-> per-scene clip (zoompan, fades, burned cues) -> concat + music -> MP4 + SRT
```

- `domain/comic.py` holds the storyboard contract; `application/comic_routes.py` the
  `/api/v1/comic` API; `application/comic_service.py` runs the job.
- Providers sit behind interfaces: `infrastructure/llm_providers.py` (Gemini, Qwen3 via
  Ollama) and `infrastructure/image_providers.py` (Gemini image with reference images,
  offline placeholder).
- Every derived asset is content-addressed by the inputs it depends on, so editing one
  scene regenerates only that scene's image/voice/clip; images and voice survive edits
  so reverting is free. The storyboard can only be edited while the job is idle.

### Animated mode

`video_mode = "animated"` splits every scene into shots (one camera set-up, one simple
3-8 s action). Per shot: keyframe (from the approved character reference) -> voice, which
fixes the shot length -> image-to-video (`infrastructure/video_providers.py`: Veo, a
self-hosted GPU worker per `docs/animation-worker-api.md`, or the local 2.5D camera) ->
automatic QC (`pipeline/animation_qc.py`: unreadable, too short, frozen, black) -> bounded
retries with new seeds -> 2.5D fallback when attempts or the render's AI-seconds budget run
out -> normalised clip (size, fps, <= 25 % slow-down, last-frame hold). A process-wide
semaphore (`VIDTRANS_ANIMATION_CONCURRENCY`) caps concurrent AI generations. Within a job,
up to `VIDTRANS_COMIC_CONCURRENCY` scenes (or shots' keyframes, voices and clips) are built at
once; AI motion still runs shot by shot so the AI budget goes to the earliest shots. Editing a
motion prompt, or "regenerate" (a new take/seed), re-animates only that shot; approved
character references are pinned so profile edits create variants instead of overwriting.
Lip-sync is not implemented yet.

## Job state

Job state is stored in PostgreSQL configured by `VIDTRANS_DATABASE_URL`. Docker
Compose persists it in the `postgres-data` volume.

Properties:

- one PostgreSQL connection per operation;
- atomic read/merge/write updates in database transactions;
- JSON payload keeps the current `/status/{job_id}` response compatible;
- unfinished in-process jobs are requeued and restarted from their persisted request after restart.
- transcript, complete translation, TTS, mixed-audio and rendered-video checkpoints are reused
  after retry/restart only when their per-job fingerprint, schema and artifact/dependency checksums
  still match; failed translations and incomplete media bundles are never cached.
- atomic queue claims prevent two workers from running one job;
- queued and running jobs support cooperative cancellation;
- batch and job list queries support pagination, status filters and search.

`VIDTRANS_RECOVER_INTERRUPTED_JOBS=1` requeues unfinished jobs at startup. The local
scheduler uses atomic PostgreSQL claims and configurable worker concurrency. CPU-heavy
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
8. Stage-level cache keys and checkpoint resume. **Implemented locally for per-job transcript,
   translation, TTS, mixed-audio and render artifacts; kill-after-TTS/restart validated, while
   remaining crash boundaries and the audio-duration defect remain open.**
9. Redis-backed workers for multi-host deployment.
10. Interactive subtitle review editor and background inpainting.

Every extraction must keep existing endpoints and the three processing modes
working until a versioned replacement API is ready.
