import io
import json
import unittest
import unittest.mock
import zipfile

from pydantic import ValidationError

from domain.comic import ComicCharacter, ComicScene, ComicSettings, Storyboard, slugify_id
from infrastructure.story_text import StoryTextError, chunk_story, decode_text, read_story_upload, split_sentences
from infrastructure.visual_prompt_translator import ChainedVisualPromptTranslator, LLMVisualPromptTranslator
from pipeline.animation_storyboard import heuristic_animated_scenes
from pipeline.comic_storyboard import MAX_REPAIR_ROUNDS, build_storyboard, extract_json, heuristic_scenes

STORY = (
    "Đêm hôm đó, Minh bước vào khu rừng. Ánh trăng lạnh lẽo xuyên qua tán lá.\n\n"
    "\"Có ai ở đây không?\" Minh gọi lớn. Không ai trả lời.\n\n"
    "Bỗng một bóng trắng lướt qua. Cô gái tóc dài đứng giữa lối mòn, mỉm cười buồn bã."
)


def scene(narration="Minh bước vào khu rừng.", **extra):
    return {"narration": narration, "dialogue": [], "characters": ["minh"], "location": "dark forest",
            "emotion": "fear", "visual_prompt": "A young man walks into a dark forest at night",
            "camera_motion": "slow_zoom_in", "estimated_duration": 6, "source_excerpt": "Đêm hôm đó, Minh bước vào",
            **extra}


MINH = {"id": "minh", "name": "Minh", "gender": "male", "appearance": "young man, short black hair"}


class FakeLLM:
    name = "fake-llm"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_json(self, system, user, schema):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


class StoryTextTests(unittest.TestCase):
    def test_docx_paragraphs_are_extracted(self) -> None:
        document = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            "<w:p><w:r><w:t>Chương 1</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Minh bước </w:t></w:r><w:r><w:t>vào rừng.</w:t></w:r></w:p>"
            "</w:body></w:document>"
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document)
        self.assertEqual(read_story_upload("story.docx", buffer.getvalue()), "Chương 1 Minh bước vào rừng.")

    def test_vietnamese_encodings_and_unsupported_files(self) -> None:
        self.assertEqual(decode_text("Đêm khuya".encode("utf-16")), "Đêm khuya")
        self.assertEqual(decode_text("﻿Đêm".encode("utf-8")), "Đêm")
        with self.assertRaises(StoryTextError):
            read_story_upload("story.pdf", b"%PDF")
        with self.assertRaises(StoryTextError):
            read_story_upload("story.docx", b"not a zip")

    def test_chunks_never_cut_a_sentence(self) -> None:
        text = "\n\n".join(f"Câu số {i} kể về một chuyện dài dòng." for i in range(200))
        chunks = chunk_story(text, 500)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertEqual(sum(len(split_sentences(c)) for c in chunks), 200)


class DomainTests(unittest.TestCase):
    def test_ids_are_ascii_slugs_and_motion_aliases_normalise(self) -> None:
        self.assertEqual(slugify_id("Đức Anh"), "duc_anh")
        self.assertEqual(ComicScene(scene_index=1, narration="x", visual_prompt="abc",
                                    camera_motion="Zoom In").camera_motion, "slow_zoom_in")

    def test_scene_needs_speech_and_known_characters(self) -> None:
        with self.assertRaises(ValidationError):
            ComicScene(scene_index=1, visual_prompt="abc")
        with self.assertRaisesRegex(ValidationError, "unknown characters"):
            Storyboard(characters=[], scenes=[ComicScene(scene_index=1, narration="x", visual_prompt="abc",
                                                         characters=["lan"])])

    def test_storyboard_renumbers_scenes(self) -> None:
        board = Storyboard(scenes=[ComicScene(scene_index=7, narration="a", visual_prompt="abc"),
                                   ComicScene(scene_index=3, narration="b", visual_prompt="abc")])
        self.assertEqual([s.scene_index for s in board.scenes], [1, 2])


