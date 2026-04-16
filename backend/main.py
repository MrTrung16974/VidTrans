import asyncio
import json
import logging
import os
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path
from typing import Any, Optional

import whisper
from deep_translator import GoogleTranslator
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from gtts import gTTS

try:
    import edge_tts
except Exception:  # pragma: no cover
    edge_tts = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
WORK_DIR = BASE_DIR / "work"

for path in (UPLOAD_DIR, OUTPUT_DIR, WORK_DIR):
    path.mkdir(parents=True, exist_ok=True)

HOMEBREW_BIN = "/opt/homebrew/bin"
os.environ["PATH"] = f"{HOMEBREW_BIN}{os.pathsep}{os.environ.get('PATH', '')}"


def _resolve_binary(name: str) -> str:
    candidates = [
        shutil.which(name),
        str((BASE_DIR.parent / "ffmpeg" / name).resolve()),
        str((BASE_DIR.parent / "ffmpeg").resolve()),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return name


FFMPEG = _resolve_binary("ffmpeg")
FFPROBE = _resolve_binary("ffprobe")

VOICE_OPTIONS = {
    "female": "vi-VN-HoaiMyNeural",
    "male": "vi-VN-NamMinhNeural",
}
SOURCE_LANG = "zh-CN"
TARGET_LANG = "vi"
TRANSLATE_MAX_CHARS = 4000
DEFAULT_SUB_STYLE = {
    "font_name": "Arial",
    "font_size": 36,
    "primary_color": "&H00FFFFFF",
    "outline_color": "&H00000000",
    "back_color": "&H66000000",
    "outline": 3,
    "shadow": 0,
    "alignment": 2,
    "margin_v": 210,
}
FONT_CANDIDATES = [
    "Arial",
    "Helvetica",
    "Arial Unicode MS",
    "DejaVu Sans",
]

app = FastAPI(title="VidTrans", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: dict[str, dict[str, Any]] = {}
_whisper_models: dict[str, Any] = {}


def ff_escape_path(path: Path) -> str:
    escaped = str(path).replace("\\", "/")
    for src, dest in {
        ":": "\\:",
        "'": "\\'",
        ",": "\\,",
        "[": "\\[",
        "]": "\\]",
    }.items():
        escaped = escaped.replace(src, dest)
    return escaped


def ff_escape_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for src, dest in {
        ":": "\\:",
        "'": "\\'",
        ",": "\\,",
        "[": "\\[",
        "]": "\\]",
    }.items():
        escaped = escaped.replace(src, dest)
    return escaped


def run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("Running ffmpeg command: %s", " ".join(args))
    try:
        return subprocess.run(args, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed with stderr:\n%s", exc.stderr)
        raise


def run_cmd(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("Running command: %s", " ".join(args))
    return subprocess.run(args, check=check, capture_output=True, text=True)


def format_time(seconds: float, for_ass: bool = False) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        secs += 1
        millis = 0
    if for_ass:
        centis = millis // 10
        return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def wrap_text(text: str, width: int = 34) -> str:
    words = " ".join(text.split()).split()
    lines: list[str] = []
    current = ""
    for word in words:
        extra = len(word) + (1 if current else 0)
        if len(current) + extra <= width:
            current += (" " if current else "") + word
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:2] if len(lines) > 2 else lines)


def find_font_file(preferred_name: str) -> Optional[str]:
    fc_match = shutil.which("fc-match")
    if not fc_match:
        return None
    for candidate in [preferred_name, *FONT_CANDIDATES]:
        try:
            result = run_cmd([fc_match, "-f", "%{file}", candidate], check=False)
            font_file = (result.stdout or "").strip()
            if font_file and Path(font_file).exists():
                return font_file
        except Exception:
            continue
    return None


def split_text(text: str, max_len: int = TRANSLATE_MAX_CHARS) -> list[str]:
    return [text[i : i + max_len] for i in range(0, len(text), max_len)] or [text]


def translate_long(text: str, translator: GoogleTranslator) -> str:
    translated_parts = []
    for part in split_text(text):
        clean = " ".join(part.split())
        if not clean:
            continue
        last_error = None
        for _ in range(3):
            try:
                translated_parts.append(translator.translate(clean) or clean)
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
                logger.warning("Translate retry due to error: %s", exc)
        else:
            logger.exception("Translate failed, falling back to source text")
            translated_parts.append(clean if last_error else clean)
    return " ".join(translated_parts).strip()


def translate_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    translated_segments: list[dict[str, Any]] = []
    translator = GoogleTranslator(source=SOURCE_LANG, target=TARGET_LANG)
    for segment in segments:
        source_text = segment["text"].strip()
        if not source_text:
            continue
        try:
            vi_text = translate_long(source_text, translator)
        except Exception:
            vi_text = source_text
        translated_segments.append(
            {
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "source_text": source_text,
                "text": vi_text.strip() or source_text,
            }
        )
    return translated_segments


def write_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = wrap_text(segment["text"])
        if not text:
            continue
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_time(segment['start'])} --> {format_time(segment['end'])}",
                    text,
                ]
            )
        )
    output_path.write_text("\n\n".join(blocks), encoding="utf-8")


