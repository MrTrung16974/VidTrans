import unittest

from domain.languages import (
    LANGUAGES,
    VOICE_PRESETS,
    language_profile,
    languages_from_config,
    normalize_language,
    validate_language_pair,
    voice_preset,
)
from domain.models import ProcessingRequest


class LanguageCatalogTests(unittest.TestCase):
    def test_saved_job_languages_preserve_legacy_defaults_and_normalize_aliases(self):
        self.assertEqual(languages_from_config({}), ("zh-CN", "vi"))
        self.assertEqual(
            languages_from_config({"source_language": "jp", "target_language": "en"}),
            ("ja", "en"),
        )

    def test_catalog_has_expected_first_release_languages(self):
        self.assertEqual(set(LANGUAGES), {"vi", "en", "zh-CN", "ja", "ko", "th", "id", "es"})
        self.assertEqual(language_profile("zh").whisper_code, "zh")
        self.assertEqual(normalize_language("zh-cn"), "zh-CN")
        for profile in LANGUAGES.values():
            self.assertIn("Neural", profile.edge_female)
            self.assertIn("Neural", profile.edge_male)
            self.assertTrue(profile.gtts_code)
            self.assertTrue(profile.font_family)

    def test_pair_rejects_same_explicit_language(self):
        with self.assertRaisesRegex(ValueError, "must be different"):
            validate_language_pair("ja", "ja")
        self.assertEqual(validate_language_pair("auto", "ja"), ("auto", "ja"))

    def test_legacy_request_defaults_to_chinese_vietnamese(self):
        request = ProcessingRequest.from_form(
            mode=1, subtitle_source="speech", ocr_sample_fps=5,
            ocr_roi_top=.68, ocr_roi_bottom=.96,
            voice_mode="auto", voice_type="female",
        )
        self.assertEqual((request.source_language, request.target_language), ("zh-CN", "vi"))

    def test_burned_ocr_rejects_non_chinese_or_auto_source(self):
        for source in ("auto", "en"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "OCR"):
                ProcessingRequest.from_form(
                    mode=1, subtitle_source="burned", ocr_sample_fps=5,
                    ocr_roi_top=.68, ocr_roi_bottom=.96,
                    voice_mode="auto", voice_type="female",
                    source_language=source, target_language="vi",
                )

    def test_voice_preset_catalog_is_bounded_and_validated(self):
        self.assertEqual(
            set(VOICE_PRESETS),
            {
                "auto", "natural", "warm", "bright", "deep", "energetic", "gentle",
                "storyteller", "news", "commercial", "youthful", "authoritative",
                "calm", "cheerful",
            },
        )
        self.assertEqual(voice_preset("warm").pitch_hz, -4)
        self.assertEqual(voice_preset("storyteller").rate_percent, -5)
        self.assertEqual(voice_preset("commercial").pitch_hz, 3)
        self.assertEqual(voice_preset("").key, "natural")
        with self.assertRaisesRegex(ValueError, "voice_style"):
            voice_preset("custom-injected-voice")
