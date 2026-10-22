# GPU animation worker API

VidTrans can animate shots on your own GPU server (Wan, HunyuanVideo, a ComfyUI
workflow…). The worker only has to implement these three HTTP endpoints; VidTrans
handles storyboard, keyframes, retries, QC, fallback, normalisation and rendering.

Configure VidTrans with:

| Variable | Meaning |
| --- | --- |
| `VIDTRANS_ANIMATION_WORKER_URL` | Base URL, e.g. `http://gpu-box:9000` |
| `VIDTRANS_ANIMATION_WORKER_TOKEN` | Optional bearer token sent as `Authorization: Bearer …` |
| `VIDTRANS_ANIMATION_WORKER_MODEL` | Label stored with every clip and used in the cache key (change it when you change the model) |
| `VIDTRANS_ANIMATION_WORKER_MAX_SECONDS` | Longest clip the model can produce (default 8); longer shots are slowed ≤ 25 % and hold the last frame |
| `VIDTRANS_ANIMATION_CONCURRENCY` | Concurrent AI video generations across all VidTrans jobs (default 1) |

## `POST /v1/animate`

```json
{
  "image_base64": "<PNG keyframe>",
  "prompt": "anime ... Minh walks slowly toward the camera, smooth tracking shot ...",
  "negative_prompt": "blurry, distorted face, extra fingers",
  "duration_seconds": 5.0,
  "fps": 24,
  "width": 1280,
  "height": 720,
  "seed": 12345,
  "motion_strength": 0.45,
  "idempotency_key": "<stable per attempt>"
}
```

Respond `{"job_id": "…", "status": "queued"}`. Return the same `job_id` for a
repeated `idempotency_key`, so a retried request never renders twice.

## `GET /v1/jobs/{job_id}`

```json
{"status": "queued|running|completed|failed", "progress": 0.4, "error": null,
 "model": "wan2.1-i2v-14b", "gpu_seconds": 96.0}
```

`gpu_seconds` is recorded as the shot's cost and counted against the render's
AI budget (`animation_budget_seconds`).

## `GET /v1/jobs/{job_id}/video`

The finished clip as `video/mp4` (any resolution/fps; VidTrans normalises it).

## Errors

* HTTP 401/403/404 → treated as configuration errors: no retry, the shot falls back
  to the 2.5D camera move.
* Other HTTP errors, timeouts, `"status": "failed"` → the attempt is retried with a
  new seed up to `max_animation_attempts`, then falls back.