def write_ass(segments: list[dict[str, Any]], output_path: Path, style: dict[str, Any]) -> None:
    style_line = (
        "Style: Default,{font_name},{font_size},{primary_color},{primary_color},"
        "{outline_color},{back_color},0,0,0,0,100,100,0,0,1,{outline},{shadow},"
        "{alignment},10,10,{margin_v},1"
    ).format(**style)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1280",
        "PlayResY: 720",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    for segment in segments:
        text = wrap_text(segment["text"]).replace("\n", "\\N")
        text = text.replace("{", "\\{").replace("}", "\\}")
        lines.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=format_time(segment["start"], for_ass=True),
                end=format_time(segment["end"], for_ass=True),
                text=text,
            )
        )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def get_media_duration(path: Path) -> float:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float((result.stdout or "0").strip() or 0)


def get_video_fps(path: Path) -> float:
    result = run_cmd(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    raw = (result.stdout or "").strip()
    if "/" in raw:
        num, denom = raw.split("/", 1)
        if float(denom) != 0:
            return float(num) / float(denom)
    try:
        return float(raw)
    except Exception:
        return 30.0


def get_whisper_model(model_name: str) -> Any:
    if model_name not in _whisper_models:
        logger.info("Loading whisper model: %s", model_name)
        _whisper_models[model_name] = whisper.load_model(model_name)
    return _whisper_models[model_name]


def transcribe_chinese_video(model: Any, video_path: Path) -> list[dict[str, Any]]:
    result = model.transcribe(
        str(video_path),
        language="zh",
        fp16=False,
        temperature=0,
        best_of=5,
        beam_size=5,
        condition_on_previous_text=True,
        verbose=False,
    )
    segments = normalize_segments(result.get("segments", []))
    if segments:
        return segments

    logger.warning("No transcript segments with forced zh, retrying with simpler whisper settings")
    result = model.transcribe(
        str(video_path),
        language="zh",
        fp16=False,
        verbose=False,
    )
    segments = normalize_segments(result.get("segments", []))
    if segments:
        return segments

    logger.warning("No transcript segments with forced zh, retrying with auto language detection")
    result = model.transcribe(
        str(video_path),
        fp16=False,
        verbose=False,
    )
    return normalize_segments(result.get("segments", []))


def update_job(job_id: str, **fields: Any) -> None:
    job = jobs.setdefault(job_id, {})
    job.update(fields)


async def tts_edge_sync(text: str, output_path: Path, voice_name: str, rate: str) -> None:
    if edge_tts is None:
        raise RuntimeError("edge-tts is not available")
    communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
    await communicate.save(str(output_path))


def tts_gtts_sync(text: str, output_path: Path, slow: bool = False) -> None:
    gTTS(text=text, lang="vi", slow=slow).save(str(output_path))


def synthesize_tts_segments(
    segments: list[dict[str, Any]],
    work_dir: Path,
    voice_type: str,
    speech_rate: float,
) -> list[Path]:
    tts_dir = work_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    voice_name = VOICE_OPTIONS.get(voice_type, VOICE_OPTIONS["female"])
    rate_percent = int((speech_rate - 1.0) * 100)
    rate_string = f"{rate_percent:+d}%"
    paths: list[Path] = []
    for index, segment in enumerate(segments):
        text = " ".join(segment["text"].split())
        if not text:
            continue
        output_path = tts_dir / f"{index:04d}.mp3"
        try:
            tts_gtts_sync(text, output_path, slow=speech_rate < 0.95)
        except Exception as exc:
            logger.warning("gTTS failed, fallback to edge-tts: %s", exc)
            asyncio.run(tts_edge_sync(text, output_path, voice_name, rate_string))
        paths.append(output_path)
        segment["tts_path"] = output_path
    return paths


def build_voice_track(segments: list[dict[str, Any]], work_dir: Path, video_duration: float) -> Path:
    voice_track = work_dir / "voice_track.wav"
    input_args: list[str] = []
    filter_parts = []
    labels = []
    for index, segment in enumerate(segments):
        tts_path = segment.get("tts_path")
        if not tts_path:
            continue
        delay_ms = max(0, int(segment["start"] * 1000))
        input_args.extend(["-i", str(tts_path)])
        filter_parts.append(f"[{index}:a]adelay={delay_ms}|{delay_ms},volume=1.4[a{index}]")
        labels.append(f"[a{index}]")
    if not labels:
        raise RuntimeError("No TTS segments were generated")
    filter_parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,alimiter=limit=0.95[out]")
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-t",
            f"{video_duration:.3f}",
            str(voice_track),
        ]
    )
    return voice_track


