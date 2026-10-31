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

## Clients and API boundary

```text
browser ──> gateway nginx ─┬─ /api/*, /docs, legacy routes, WebSocket ──> vidtrans (FastAPI)
                           └─ everything else ──────────────────────────> vidtrans-web (static)
Flutter app (iOS/Android) ──────────── /api/* (Bearer token) ──────────> vidtrans (FastAPI)
```

- `backend/` is API-only in containers. It serves `web/` only when run directly with uvicorn
  for local development (or when `VIDTRANS_WEB_DIR` points at a web build).
- `web/` is a static client with its own nginx image. It fills in SEO URLs per request and,
  locally, proxies API paths to the backend.
- `contracts/openapi.json` is the API contract; the Flutter client is generated from it
  (`backend/scripts/export_openapi.py`, `docs/mobile-api.md`). A test fails when routes and the
  contract diverge.
- Web clients authenticate with HttpOnly cookies plus an anti-CSRF header. Native clients use
  `client=native` at login, keep tokens themselves, send `Authorization: Bearer` (HTTP and
  WebSocket) and refresh with a JSON body. OAuth flows started by an app return to an
  allow-listed deep link (`VIDTRANS_APP_REDIRECT_URIS`).
- New features must be usable through `/api/v1` with JSON in and out; UI-only behaviour belongs
  in the clients, not in the API.

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
TXT/DOCX/text -> story_text (normalise, chunk) -> [story brief -> visual bible: world line,
characters, locations (one LLM call)] -> comic_storyboard (LLM JSON + Pydantic
validation + bounded repair, or sentence splitter) -> storyboard.json (versioned, editable)
-> comic_video: character references -> scene images -> voice -> subtitle cues
-> per-scene clip (zoompan, fades, burned cues) -> concat + music -> MP4 + SRT
```

- `domain/comic.py` holds the storyboard contract; `application/comic_routes.py` the
  `/api/v1/comic` API; `application/comic_service.py` runs the job.
- Providers sit behind interfaces: `infrastructure/llm_providers.py` (Gemini, Qwen3 via
  Ollama) and `infrastructure/image_providers.py` (Gemini image with reference images,
  offline placeholder).
- Creation is two steps: `POST /api/v1/comic/bible/suggest` reads the story (up to 3 chunks) and
  proposes a bilingual bible — Vietnamese for the user, English for image models — which the user
  reviews and sends back as `bible` (`settings.bible`, authoritative, no second planning pass). For a
  new episode the same call compares the chapter with the series and marks entries `new`/`known`;
  only new ones are reviewed. Editing a Vietnamese look clears its English twin, and the worker
  translates it again (`fill_english`) before anything is drawn. Genre presets fill the world line.
- Without a reviewed bible, the user's notes (`settings.story_brief`, plus `episode_brief`) are turned
  into a bible before scenes are split: a world line, characters with fixed English looks and
  locations with fixed English descriptions. Scenes point at a location (`location_id`); image
  prompts use its description and the world line, so one place is drawn alike in every scene.
  A series keeps world and locations like its character bible.
- Each planned place gets a picture: an empty establishing shot drawn once (content-addressed,
  `refs/place-<id>-*.png`) or a pinned upload. Scene images and keyframes pass it as
  `ImageRequest.setting_reference` — Gemini as a last reference image, ComfyUI through a second,
  weaker IP-Adapter (`VIDTRANS_COMFYUI_SETTING_WEIGHT`, default 0.35). Providers without
  reference support skip it and keep the text description. A series shares pinned place pictures
  like character references. `RenderRequest.preview_scenes` draws only the first N scenes.
- Quick fixes (`POST /projects/{id}/scenes/{n}/fix`: redraw, wrong characters, wrong place, wrong
  outfit) correct the scene, store an optional Vietnamese note (`fix_note_vi`, translated into
  `fix_note` and placed right after the action in the image prompt), bump `take` (a new seed) and
  queue an images-only job for that scene alone (`preview_scene_indexes`). `POST .../scenes/bulk`
  changes the place of many scenes and/or redraws them. Partial previews keep the other scenes'
  images and the manifest's earlier revision.
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

## Account branding (logo)

A third job type stamps the account's logo on a video (`job_action = "logo_branding"`), on
the same queue, quota, cancellation, retention and ownership checks as the other jobs.

```text
upload + rights confirmation -> snapshot logo/settings into the job -> probe
-> find the old logo: template tracking when the user uploaded a sample of it
   (pipeline/logo_tracking.py, follows moving logos) or persistent-edge detection
   (pipeline/logo_detection.py, per 6 s window, tracks across windows)
-> placements per time segment, keyframed x(t)/y(t) for moving logos (pipeline/logo_overlay.py)
-> one FFmpeg pass (delogo blur, static logos only), or decode -> OpenCV inpainting of the
   old logo's pixels (pipeline/logo_inpaint.py) -> encode, both with the scaled logo + opacity
```

- `domain/branding.py` holds the settings contract; `infrastructure/branding_store.py` keeps one
  row per user (`user_branding_configs`) and the logo under `work/users/<user>/branding/`.
- `application/branding_routes.py` exposes `/api/v1/me`, `/api/v1/me/branding[/logo]` and
  `/api/v1/me/branding/template`. There is no separate logo job: translation batches and comic
  projects take `apply_logo`, and `stamp_account_logo` (main.py) runs `BrandingJobRunner.stamp`
  on the finished video in place. Translation videos detect an existing logo first; comic videos
  use the default corner. A stamping failure keeps the unbranded video and records `branding.error`.
- No logo found -> default corner (top-right unless configured).

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