class StoryboardTests(unittest.TestCase):
    def settings(self, **overrides):
        return ComicSettings(title="T", llm_provider=overrides.pop("llm_provider", "gemini"), **overrides)

    def test_heuristic_keeps_every_sentence_in_order(self) -> None:
        board = build_storyboard(STORY, self.settings(llm_provider="heuristic", target_scene_duration=4), None)
        joined = " ".join(s.narration for s in board.scenes)
        self.assertEqual(joined.split(), " ".join(split_sentences(STORY)).split())
        self.assertGreater(len(board.scenes), 1)
        self.assertEqual(board.analysis_method, "heuristic")
        self.assertEqual(board.youtube_title, "T")

    def test_llm_generates_vietnamese_youtube_title_unless_user_supplies_one(self) -> None:
        answer = {"youtube_title": "Bí Ẩn Trong Khu Rừng Đêm", "characters": [MINH], "scenes": [scene()]}
        generated = build_storyboard(STORY, self.settings(), FakeLLM([answer]))
        supplied = build_storyboard(
            STORY, self.settings(youtube_title="Minh Gặp Nữ Tu Bí Ẩn"), FakeLLM([answer]),
        )

        self.assertEqual(generated.youtube_title, "Bí Ẩn Trong Khu Rừng Đêm")
        self.assertEqual(supplied.youtube_title, "Minh Gặp Nữ Tu Bí Ẩn")

    def test_llm_answer_is_validated_and_repaired(self) -> None:
        invalid = {"characters": [MINH], "scenes": [{"narration": "", "visual_prompt": "x"}]}
        valid = {"characters": [MINH], "scenes": [scene(dialogue=[{"character": "Lan", "text": "Anh là ai?"}])]}
        llm = FakeLLM(["```json\n" + json.dumps(invalid) + "\n```", valid])

        board = build_storyboard(STORY, self.settings(), llm)

        self.assertEqual(len(llm.prompts), 2)
        self.assertIn("rejected by the validator", llm.prompts[1])
        self.assertEqual({c.id for c in board.characters}, {"minh", "lan"})  # Unknown speaker is registered.
        self.assertEqual(board.scenes[0].dialogue[0].character, "lan")
        self.assertTrue(board.scenes[0].negative_prompt)
        self.assertEqual(board.warnings, [])

    def test_repairs_are_bounded(self) -> None:
        llm = FakeLLM(["not json"] * (MAX_REPAIR_ROUNDS + 1))
        with self.assertRaises(json.JSONDecodeError):
            build_storyboard(STORY, self.settings(), llm)
        self.assertEqual(len(llm.prompts), MAX_REPAIR_ROUNDS + 1)

    def test_auto_mode_falls_back_to_sentences_with_a_warning(self) -> None:
        board = build_storyboard(STORY, self.settings(llm_provider="auto"), FakeLLM([RuntimeError("quota")]))
        self.assertEqual(board.analysis_method, "heuristic")
        self.assertIn("quota", board.warnings[0])

    def test_known_characters_keep_their_first_description(self) -> None:
        text = "\n\n".join([STORY] * 3)
        redrawn = {**MINH, "appearance": "old man with a beard"}
        llm = FakeLLM([{"characters": [MINH], "scenes": [scene()]},
                       {"characters": [redrawn], "scenes": [scene()]}] * 3)
        with unittest.mock.patch("pipeline.comic_storyboard.CHUNK_CHARS", len(STORY) + 5):
            board = build_storyboard(text, self.settings(), llm)
        self.assertEqual(board.character("minh").appearance, MINH["appearance"])
        self.assertIn("KNOWN CHARACTERS", llm.prompts[1])
        self.assertIn("short black hair", llm.prompts[1])

    def test_series_characters_seed_the_next_episode(self) -> None:
        bible = [ComicCharacter(**MINH, approved_reference="minh-upload-abc.png"),
                 ComicCharacter(id="lan", name="Lan", gender="female", appearance="long white hair")]
        redrawn = {**MINH, "appearance": "old man with a beard"}
        llm = FakeLLM([{"characters": [redrawn], "scenes": [scene()]}])
        board = build_storyboard(STORY, self.settings(), llm, known_characters=bible)
        self.assertIn("long white hair", llm.prompts[0])
        minh = board.character("minh")
        self.assertEqual((minh.appearance, minh.approved_reference), (MINH["appearance"], "minh-upload-abc.png"))
        # Characters absent from this episode are not copied into its storyboard.
        self.assertIsNone(board.character("lan"))

    def test_excerpt_not_in_source_is_flagged(self) -> None:
        llm = FakeLLM([{"characters": [MINH], "scenes": [scene(source_excerpt="Minh cưỡi rồng bay lên trời")]}])
        board = build_storyboard(STORY, self.settings(), llm)
        self.assertIn("không khớp", board.warnings[0])

    def test_scene_cap(self) -> None:
        board = build_storyboard(STORY, self.settings(llm_provider="heuristic", max_scenes=1,
                                                      target_scene_duration=3), None)
        self.assertEqual(len(board.scenes), 1)
        self.assertTrue(board.warnings)

    def test_default_scene_count_covers_complete_story(self) -> None:
        story = " ".join(f"Sự kiện thứ {index} xảy ra tại một địa điểm hoàn toàn khác biệt." for index in range(50))
        board = build_storyboard(story, self.settings(llm_provider="heuristic", target_scene_duration=3), None)
        self.assertGreater(len(board.scenes), 40)
        self.assertNotIn("chỉ giữ", " ".join(board.warnings))

    def test_heuristic_visual_prompt_is_translated_for_image_model(self) -> None:
        class Translator:
            def translate_lines(self, lines):
                self.lines = lines
                return ["In the afternoon, Doan Than drinks tea with a male friend in a tea room."]

        translator = Translator()
        board = build_storyboard(
            "Buổi chiều Doãn Thần uống trà cùng một người bạn trong phòng trà.",
            self.settings(llm_provider="heuristic"), None, prompt_translator=translator,
        )
        self.assertIn("drinks tea", board.scenes[0].visual_prompt)
        self.assertIn("visual-en-local", board.analysis_method)

    def test_animated_fallback_translates_every_shot_keyframe(self) -> None:
        class Translator:
            def translate_lines(self, lines):
                return [f"english {index}" for index, _ in enumerate(lines)]

        board = build_storyboard(
            "Doãn Thần mở cửa. Hắn nhìn thấy một hòn đá.\n\nĐầu đinh nam cười lớn.",
            self.settings(llm_provider="heuristic", video_mode="animated"), None, prompt_translator=Translator(),
        )
        prompts = [shot.keyframe_prompt for scene in board.scenes for shot in scene.shots]
        self.assertTrue(prompts)
        self.assertTrue(all("english" in prompt for prompt in prompts))
        self.assertFalse(any("Doãn" in prompt for prompt in prompts))

    def test_llm_visual_translator_batches_and_chain_falls_back(self) -> None:
        class FakeLLM:
            calls = 0

            def generate_json(self, system, user, schema):
                FakeLLM.calls += 1
                count = len(user.splitlines())
                return "<think>x</think>" + json.dumps({"lines": [f"scene {i}" for i in range(count)]})

        class Broken:
            def translate_lines(self, lines):
                raise ModuleNotFoundError("transformers")

        lines = [f"Câu {index}." for index in range(13)]
        translated = ChainedVisualPromptTranslator(Broken(), LLMVisualPromptTranslator(FakeLLM())).translate_lines(lines)
        self.assertEqual(len(translated), 13)
        self.assertEqual(FakeLLM.calls, 2)

    def test_sentence_split_scenes_keep_cast_and_location_from_earlier_chunks(self) -> None:
        from pipeline.comic_storyboard import attach_story_context

        hero = ComicCharacter(id="doan_than", name="Doãn Thần", gender="male")
        friend = ComicCharacter(id="dau_dinh", name="Đầu đinh nam", gender="male")
        earlier = ComicScene(scene_index=1, narration="x", visual_prompt="cafe", location="Café", characters=["doan_than"])
        scenes = heuristic_animated_scenes(
            "Đầu đinh nam cười lớn. Doãn Thần im lặng.\n\nHắn nói tiếp về vũ trụ.",
            self.settings(llm_provider="heuristic", video_mode="animated"), 2, 15.0,
        )
        attach_story_context(scenes, [hero, friend], [earlier])
        self.assertEqual(scenes[0].shots[0].characters, ["dau_dinh", "doan_than"])
        # No name in the sentence: the conversation's cast carries over.
        self.assertEqual(scenes[1].shots[0].characters, ["dau_dinh", "doan_than"])
        self.assertTrue(all(scene.location == "Café" for scene in scenes))

    def test_draft_that_summarises_a_long_chunk_is_rejected(self) -> None:
        from pipeline.comic_storyboard import _validate_draft

        chunk = " ".join(f"Câu thứ {index} kể một sự việc khác nhau trong truyện." for index in range(30))
        summary = {"characters": [], "scenes": [{"narration": "Tóm tắt ngắn.", "visual_prompt": "a quiet room"}]}
        with self.assertRaisesRegex(ValueError, "cover only"):
            _validate_draft(json.dumps(summary, ensure_ascii=False), False, chunk)
        full = {"characters": [], "scenes": [{"narration": chunk, "visual_prompt": "a quiet room"}]}
        self.assertEqual(len(_validate_draft(json.dumps(full, ensure_ascii=False), False, chunk).scenes), 1)

    def test_extract_json_ignores_thinking_and_fences(self) -> None:
        self.assertEqual(extract_json('<think>hmm {"a": 0}</think> ```json\n{"a": 1}\n```'), {"a": 1})

    def test_heuristic_scene_durations_follow_text_length(self) -> None:
        scenes = heuristic_scenes("Một. " * 40, ComicSettings(target_scene_duration=5))
        self.assertTrue(all(1.0 <= s.estimated_duration <= 40 for s in scenes))


