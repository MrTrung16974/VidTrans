"""Manual SIT smoke using live translation/TTS providers and the local FFmpeg.

This is intentionally not auto-discovered by unittest. It performs network calls
with synthetic text and writes only to a temporary directory.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from gtts import gTTS

from infrastructure.vietnamese_translator import GoogleVietnameseTranslator
from main import DEFAULT_SUB_STYLE, burn_subtitles


def run(command: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def main() -> None:
    output_dir = Path(tempfile.mkdtemp(prefix="vidtrans-real-smoke-"))
    source_video = output_dir / "source.mp4"
    speech_path = output_dir / "speech.mp3"
    subtitle_path = output_dir / "subtitle.srt"
    output_video = output_dir / "output.mp4"
    timings: dict[str, float] = {}

    started = time.monotonic()
    translated = GoogleVietnameseTranslator(
        source_language="zh-CN",
        target_language="vi",
        timeout=15,
        min_interval=0,
    ).translate("你好，欢迎来到视频测试。")
    timings["translation_seconds"] = round(time.monotonic() - started, 3)
    if not translated.strip():
        raise RuntimeError("live translation returned empty text")

    started = time.monotonic()
    gTTS(text=translated, lang="vi", slow=False).save(str(speech_path))
    timings["tts_seconds"] = round(time.monotonic() - started, 3)

    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:03,800\n" + translated.strip() + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source_video),
        ]
    )

    started = time.monotonic()
    burn_subtitles(
        source_video,
        subtitle_path,
        output_video,
        speech_path,
        [{"start": 0.0, "end": 3.8, "text": translated}],
        dict(DEFAULT_SUB_STYLE),
        output_dir,
        video_fps=24.0,
    )
    timings["ffmpeg_seconds"] = round(time.monotonic() - started, 3)

    probe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(output_video),
        ]
    )
    metadata = json.loads(probe.stdout)
    stream_types = {stream.get("codec_type") for stream in metadata.get("streams", [])}
    if stream_types != {"video", "audio"}:
        raise RuntimeError(f"unexpected output streams: {sorted(stream_types)}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "translation_provider": "google/mymemory fallback chain",
                "tts_provider": "gTTS",
                "translated_characters": len(translated),
                "timings": timings,
                "probe": metadata,
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