def extract_original_audio(video_path: Path, output_path: Path) -> Path:
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            str(output_path),
        ]
    )
    return output_path


def mix_audio(
    *,
    mode: int,
    video_path: Path,
    work_dir: Path,
    voice_track: Optional[Path],
    background_music: Optional[Path],
    keep_original_audio: bool,
    original_audio_volume: float,
    music_volume: float,
) -> Optional[Path]:
    if mode == 1:
        return None

    output_audio = work_dir / "mixed_audio.wav"
    input_args: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []
    input_index = 0

    if voice_track:
        input_args.extend(["-i", str(voice_track)])
        filter_parts.append(f"[{input_index}:a]volume=1.0,alimiter=limit=0.95[v]")
        labels.append("[v]")
        input_index += 1

    if keep_original_audio:
        original_audio = extract_original_audio(video_path, work_dir / "original_audio.wav")
        input_args.extend(["-i", str(original_audio)])
        filter_parts.append(f"[{input_index}:a]volume={original_audio_volume:.2f}[o]")
        labels.append("[o]")
        input_index += 1

    if background_music:
        duration = get_media_duration(video_path)
        looped_music = work_dir / "looped_music.wav"
        run_ffmpeg(
            [
                FFMPEG,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(background_music),
                "-t",
                f"{duration:.3f}",
                "-af",
                f"volume={music_volume:.2f},afade=t=in:st=0:d=1.5,afade=t=out:st={max(duration - 2.0, 0):.3f}:d=2",
                str(looped_music),
            ]
        )
        input_args.extend(["-i", str(looped_music)])
        filter_parts.append(f"[{input_index}:a]volume=1.0[m]")
        labels.append("[m]")
        input_index += 1

    if not labels:
        return None

    filter_parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,loudnorm=I=-16:LRA=11:TP=-1.5[out]"
    )
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            *input_args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            str(output_audio),
        ]
    )
    return output_audio


def build_drawtext_filter(segments: list[dict[str, Any]], subtitle_style: dict[str, Any]) -> str:
    font_file = find_font_file(subtitle_style.get("font_name", "Arial"))
    font_size = subtitle_style.get("font_size", 22)
    margin_v = subtitle_style.get("margin_v", 30)
    filters = []
    for segment in segments:
        text = wrap_text(segment["text"])
        if not text:
            continue
        escaped_text = ff_escape_text(text).replace("\n", "\\n")
        parts = [
            f"text='{escaped_text}'",
            f"fontsize={font_size}",
            "fontcolor=white",
            "line_spacing=6",
            "x=(w-text_w)/2",
            f"y=h-text_h-{margin_v}",
            "box=1",
            "boxcolor=black@0.45",
            "boxborderw=12",
            f"enable='between(t,{segment['start']:.3f},{segment['end']:.3f})'",
        ]
        if font_file:
            parts.insert(0, f"fontfile='{ff_escape_path(Path(font_file))}'")
        filters.append(f"drawtext={':'.join(parts)}")
    return ",".join(filters)


def _load_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Pillow is required for subtitle rendering fallback. Please run `pip install -r requirements.txt`."
        ) from exc
    return Image, ImageDraw, ImageFont


