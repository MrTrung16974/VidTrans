from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import whisper
from deep_translator import GoogleTranslator
import subprocess
import os
import uuid
import shutil
import asyncio
from pathlib import Path
from typing import Optional
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= PATHS =========
# Ưu tiên ffmpeg ở PATH hiện tại. Có thể override bằng biến môi trường FFMPEG_BIN.
HOMEBREW_BIN = "/opt/homebrew/bin"
os.environ["PATH"] = HOMEBREW_BIN + os.pathsep + os.environ.get("PATH", "")
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or FFMPEG.replace("ffmpeg", "ffprobe")

UPLOAD_DIR = Path("uploads").resolve()
OUTPUT_DIR = Path("outputs").resolve()
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

jobs: dict = {}

# ========= DRAWTEXT HELPERS =========
FONT_FILE = "/System/Library/Fonts/Supplemental/Arial.ttf"
# Có thể đổi sang font Unicode khác nếu cần.
# FONT_FILE = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def ff_escape_text(text: str) -> str:
    """Escape text cho FFmpeg drawtext."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace(",", "\\,")
    text = text.replace("%", "\\%")
    text = text.replace("'", "\\'")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("(", "\\(")
    text = text.replace(")", "\\)")
    text = text.replace("\n", "\\n")
    return text


def ff_escape_path(path: str) -> str:
    """Escape path cho FFmpeg filter args."""
    return path.replace("\\", "/").replace(":", "\\:")


def _parse_srt_blocks(srt_text: str):
    blocks = re.split(r"\n\n+", srt_text.strip())
    time_re = re.compile(
        r"(\d+):(\d+):(\d+)[,\.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,\.](\d+)"
    )

    def ts(*g):
        return int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3]) / 1000

    entries = []
    for blk in blocks:
        lines = blk.strip().splitlines()
        if len(lines) < 2:
            continue
        m = None
        for line in lines:
            m = time_re.match(line.strip())
            if m:
                break
        if not m:
            continue
        start = ts(m.group(1), m.group(2), m.group(3), m.group(4))
        end = ts(m.group(5), m.group(6), m.group(7), m.group(8))
        txt_lines = [l for l in lines if not time_re.match(l.strip()) and not l.strip().isdigit()]
        text = " ".join(re.sub(r"\{[^}]*\}", "", l).strip() for l in txt_lines if l.strip())
        if text:
            entries.append((start, end, text))
    return entries


# ========= SUBTITLE BURN HELPER =========
def burn_subtitles(ffmpeg_bin: str, video_in: str, srt_path: str, video_out: str, extra_args: list = None):
    """
    Burn subtitles bằng drawtext בלבד.
    Yêu cầu FFmpeg build có filter drawtext.
    extra_args: ví dụ ["-an"] hoặc ["-map", "0:v", "-map", "0:a", "-c:a", "copy"]
    """
    if extra_args is None:
        extra_args = []

    abs_srt = str(Path(srt_path).resolve())
    srt_text = open(abs_srt, encoding="utf-8").read()
    entries = _parse_srt_blocks(srt_text)

    if not entries:
        raise RuntimeError("burn_subtitles: không parse được SRT")

    if not Path(FONT_FILE).exists():
        raise RuntimeError(f"Không tìm thấy font: {FONT_FILE}")

    base_encode = ["-c:v", "libx264", "-crf", "23", "-preset", "fast"]

    filters = []
    for start, end, text in entries:
        ft = ff_escape_text(text)
        filters.append(
            f"drawtext=fontfile={ff_escape_path(FONT_FILE)}:"
            f"text={ft}:"
            f"fontcolor=white:fontsize=24:borderw=2:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-text_h-30:"
            f"enable=between(t\\,{start}\\,{end})"
        )

    vf = ",".join(filters)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        video_in,
        "-vf",
        vf,
    ] + base_encode + extra_args + [video_out]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg drawtext failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")


# ========= GIỌNG ĐỌC TIẾNG VIỆT =========
VOICE_OPTIONS = {
    "nu-hay": "vi-VN-HoaiMyNeural",
    "nam-hay": "vi-VN-NamMinhNeural",
}

# ========= HELPERS =========
def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def split_text(text, max_len=4000):
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


def translate_long(text, translator):
    return " ".join([translator.translate(p) for p in split_text(text)])


def wrap_text(text, max_chars=22):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + (1 if current else 0) <= max_chars:
            current += (" " if current else "") + word
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def get_font_size(text, base_size=14, min_size=10, max_chars=22):
    ml = max((len(l) for l in text.split("\n")), default=1)
    return base_size if ml <= max_chars else max(int(base_size * max_chars / ml), min_size)


def upd(job_id, **kw):
    jobs[job_id].update(kw)


# ========= edge-tts =========
def tts_edge_sync(text: str, voice: str, output_mp3: str, rate: str = "+0%"):
    import edge_tts

    text = text.strip()
    if not text:
        text = "."

    if rate and not rate.startswith(("+", "-")):
        rate = "+" + rate

    async def _run():
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(output_mp3)
                if os.path.exists(output_mp3) and os.path.getsize(output_mp3) > 0:
                    return
            except Exception as e:
                if attempt == 2:
                    raise e
                await asyncio.sleep(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    except Exception:
        loop.close()
        try:
            from gtts import gTTS
            gTTS(text=text, lang="vi").save(output_mp3)
        except Exception as e2:
            raise RuntimeError(f"edge-tts và gTTS đều thất bại: {e2}")
        return
    finally:
        if not loop.is_closed():
            loop.close()


# ========= CORE =========
# output_mode:
#   "full"       — dịch tiếng Việt + lồng tiếng TTS + phụ đề VI
#   "sub_vi"     — chỉ phụ đề tiếng Việt, giữ audio gốc
#   "sub_origin" — chỉ phụ đề ngôn ngữ gốc (không dịch), giữ audio gốc

def process_video(
    job_id: str,
    video_path: str,
    bgm_path,
    bgm_volume: float,
    tts_volume: float,
    bgm_loop: bool,
    source_lang: str,
    model_size: str,
    voice_key: str,
    tts_rate: str,
    output_mode: str,
):
    workdir = UPLOAD_DIR / job_id
    workdir.mkdir(exist_ok=True)
    output_srt = str(workdir / "output.srt")
    mixed_audio = str(workdir / "mixed_audio.aac")
    output_video = str(OUTPUT_DIR / f"output_{job_id[:8]}.mp4")
    voice = VOICE_OPTIONS.get(voice_key, VOICE_OPTIONS["nu-hay"])
    use_bgm = bgm_path is not None and output_mode == "full"

    encode_args = ["-c:v", "libx264", "-crf", "23", "-preset", "fast"]
    try:
        t = subprocess.run(
            [FFMPEG, "-f", "lavfi", "-i", "nullsrc", "-t", "0.1",
             "-c:v", "h264_videotoolbox", "-f", "null", "-"],
            capture_output=True, timeout=5
        )
        if t.returncode == 0:
            encode_args = ["-c:v", "h264_videotoolbox", "-q:v", "50"]
    except Exception:
        pass

    try:
        upd(job_id, status="running", step="Đang load Whisper model...", progress=5)
        model = whisper.load_model(model_size)

        upd(job_id, step="Đang nhận diện giọng nói...", progress=15)
        result = model.transcribe(video_path, language=source_lang)
        segments = result["segments"]
        upd(job_id, step=f"Nhận diện xong {len(segments)} đoạn", progress=35)

        if output_mode in ("full", "sub_vi"):
            upd(job_id, step="Đang dịch sang tiếng Việt...", progress=38)
            src_map = {"zh": "zh-CN", "en": "en", "ja": "ja", "ko": "ko", "fr": "fr"}
            translator = GoogleTranslator(source=src_map.get(source_lang, source_lang), target="vi")
            final_segments = []
            for seg in segments:
                text = seg["text"].strip()
                try:
                    vi = translate_long(text, translator)
                except Exception:
                    vi = text
                final_segments.append({"start": seg["start"], "end": seg["end"], "text": vi})
        else:
            final_segments = [
                {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                for s in segments
            ]

        upd(job_id, step="Đang tạo phụ đề...", progress=50)
        with open(output_srt, "w", encoding="utf-8") as f:
            for i, seg in enumerate(final_segments, 1):
                wrapped = wrap_text(seg["text"])
                font_size = get_font_size(wrapped)
                tagged = f"{{\\fs{font_size}}}{wrapped}"
                f.write(f"{i}\n{format_time(seg['start'])} --> {format_time(seg['end'])}\n{tagged}\n\n")

        if output_mode in ("sub_vi", "sub_origin"):
            upd(job_id, step="Đang ghép phụ đề vào video...", progress=75)
            burn_subtitles(
                FFMPEG,
                video_path,
                output_srt,
                output_video,
                extra_args=["-an"],
            )
            shutil.rmtree(workdir, ignore_errors=True)
            upd(job_id, status="done", step="Hoàn thành!", progress=100, output_file=f"output_{job_id[:8]}.mp4")
            return

        upd(job_id, step="Đang tạo giọng đọc tiếng Việt...", progress=55)
        segment_files = []

        for i, seg in enumerate(final_segments):
            text = seg["text"].strip()
            if not text:
                continue
            mp3 = str(workdir / f"seg_{i}.mp3")
            wav = str(workdir / f"seg_{i}.wav")

            tts_edge_sync(text, voice, mp3, rate=tts_rate)
            subprocess.run(
                [FFMPEG, "-y", "-i", mp3, "-ar", "44100", "-ac", "2", wav],
                check=True, capture_output=True
            )
            os.remove(mp3)
            segment_files.append((wav, seg["start"]))

            prog = 55 + int((len(segment_files) / max(len(final_segments), 1)) * 20)
            upd(job_id, step=f"TTS {len(segment_files)}/{len(final_segments)}...", progress=prog)

        upd(job_id, step="Đang mix audio...", progress=78)

        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        total_dur = float(probe.stdout.strip())

        BATCH = 50
        tts_track = str(workdir / "tts_track.wav")

        subprocess.run([
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(total_dur),
            "-ar", "44100", "-ac", "2",
            tts_track,
        ], check=True, capture_output=True)

        for batch_start in range(0, len(segment_files), BATCH):
            batch = segment_files[batch_start:batch_start + BATCH]
            b_inputs = ["-i", tts_track]
            b_filter = []
            prev = "[0]"
            for j, (wav, start_sec) in enumerate(batch):
                idx = j + 1
                delay_ms = int(start_sec * 1000)
                b_filter.append(f"[{idx}]volume={tts_volume},adelay={delay_ms}|{delay_ms}[s{j}]")
                b_filter.append(f"{prev}[s{j}]amix=inputs=2:duration=first:normalize=0[m{j}]")
                prev = f"[m{j}]"
                b_inputs += ["-i", wav]

            fcomplex = ";".join(b_filter) + f";{prev}acopy[out]"
            tmp_out = str(workdir / "tts_track_tmp.wav")
            subprocess.run(
                [FFMPEG, "-y"] + b_inputs + [
                    "-filter_complex", fcomplex,
                    "-map", "[out]", "-ar", "44100", "-ac", "2", tmp_out,
                ], check=True, capture_output=True
            )
            os.replace(tmp_out, tts_track)

            prog = 78 + int(((batch_start + len(batch)) / max(len(segment_files), 1)) * 8)
            upd(job_id, step=f"Mix TTS {batch_start + len(batch)}/{len(segment_files)}...", progress=prog)

        if use_bgm:
            upd(job_id, step="Đang mix nhạc nền...", progress=87)
            bgm_args = ["-stream_loop", "-1", "-i", bgm_path] if bgm_loop else ["-i", bgm_path]
            subprocess.run(
                [FFMPEG, "-y"] + bgm_args + ["-i", tts_track] + [
                    "-filter_complex",
                    f"[0]volume={bgm_volume}[bg];[bg][1]amix=inputs=2:duration=second:normalize=0[out]",
                    "-map", "[out]", "-ar", "44100", "-ac", "2", mixed_audio,
                ], check=True,
            )
        else:
            shutil.copy(tts_track, mixed_audio)

        upd(job_id, step="Đang burn phụ đề...", progress=88)
        video_subbed = str(workdir / "video_subbed.mp4")
        burn_subtitles(FFMPEG, video_path, output_srt, video_subbed, extra_args=["-an"])

        upd(job_id, step="Đang ghép audio...", progress=94)
        subprocess.run([
            FFMPEG, "-y",
            "-i", video_subbed,
            "-i", mixed_audio,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_video,
        ], check=True)

        shutil.rmtree(workdir, ignore_errors=True)
        upd(job_id, status="done", step="Hoàn thành!", progress=100, output_file=f"output_{job_id[:8]}.mp4")

    except Exception as e:
        upd(job_id, status="error", step=f"Lỗi: {e}", progress=0)
        shutil.rmtree(workdir, ignore_errors=True)


# ========= API =========
@app.post("/api/convert")
async def convert(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    bgm: Optional[UploadFile] = File(None),
    bgm_volume: float = Form(0.15),
    tts_volume: float = Form(1.0),
    bgm_loop: bool = Form(True),
    source_lang: str = Form("zh"),
    model_size: str = Form("base"),
    voice_key: str = Form("nu-hay"),
    tts_rate: str = Form("+0%"),
    output_mode: str = Form("full"),
):
    job_id = uuid.uuid4().hex
    workdir = UPLOAD_DIR / job_id
    workdir.mkdir(exist_ok=True)

    video_path = str(workdir / video.filename)
    with open(video_path, "wb") as f:
        shutil.copyfileobj(video.file, f)

    bgm_path = None
    if bgm and bgm.filename:
        bgm_path = str(workdir / bgm.filename)
        with open(bgm_path, "wb") as f:
            shutil.copyfileobj(bgm.file, f)

    jobs[job_id] = {"status": "queued", "step": "Đang chờ xử lý...", "progress": 0}
    background_tasks.add_task(
        process_video,
        job_id,
        video_path,
        bgm_path,
        bgm_volume,
        tts_volume,
        bgm_loop,
        source_lang,
        model_size,
        voice_key,
        tts_rate,
        output_mode,
    )
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return job


@app.get("/api/download/{filename}")
def download(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(str(path), media_type="video/mp4", filename=filename)


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
