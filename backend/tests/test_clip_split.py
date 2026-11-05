"""Cắt clip: plans snapped to subtitle gaps, and clip jobs cut from a finished video."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
from application.clip_routes import create_clip_router
from application.job_service import JobService
from infrastructure.job_store import SQLiteJobStore
from infrastructure.llm_providers import LLMRateLimitedError
from pipeline.clip_suggest import clean_suggestions, suggest_clips, transcript_for_prompt
from pipeline.clip_planning import (
    FRAME_VERTICAL_BLUR,
    FRAME_VERTICAL_CROP,
    CropBox,
    vertical_crop_box,
    vertical_segments,
    ClipPlanError,
    ClipSpec,
    build_clip_command,
    cut_points,
    plan_equal_clips,
    render_srt,
    slice_segments,
    validate_plan,
)

# One cue per 10 s (speech from 1 s to 9 s), so the gaps between cues are centred on 10, 20, 30...
CUES = [{"start": float(start) + 1, "end": float(start) + 9, "text": f"Câu {start // 10 + 1}"}
        for start in range(0, 120, 10)]


class ClipPlanningTests(unittest.TestCase):
    def test_cut_points_sit_in_the_gaps_between_cues(self) -> None:
        points = cut_points(CUES, 120.0)
        self.assertEqual(points[:3], [0.0, 10.0, 20.0])
        self.assertIn(120.0, points)
        self.assertIn(119.3, points)  # just after the last line

    def test_overlapping_cues_are_never_split(self) -> None:
        cues = [{"start": 0, "end": 5}, {"start": 4, "end": 9}, {"start": 10, "end": 12}]
        self.assertEqual(cut_points(cues, 12.0), [0.0, 9.5, 12.0])

    def test_equal_split_ends_every_clip_between_two_cues(self) -> None:
        clips = plan_equal_clips(CUES, 120.0, 25)
        bounds = [clip.start for clip in clips] + [clips[-1].end]
        self.assertEqual(bounds[0], 0.0)
        self.assertEqual(bounds[-1], 120.0)
        for bound in bounds[1:-1]:
            self.assertAlmostEqual(bound % 10, 0.0)  # a gap, never inside a cue
        self.assertEqual([clip.title for clip in clips][:2], ["Phần 1", "Phần 2"])

    def test_a_short_tail_joins_the_previous_clip(self) -> None:
        clips = plan_equal_clips([], 62.0, 30)
        self.assertEqual([(clip.start, clip.end) for clip in clips], [(0.0, 30.0), (30.0, 62.0)])

    def test_equal_split_limits(self) -> None:
        with self.assertRaises(ClipPlanError):
            plan_equal_clips([], 120.0, 2)
        with self.assertRaises(ClipPlanError):
            plan_equal_clips([], 3.0, 10)

    def test_manual_plan_is_snapped_and_checked(self) -> None:
        clips = validate_plan([{"start": 8.7, "end": 31.2, "title": "  Mở   đầu ", "frame": "vertical_blur"}],
                              120.0, segments=CUES)
        self.assertEqual(clips, [ClipSpec(10.0, 30.0, "Mở đầu", "vertical_blur")])
        exact = validate_plan([{"start": 8.7, "end": 31.2}], 120.0, segments=CUES, snap=False)
        self.assertEqual((exact[0].start, exact[0].end, exact[0].title), (8.7, 31.2, "Phần 1"))

    def test_invalid_plans_explain_the_problem(self) -> None:
        cases = [
            ([], "chưa có clip"),
            ([{"start": 0, "end": 3}], "ngắn hơn"),
            ([{"start": 0, "end": 700}], "dài hơn"),
            ([{"start": 0, "end": 30}, {"start": 20, "end": 50}], "chồng lên nhau"),
            ([{"start": 0, "end": 30, "frame": "square"}], "khung hình"),
        ]
        for raw, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ClipPlanError, message):
                validate_plan(raw, 800.0, snap=False)

    def test_cues_are_shifted_to_the_clip_timeline(self) -> None:
        sliced = slice_segments(CUES, 10.0, 30.0)
        self.assertEqual([(cue["start"], cue["end"], cue["text"]) for cue in sliced],
                         [(1.0, 9.0, "Câu 2"), (11.0, 19.0, "Câu 3")])
        self.assertEqual(render_srt(sliced).splitlines()[:3], ["1", "00:00:01,000 --> 00:00:09,000", "Câu 2"])

    def test_vertical_frame_blurs_the_background_instead_of_cropping(self) -> None:
        command = build_clip_command("ffmpeg", Path("in.mp4"), ClipSpec(10, 30, frame=FRAME_VERTICAL_BLUR),
                                     Path("out.mp4"))
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("boxblur", graph)
        self.assertIn("1080:1920:force_original_aspect_ratio=decrease", graph)
        self.assertEqual(command[command.index("-ss") + 1], "10.000")
        self.assertEqual(command[command.index("-t") + 1], "20.000")


class VerticalCropTests(unittest.TestCase):
    def test_the_crop_is_the_centred_9_16_window(self) -> None:
        self.assertEqual(vertical_crop_box(1280, 720), CropBox(404, 720, 438, 0))
        self.assertEqual(vertical_crop_box(1920, 1080), CropBox(608, 1080, 656, 0))
        self.assertEqual(vertical_crop_box(1080, 1920), CropBox(1080, 1920, 0, 0))
        self.assertEqual(vertical_crop_box(720, 1600), CropBox(720, 1280, 0, 160))  # taller than 9:16

    def test_subtitles_are_laid_out_again_for_the_narrow_frame(self) -> None:
        box = vertical_crop_box(1280, 720)  # scale 2.667
        segment = {"start": 0, "end": 2, "text": "Ngươi, kẻ phản bội! Cẩn trọng ư? Không, không giống vậy!",
                   "subtitle_layout": {"x": 640, "y": 650, "font_size": 34, "mask_original": True,
                                       "mask": {"x": 300, "y": 630, "width": 680, "height": 40}}}
        layout = vertical_segments([segment], box, base_font_size=36)[0]["subtitle_layout"]
        self.assertEqual(layout["x"], 540)
        self.assertAlmostEqual(layout["y"], 650 * 1920 / 720, delta=1)
        self.assertEqual(layout["font_size"], 63)  # 34 * 2.667 * 0.7
        self.assertTrue(all(len(line) <= 30 for line in layout["render_text"].split("\n")))
        self.assertGreater(layout["render_text"].count("\n"), 0)
        # The source subtitle patch keeps covering it, clipped to the crop.
        self.assertEqual(layout["mask"], {"x": 0.0, "y": 1680.0, "width": 1080.0, "height": 106.7})

    def test_a_mask_outside_the_crop_is_dropped_and_unplaced_cues_go_above_the_app_buttons(self) -> None:
        box = vertical_crop_box(1280, 720)
        segment = {"start": 0, "end": 2, "text": "Xin chào",
                   "subtitle_layout": {"y": 100, "mask_original": True,
                                       "mask": {"x": 0, "y": 80, "width": 200, "height": 40}}}
        self.assertNotIn("mask", vertical_segments([segment], box, base_font_size=36)[0]["subtitle_layout"])
        plain = vertical_segments([{"start": 0, "end": 2, "text": "Xin chào"}], box, base_font_size=36)[0]
        self.assertAlmostEqual(plain["subtitle_layout"]["y"], 1920 * 0.78, delta=1)

    def test_crop_without_subtitles_crops_the_finished_video(self) -> None:
        command = build_clip_command("ffmpeg", Path("in.mp4"), ClipSpec(0, 10, frame=FRAME_VERTICAL_CROP),
                                     Path("out.mp4"), source_size=(1920, 1080))
        self.assertIn("crop=608:1080:656:0,scale=1080:1920,setsar=1", command)


class FakeModel:
    name = "fake-gemini"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        self.prompts.append((system, user))
        return self.answer


class ClipSuggestTests(unittest.TestCase):
    def test_the_model_reads_timed_lines_and_the_users_wish(self) -> None:
        model = FakeModel('{"clips": [{"start": 9, "end": 31, "title": "Màn đối đầu", "reason": "kịch tính"}]}')
        clips = suggest_clips(model, CUES, 120.0, count=3, min_seconds=15, max_seconds=40, wish="  đoạn   đánh nhau ")
        self.assertEqual(clips, [ClipSpec(10.0, 30.0, "Màn đối đầu")])  # snapped to the cue gaps
        prompt = model.prompts[0][1]
        self.assertIn("[1.0-9.0] Câu 1", prompt)
        self.assertIn("Người dùng muốn: đoạn đánh nhau.", prompt)
        self.assertIn("tối đa 3 clip", prompt)

    def test_unusable_answers_are_dropped_not_fatal(self) -> None:
        raw = [
            {"start": 0, "end": 30, "title": "A"},
            {"start": 20, "end": 50, "title": "trùng A"},      # overlaps A
            {"start": 50, "end": 52, "title": "quá ngắn"},
            {"start": "x", "end": 80},                          # unreadable
            {"start": 60, "end": 500, "title": "vượt video"},   # past the end of the video: made up
            {"start": 80, "end": 110, "title": ""},
        ]
        clips = clean_suggestions(raw, CUES, 120.0, count=5, min_seconds=15, max_seconds=40)
        self.assertEqual([(clip.start, clip.end, clip.title) for clip in clips],
                         [(0.0, 30.0, "A"), (80.0, 110.0, "Gợi ý 2")])
        self.assertEqual(len(clean_suggestions(raw, CUES, 120.0, count=1, min_seconds=15, max_seconds=40)), 1)

    def test_fenced_json_and_empty_answers(self) -> None:
        fenced = FakeModel('Đây:\n```json\n{"clips":[{"start":0,"end":20,"title":"Mở đầu"}]}\n```')
        self.assertEqual(suggest_clips(fenced, CUES, 120.0)[0].title, "Mở đầu")
        with self.assertRaisesRegex(ClipPlanError, "chưa tìm được"):
            suggest_clips(FakeModel("không biết"), CUES, 120.0)
        with self.assertRaisesRegex(ClipPlanError, "không có phụ đề"):
            suggest_clips(FakeModel("{}"), [], 120.0)

    def test_long_transcripts_are_sampled_across_the_whole_video(self) -> None:
        cues = [{"start": i, "end": i + 0.9, "text": f"Câu số {i} " + "x" * 40} for i in range(2000)]
        text = transcript_for_prompt(cues, limit=10_000)
        self.assertLessEqual(len(text), 10_500)
        self.assertIn("[0.0-0.9]", text)
        self.assertGreater(float(text.splitlines()[-1][1:].split("-")[0]), 1900)


def account(user_id: str, role: str = "editor") -> dict[str, str]:
    return {"user_id": user_id, "email": f"{user_id}@test.local", "display_name": user_id,
            "role": role, "plan": "free", "approval_status": "approved"}


def probe(path: Path) -> dict:
    result = subprocess.run(
        [main.FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class ClipJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.output_dir = root / "outputs"
        source_dir = self.output_dir / "users" / "user-a" / "src1"
        source_dir.mkdir(parents=True)
        self.source_video = source_dir / "output_src1.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=40",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=40", "-shortest",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(self.source_video),
        ], check=True)
        laid_out = [{**cue, "subtitle_layout": {"x": 320, "y": 330, "font_size": 20, "mask_original": True,
                                                 "mask": {"x": 120, "y": 318, "width": 400, "height": 26}}}
                    for cue in CUES if cue["end"] <= 40]
        (source_dir / "src1.translation.json").write_text(
            json.dumps({"segments": laid_out}, ensure_ascii=False), encoding="utf-8")
        # The source before subtitles were burned in (the upload), kept for the 9:16 crop.
        self.clean_video = root / "uploads" / "source.mp4"
        self.clean_video.parent.mkdir()
        shutil.copyfile(self.source_video, self.clean_video)
        self.service = JobService(SQLiteJobStore(root / "jobs.sqlite3"))
        self.service.create("src1", {
            "status": "completed", "filename": "Phim hay.mp4", "mode": 2, "target_language": "vi",
            "output_video": "users/user-a/src1/output_src1.mp4",
            "translation_file": "users/user-a/src1/src1.translation.json",
            "resume_request": {"video_path": str(self.clean_video), "subtitle_style": {"font_size": 22}},
        }, owner_user_id="user-a")
        self.service.create("busy", {"status": "processing", "filename": "Đang chạy.mp4"}, owner_user_id="user-a")
        self.account = account("user-a")
        for active in (
            patch.object(main, "JOB_SERVICE", self.service),
            patch.object(main, "OUTPUT_DIR", self.output_dir),
            patch.object(main, "WORK_DIR", root / "work"),
            patch.object(main, "notify_scheduler"),
            patch.object(main, "current_account", lambda request: self.account),
        ):
            active.start()
            self.addCleanup(active.stop)
        app = FastAPI()
        app.include_router(create_clip_router(main.CLIP_ROUTE_DEPS))
        self.client = TestClient(app)

    def run_worker(self, job_id: str) -> dict:
        job = self.service.get(job_id)
        main.resume_process_video(job_id, {**job["resume_request"], "_job_action": job["job_action"]})
        return self.service.get(job_id)

    def test_sources_lists_only_finished_videos(self) -> None:
        listed = self.client.get("/api/v1/clips/sources").json()
        self.assertEqual([item["job_id"] for item in listed["items"]], ["src1"])
        detail = self.client.get("/api/v1/clips/sources/src1").json()
        self.assertAlmostEqual(detail["duration"], 40.0, delta=0.2)
        self.assertEqual(detail["cues"][0], {"start": 1.0, "end": 9.0, "text": "Câu 1"})
        self.assertEqual(self.client.get("/api/v1/clips/sources/busy").status_code, 409)

    def test_another_users_source_is_not_found(self) -> None:
        self.account = account("user-b")
        self.assertEqual(self.client.get("/api/v1/clips/sources/src1").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/clips/sources").json()["items"], [])

    def test_equal_plan(self) -> None:
        response = self.client.post("/api/v1/clips/plan", json={"source_job_id": "src1", "clip_seconds": 20,
                                                                   "frame": "vertical_blur"})
        self.assertEqual(response.status_code, 200, response.text)
        clips = response.json()["clips"]
        self.assertEqual([(clip["start"], clip["end"]) for clip in clips], [(0.0, 20.0), (20.0, clips[-1]["end"])])
        self.assertEqual({clip["frame"] for clip in clips}, {"vertical_blur"})

    def test_clips_are_cut_with_their_subtitles_and_caption(self) -> None:
        response = self.client.post("/api/v1/clips", json={"source_job_id": "src1", "clips": [
            {"start": 9.0, "end": 21.0, "title": "Đoạn hay"},
            {"start": 20.0, "end": 31.0, "frame": "vertical_blur"},
        ]})
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(len(created["jobs"]), 2)
        first, second = (job["job_id"] for job in created["jobs"])
        self.assertEqual(self.service.get(first)["batch_id"], created["batch_id"])
        self.assertEqual(self.service.get(first)["clip"]["start"], 10.0)  # snapped to the gap

        done = self.run_worker(first)
        self.assertEqual(done["status"], "completed", done.get("error"))
        output = self.output_dir / done["output_video"]
        info = probe(output)
        self.assertAlmostEqual(float(info["format"]["duration"]), 10.0, delta=0.15)
        self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
        srt = (self.output_dir / done["subtitle_file"]).read_text(encoding="utf-8")
        self.assertIn("00:00:01,000 --> 00:00:09,000\nCâu 2", srt)
        self.assertTrue((self.output_dir / done["tiktok_text_file"]).is_file())
        self.assertEqual(done["filename"], "Đoạn hay · Phim hay.mp4")

        vertical = self.run_worker(second)
        self.assertEqual(vertical["status"], "completed", vertical.get("error"))
        streams = probe(self.output_dir / vertical["output_video"])["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))

    def test_ai_suggestions_need_a_paid_plan(self) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
            detail = self.client.get("/api/v1/clips/sources/src1").json()
            self.assertFalse(detail["ai_suggest"]["available"])
            self.assertIn("nâng cấp", detail["ai_suggest"]["reason"])
            response = self.client.post("/api/v1/clips/suggest", json={"source_job_id": "src1"})
        self.assertEqual(response.status_code, 403)

    def test_ai_suggestions_for_a_paid_plan(self) -> None:
        self.account = {**account("user-a"), "plan": "creator"}
        model = FakeModel('{"clips": [{"start": 9, "end": 31, "title": "Đoạn hay"}]}')
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
                patch.object(main.CLIP_ROUTE_DEPS, "suggest_model", lambda: model):
            self.assertTrue(self.client.get("/api/v1/clips/sources/src1").json()["ai_suggest"]["available"])
            response = self.client.post("/api/v1/clips/suggest", json={
                "source_job_id": "src1", "count": 2, "min_seconds": 10, "max_seconds": 30,
                "wish": "đoạn hài", "frame": "vertical_blur"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["clips"], [{"start": 10.0, "end": 30.0, "title": "Đoạn hay",
                                                     "frame": "vertical_blur", "duration": 20.0}])
        self.assertEqual(response.json()["provider"], "fake-gemini")
        self.assertIn("đoạn hài", model.prompts[0][1])

    def test_ai_errors_are_reported_as_temporary(self) -> None:
        self.account = {**account("user-a"), "plan": "creator"}

        class Limited(FakeModel):
            def generate_json(self, system, user, schema):
                raise LLMRateLimitedError("hết hạn mức")

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
                patch.object(main.CLIP_ROUTE_DEPS, "suggest_model", lambda: Limited("")):
            response = self.client.post("/api/v1/clips/suggest", json={"source_job_id": "src1"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("tạm lỗi", response.json()["detail"])

    def test_vertical_crop_draws_the_subtitles_again_on_the_clean_source(self) -> None:
        job_id = self.client.post("/api/v1/clips", json={"source_job_id": "src1", "clips": [
            {"start": 10, "end": 20, "frame": "vertical_crop"}]}).json()["jobs"][0]["job_id"]
        with patch.object(main, "burn_subtitles", wraps=main.burn_subtitles) as burn:
            done = self.run_worker(job_id)
        self.assertEqual(done["status"], "completed", done.get("error"))
        info = probe(self.output_dir / done["output_video"])
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertIn("audio", {stream["codec_type"] for stream in info["streams"]})
        self.assertAlmostEqual(float(info["format"]["duration"]), 10.0, delta=0.15)
        burned_from, ass = burn.call_args.args[0], burn.call_args.args[1]
        self.assertEqual(burned_from.name, "cropped.mp4")
        laid_out = burn.call_args.args[4]
        self.assertEqual(laid_out[0]["subtitle_layout"]["x"], 540)
        self.assertEqual(laid_out[0]["subtitle_layout"]["mask"]["width"], 1080.0)  # clipped to the crop
        self.assertFalse(ass.exists())  # the scratch folder is removed

    def test_vertical_crop_is_refused_when_the_clean_source_is_gone(self) -> None:
        self.clean_video.unlink()
        detail = self.client.get("/api/v1/clips/sources/src1").json()
        self.assertFalse(detail["can_crop"])
        response = self.client.post("/api/v1/clips", json={"source_job_id": "src1", "clips": [
            {"start": 0, "end": 10, "frame": "vertical_crop"}]})
        self.assertEqual(response.status_code, 409)
        self.assertIn("nền mờ", response.json()["detail"])

    def test_bad_plans_are_refused_before_any_job_is_made(self) -> None:
        response = self.client.post("/api/v1/clips", json={"source_job_id": "src1", "clips": [
            {"start": 0, "end": 20}, {"start": 15, "end": 35}]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("chồng lên nhau", response.json()["detail"])
        self.assertEqual(self.service.list_jobs(job_action="clip_video")[1], 0)

    def test_a_clip_whose_source_was_deleted_fails_clearly_and_can_be_retried(self) -> None:
        job_id = self.client.post("/api/v1/clips", json={"source_job_id": "src1", "clips": [
            {"start": 0, "end": 10}]}).json()["jobs"][0]["job_id"]
        moved = self.source_video.with_suffix(".bak")
        self.source_video.rename(moved)
        failed = self.run_worker(job_id)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Video gốc", failed["error"])
        moved.rename(self.source_video)
        with patch.object(main, "job_for_request", lambda request, wanted: self.service.get(wanted)):
            main.requeue_video_job(request=None, job_id=job_id)
        self.assertEqual(self.service.get(job_id)["status"], "queued")
        self.assertEqual(self.run_worker(job_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
