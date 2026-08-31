import unittest

from domain.models import (
    ProcessingMode,
    ProcessingRequest,
    SubtitleSource,
    VoiceRoutingMode,
    VoiceType,
)


class ProcessingRequestTests(unittest.TestCase):
    def test_parses_supported_form_values(self) -> None:
        request = ProcessingRequest.from_form(
            mode=2,
            subtitle_source="burned",
            ocr_sample_fps=4.0,
            ocr_roi_top=0.65,
            ocr_roi_bottom=0.95,
            voice_mode="auto",
            voice_type="female",
        )

        self.assertIs(request.mode, ProcessingMode.DUBBED)
        self.assertIs(request.subtitle_source, SubtitleSource.BURNED)
        self.assertIs(request.voice_mode, VoiceRoutingMode.AUTO)
        self.assertIs(request.fallback_voice, VoiceType.FEMALE)
        self.assertEqual(request.ocr.sample_fps, 4.0)
        self.assertTrue(request.tiktok.enabled)
        self.assertEqual(request.tiktok.max_summary_chars, 350)

    def test_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode must be 1, 2, or 3"):
            ProcessingRequest.from_form(
                mode=99,
                subtitle_source="auto",
                ocr_sample_fps=5.0,
                ocr_roi_top=0.68,
                ocr_roi_bottom=0.96,
                voice_mode="auto",
                voice_type="female",
            )

    def test_rejects_unknown_subtitle_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "subtitle_source"):
            ProcessingRequest.from_form(
                mode=1,
                subtitle_source="unknown",
                ocr_sample_fps=5.0,
                ocr_roi_top=0.68,
                ocr_roi_bottom=0.96,
                voice_mode="auto",
                voice_type="female",
            )

    def test_rejects_invalid_tiktok_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "tiktok_max_summary_chars"):
            ProcessingRequest.from_form(
                mode=1,
                subtitle_source="speech",
                ocr_sample_fps=5.0,
                ocr_roi_top=0.68,
                ocr_roi_bottom=0.96,
                voice_mode="manual",
                voice_type="female",
                tiktok_max_summary_chars=20,
            )

    def test_auto_publish_requires_generated_tiktok_title(self) -> None:
        with self.assertRaisesRegex(ValueError, "generate_tiktok_post"):
            ProcessingRequest.from_form(
                mode=1,
                subtitle_source="speech",
                ocr_sample_fps=5.0,
                ocr_roi_top=0.68,
                ocr_roi_bottom=0.96,
                voice_mode="manual",
                voice_type="female",
                generate_tiktok_post=False,
                auto_publish_tiktok=True,
            )