if __name__ == "__main__":
    unittest.main()


FOREST = {"id": "forest", "name": "Rừng trúc", "description": "dense bamboo forest, mossy stone path, pale mist"}
BRIEF = "Thời nhà Đường. Minh: thư sinh 18 tuổi, áo trắng. Rừng trúc: lối đá rêu phong, sương mờ."


class StoryBibleTests(unittest.TestCase):
    def bible(self, **extra):
        return json.dumps({"world": "Tang dynasty China, ink-wash palette",
                           "characters": [{**MINH, "appearance": "18-year-old scholar, pale face",
                                           "outfit": "white hanfu"}],
                           "locations": [FOREST], **extra})

    def test_brief_is_planned_first_and_every_chunk_sees_world_and_places(self):
        llm = FakeLLM([self.bible(), json.dumps({"characters": [MINH],
                                                 "scenes": [scene(location_id="forest")]})])
        storyboard = build_storyboard(STORY, ComicSettings(story_brief=BRIEF), llm)
        self.assertIn("PRODUCTION BRIEF", llm.prompts[0])
        self.assertIn("WORLD: Tang dynasty China", llm.prompts[1])
        self.assertIn("KNOWN LOCATIONS", llm.prompts[1])
        self.assertEqual(storyboard.world, "Tang dynasty China, ink-wash palette")
        self.assertEqual([place.id for place in storyboard.locations], ["forest"])
        self.assertEqual(storyboard.scenes[0].location_id, "forest")
        # The author's description beats what the chunk analysis said about Minh.
        self.assertEqual(storyboard.character("minh").outfit, "white hanfu")
        self.assertIn("bible", storyboard.analysis_method)

    def test_unknown_location_ids_are_dropped_and_new_places_kept(self):
        cave = {"id": "cave", "name": "Hang", "description": "wet limestone cave lit by torches"}
        llm = FakeLLM([json.dumps({"characters": [MINH], "locations": [cave],
                                   "scenes": [scene(location_id="cave"), scene("Không ai trả lời.", location_id="moon")]})])
        storyboard = build_storyboard(STORY, ComicSettings(), llm)
        self.assertEqual([s.location_id for s in storyboard.scenes], ["cave", ""])
        self.assertEqual([place.id for place in storyboard.locations], ["cave"])

    def test_failed_planning_keeps_the_raw_brief_in_scene_prompts(self):
        llm = FakeLLM(["not json"] * (MAX_REPAIR_ROUNDS + 1) + [json.dumps({"characters": [MINH], "scenes": [scene()]})])
        storyboard = build_storyboard(STORY, ComicSettings(story_brief=BRIEF), llm)
        self.assertIn(BRIEF, llm.prompts[-1])
        self.assertTrue(any("kế hoạch" in warning for warning in storyboard.warnings))

    def test_without_llm_the_translated_brief_becomes_the_world(self):
        class Translator:
            def translate_lines(self, lines):
                return ["Tang dynasty, bamboo forest"]

        storyboard = build_storyboard(STORY, ComicSettings(story_brief=BRIEF, llm_provider="heuristic"), None,
                                      prompt_translator=Translator())
        self.assertEqual(storyboard.world, "Tang dynasty, bamboo forest")

    def test_episode_brief_is_added_to_the_series_brief(self):
        from pipeline.comic_storyboard import story_brief

        text = story_brief(ComicSettings(story_brief="Thời Đường.", episode_brief="Tập này ở kinh thành."))
        self.assertTrue(text.startswith("Thời Đường."))
        self.assertIn("Tập này ở kinh thành.", text)

    def test_image_prompt_uses_the_planned_place_and_world(self):
        from pipeline.comic_video import scene_image_prompt

        storyboard = Storyboard.model_validate({
            "world": "Tang dynasty China", "locations": [FOREST], "characters": [MINH],
            "scenes": [{**scene(location_id="forest"), "scene_index": 1}],
        })
        prompt = scene_image_prompt(storyboard.scenes[0], storyboard, "9:16")
        self.assertIn("Setting: dense bamboo forest, mossy stone path, pale mist.", prompt)
        self.assertIn("World: Tang dynasty China.", prompt)
        self.assertNotIn("Setting: dark forest", prompt)