def burn_subtitles_with_pillow(
    video_path: Path,
    output_path: Path,
    audio_path: Optional[Path],
    segments: list[dict[str, Any]],
    subtitle_style: dict[str, Any],
    work_dir: Path,
) -> None:
    Image, ImageDraw, ImageFont = _load_pillow()
    frames_dir = work_dir / "frames"
    rendered_dir = work_dir / "rendered_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    fps = get_video_fps(video_path)
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
            "-vsync",
            "0",
            str(frames_dir / "frame_%06d.png"),
        ]
    )

    font_size = int(subtitle_style.get("font_size", 22))
    margin_v = int(subtitle_style.get("margin_v", 30))
    font_path = find_font_file(subtitle_style.get("font_name", "Arial"))
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    frame_paths = sorted(frames_dir.glob("frame_*.png"))

    for index, frame_path in enumerate(frame_paths):
        timestamp = index / fps
        active = next((seg for seg in segments if seg["start"] <= timestamp <= seg["end"]), None)
        output_frame = rendered_dir / frame_path.name
        if not active:
            shutil.copy2(frame_path, output_frame)
            continue

        image = Image.open(frame_path).convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        text = wrap_text(active["text"])
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=6, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (image.width - text_w) / 2
        y = image.height - text_h - margin_v
        pad_x = 20
        pad_y = 12
        draw.rounded_rectangle(
            (x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y),
            radius=18,
            fill=(0, 0, 0, 140),
        )
        draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 255), spacing=6, align="center")
        combined = Image.alpha_composite(image, overlay).convert("RGB")
        combined.save(output_frame)

    rendered_video = work_dir / "rendered_video.mp4"
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-framerate",
            f"{fps:.6f}",
            "-i",
            str(rendered_dir / "frame_%06d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            str(rendered_video),
        ]
    )

    mux_source = audio_path or video_path
    run_ffmpeg(
        [
            FFMPEG,
            "-y",
            "-i",
            str(rendered_video),
            "-i",
            str(mux_source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
    )


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    output_path: Path,
    audio_path: Optional[Path],
    segments: list[dict[str, Any]],
    subtitle_style: dict[str, Any],
    work_dir: Path,
) -> None:
    subtitle_filter = f"subtitles=filename='{ff_escape_path(subtitle_path)}'"
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(video_path),
    ]
    if audio_path:
        cmd.extend(["-i", str(audio_path)])
    cmd.extend(
        [
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
        ]
    )
    if audio_path:
        cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"])
    else:
        cmd.extend(["-c:a", "copy"])
    cmd.append(str(output_path))
    try:
        run_ffmpeg(cmd)
    except subprocess.CalledProcessError as exc:
        if "No such filter: 'subtitles'" not in (exc.stderr or ""):
            raise
        logger.warning("ffmpeg subtitles filter is unavailable, falling back to drawtext rendering")
        fallback_filter = build_drawtext_filter(segments, subtitle_style)
        fallback_cmd = [
            FFMPEG,
            "-y",
            "-i",
            str(video_path),
        ]
        if audio_path:
            fallback_cmd.extend(["-i", str(audio_path)])
        fallback_cmd.extend(
            [
                "-vf",
                fallback_filter,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
            ]
        )
        if audio_path:
            fallback_cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"])
        else:
            fallback_cmd.extend(["-c:a", "copy"])
        fallback_cmd.append(str(output_path))
        try:
            run_ffmpeg(fallback_cmd)
        except subprocess.CalledProcessError as fallback_exc:
            if "No such filter: 'drawtext'" not in (fallback_exc.stderr or ""):
                raise
            logger.warning("ffmpeg drawtext filter is unavailable, falling back to Pillow frame rendering")
            burn_subtitles_with_pillow(video_path, output_path, audio_path, segments, subtitle_style, work_dir)


def normalize_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for segment in raw_segments:
        text = " ".join((segment.get("text") or "").split())
        if not text:
            continue
        start = max(0.0, float(segment.get("start", 0)))
        end = max(start + 0.1, float(segment.get("end", start + 0.1)))
        normalized.append({"start": start, "end": end, "text": text})
    return normalized