SUGGESTED = {
    "world": "Tang dynasty China", "world_vi": "Trung Quốc thời Đường",
    "characters": [{"id": "minh", "name": "Minh", "gender": "male", "species": "human", "age": "18",
                    "appearance": "slim young scholar", "appearance_vi": "thư sinh gầy",
                    "outfit": "white robe", "outfit_vi": "áo trắng"}],
    "locations": [{"id": "forest", "name": "Rừng trúc", "description": "bamboo forest",
                   "description_vi": "rừng trúc sương mờ"}],
}


class SuggestBibleTests(unittest.TestCase):
    def test_bible_is_read_from_the_story_in_both_languages(self):
        from pipeline.comic_storyboard import suggest_bible

        llm = FakeLLM([json.dumps(SUGGESTED)])
        bible = suggest_bible(llm, STORY, ComicSettings(genre="co_trang", story_brief="Minh luôn mặc áo trắng"))
        self.assertEqual(bible.characters[0].appearance_vi, "thư sinh gầy")
        self.assertEqual(bible.locations[0].description, "bamboo forest")
        self.assertEqual(bible.world_vi, "Trung Quốc thời Đường")
        self.assertIn("GENRE: Cổ trang", llm.prompts[0])
        self.assertIn("Minh luôn mặc áo trắng", llm.prompts[0])
        self.assertIn("STORY:", llm.prompts[0])

    def test_known_entries_keep_their_description_and_genre_fills_an_empty_world(self):
        from pipeline.comic_storyboard import suggest_bible

        known = ComicCharacter(id="minh", name="Minh", appearance="old look", appearance_vi="cũ")
        llm = FakeLLM([json.dumps({**SUGGESTED, "world": "", "world_vi": ""})])
        bible = suggest_bible(llm, STORY, ComicSettings(genre="tien_hiep"), known_characters=[known])
        self.assertEqual(bible.characters[0].appearance, "old look")
        self.assertIn("cultivation", bible.world)

    def test_vietnamese_edits_are_translated_and_a_failed_translation_keeps_the_text(self):
        from pipeline.comic_storyboard import fill_english, needs_english

        character = ComicCharacter(id="minh", name="Minh", appearance="", appearance_vi="tóc bạc")
        self.assertTrue(needs_english([character]))

        class Translator:
            def translate_lines(self, lines):
                return [f"EN:{line}" for line in lines]

        self.assertEqual(fill_english([character], Translator()), [])
        self.assertEqual(character.appearance, "EN:tóc bạc")
        self.assertFalse(needs_english([character]))

        other = ComicCharacter(id="lan", name="Lan", appearance_vi="váy hồng")
        warnings = fill_english([other], None)
        self.assertEqual(other.appearance, "váy hồng")
        self.assertTrue(warnings)

    def test_reviewed_bible_drives_the_storyboard_without_a_second_planning_call(self):
        from domain.comic import StoryBible

        bible = StoryBible.model_validate(SUGGESTED)
        bible.characters[0].appearance = ""  # The user edited it in Vietnamese.

        class Translator:
            def translate_lines(self, lines):
                return ["skinny scholar" for _ in lines]

        llm = FakeLLM([json.dumps({"characters": [MINH], "scenes": [scene(location_id="forest")]})])
        storyboard = build_storyboard(STORY, ComicSettings(bible=bible, story_brief="ghi chú"), llm,
                                      prompt_translator=Translator())
        self.assertEqual(len(llm.prompts), 1)  # Only the scene split; no planning pass.
        self.assertEqual(storyboard.character("minh").appearance, "skinny scholar")
        self.assertEqual(storyboard.character("minh").outfit_vi, "áo trắng")
        self.assertEqual(storyboard.world_vi, "Trung Quốc thời Đường")
        self.assertEqual(storyboard.locations[0].id, "forest")


class QuickFixPromptTests(unittest.TestCase):
    def test_fix_note_leads_the_image_prompt_and_is_translated_first(self):
        from pipeline.comic_storyboard import fill_english
        from pipeline.comic_video import scene_image_prompt

        board = Storyboard.model_validate({"characters": [MINH], "scenes": [{
            **scene(fix_note_vi="Minh cầm kiếm"), "scene_index": 1}]})

        class Translator:
            def translate_lines(self, lines):
                return ["Minh holds a sword" for _ in lines]

        fill_english(board.scenes, Translator())
        self.assertEqual(board.scenes[0].fix_note, "Minh holds a sword")
        prompt = scene_image_prompt(board.scenes[0], board, "9:16")
        self.assertLess(prompt.index("Important:"), prompt.index("Setting:"))

    def test_a_new_take_changes_the_seed_and_the_first_take_keeps_the_old_one(self):
        from pipeline.comic_video import scene_seed

        self.assertEqual(scene_seed(0, 3), 0)
        self.assertEqual(scene_seed(100, 3), 103)
        self.assertNotEqual(scene_seed(0, 3, 2), scene_seed(0, 3, 3))
        self.assertNotEqual(scene_seed(100, 3, 2), 103)