def process_video(
    *,
    job_id: str,
    video_path: Path,
    mode: int,
    whisper_model: str,
    voice_type: str,
    speech_rate: float,
    background_music_path: Optional[Path],
    keep_original_audio: bool,
    original_audio_volume: float,
    music_volume: float,
    subtitle_style: Optional[dict[str, Any]],
) -> None:
    work_dir = WORK_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    subtitle_style = {**DEFAULT_SUB_STYLE, **(subtitle_style or {})}
    try:
        update_job(job_id, status="processing", step="transcribing", progress=0.1)
        model = get_whisper_model(whisper_model)
        segments = transcribe_chinese_video(model, video_path)
        if not segments:
            raise RuntimeError(
                "Whisper did not detect any speech segments in the video. "
                "Please verify the source has audible dialogue and try a larger Whisper model if needed."
            )

        transcript_path = work_dir / "transcript.json"
        transcript_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

        update_job(job_id, step="translating", progress=0.35)
        translated_segments = translate_segments(segments)

        srt_path = OUTPUT_DIR / f"{job_id}.srt"
        ass_path = work_dir / f"{job_id}.ass"
        write_srt(translated_segments, srt_path)
        write_ass(translated_segments, ass_path, subtitle_style)

        audio_path = None
        video_duration = get_media_duration(video_path)
        if mode in (2, 3):
            update_job(job_id, step="tts", progress=0.55)
            synthesize_tts_segments(translated_segments, work_dir, voice_type, speech_rate)
            voice_track = build_voice_track(translated_segments, work_dir, video_duration)

            update_job(job_id, step="mixing-audio", progress=0.75)
            audio_path = mix_audio(
                mode=mode,
                video_path=video_path,
                work_dir=work_dir,
                voice_track=voice_track,
                background_music=background_music_path if mode == 3 else None,
                keep_original_audio=keep_original_audio,
                original_audio_volume=original_audio_volume,
                music_volume=music_volume,
            )

        output_video = OUTPUT_DIR / f"output_{job_id}.mp4"
        update_job(job_id, step="rendering", progress=0.9)
        burn_subtitles(video_path, ass_path, output_video, audio_path, translated_segments, subtitle_style, work_dir)

        update_job(
            job_id,
            status="completed",
            step="completed",
            progress=1.0,
            output_video=str(output_video.name),
            subtitle_file=str(srt_path.name),
            transcript_file=str(transcript_path.relative_to(BASE_DIR)),
        )
    except Exception as exc:
        logger.exception("Video processing failed for job %s", job_id)
        update_job(job_id, status="failed", step="failed", error=str(exc))
    finally:
        if video_path.exists():
            video_path.unlink(missing_ok=True)
        if background_music_path and background_music_path.exists():
            background_music_path.unlink(missing_ok=True)


@app.post("/process-video")
@app.post("/convert")
async def process_video_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: int = Form(...),
    voice_type: str = Form("female"),
    speech_rate: float = Form(1.0),
    background_music: UploadFile | None = File(default=None),
    whisper_model: str = Form("base"),
    keep_original_audio: bool = Form(True),
    original_audio_volume: float = Form(0.18),
    music_volume: float = Form(0.28),
    subtitle_style: str | None = Form(default=None),
) -> JSONResponse:
    if mode not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="mode must be 1, 2, or 3")
    if mode == 3 and background_music is None:
        raise HTTPException(status_code=400, detail="background_music is required for mode 3")

    job_id = uuid.uuid4().hex[:8]
    video_ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    video_path = UPLOAD_DIR / f"{job_id}{video_ext}"
    with video_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    background_music_path = None
    if background_music is not None:
        music_ext = Path(background_music.filename or "music.mp3").suffix or ".mp3"
        background_music_path = UPLOAD_DIR / f"{job_id}_bgm{music_ext}"
        with background_music_path.open("wb") as buffer:
            shutil.copyfileobj(background_music.file, buffer)

    style_payload = None
    if subtitle_style:
        style_payload = json.loads(subtitle_style)

    jobs[job_id] = {
        "status": "queued",
        "step": "queued",
        "progress": 0.0,
        "mode": mode,
        "filename": file.filename,
    }
    background_tasks.add_task(
        process_video,
        job_id=job_id,
        video_path=video_path,
        mode=mode,
        whisper_model=whisper_model,
        voice_type=voice_type,
        speech_rate=speech_rate,
        background_music_path=background_music_path,
        keep_original_audio=keep_original_audio,
        original_audio_volume=original_audio_volume,
        music_volume=music_volume,
        subtitle_style=style_payload,
    )

    return JSONResponse(
        {
            "job_id": job_id,
            "processing_status": "queued",
            "status_url": f"/status/{job_id}",
            "video_url": None,
            "subtitle_url": None,
        }
    )


@app.get("/status/{job_id}")
def get_status(job_id: str) -> JSONResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    response = dict(job)
    if job.get("status") == "completed":
        if job.get("output_video"):
            response["video_url"] = f"/download/{job['output_video']}"
        if job.get("subtitle_file"):
            response["subtitle_url"] = f"/download/{job['subtitle_file']}"
    return JSONResponse(response)


@app.get("/download/{filename}")
def download(filename: str) -> FileResponse:
    target = OUTPUT_DIR / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
