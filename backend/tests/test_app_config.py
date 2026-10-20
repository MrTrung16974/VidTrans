import os
import unittest
from unittest.mock import patch

from app.config import AppSettings


class AppSettingsTests(unittest.TestCase):
    def test_prefers_paddle_on_linux_arm64_when_installed(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("platform.system", return_value="Linux"), patch(
            "platform.machine", return_value="aarch64"
        ), patch("importlib.util.find_spec", return_value=object()):
            self.assertEqual(AppSettings._ocr_backend(), "paddle")

    def test_falls_back_to_tesseract_on_linux_arm64_without_paddle(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("platform.system", return_value="Linux"), patch(
            "platform.machine", return_value="aarch64"
        ), patch("importlib.util.find_spec", return_value=None):
            self.assertEqual(AppSettings._ocr_backend(), "tesseract")

    def test_explicit_ocr_backend_is_validated(self) -> None:
        with patch.dict(os.environ, {"VIDTRANS_OCR_BACKEND": "off"}, clear=True):
            self.assertEqual(AppSettings._ocr_backend(), "off")
        with patch.dict(os.environ, {"VIDTRANS_OCR_BACKEND": "invalid"}, clear=True):
            with self.assertRaisesRegex(ValueError, "VIDTRANS_OCR_BACKEND"):
                AppSettings._ocr_backend()

    def test_positive_worker_configuration(self) -> None:
        with patch.dict(os.environ, {"VIDTRANS_WORKER_CONCURRENCY": "3"}, clear=True):
            self.assertEqual(AppSettings._positive_int("VIDTRANS_WORKER_CONCURRENCY", 2), 3)
        with patch.dict(os.environ, {"VIDTRANS_WORKER_CONCURRENCY": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "at least 1"):
                AppSettings._positive_int("VIDTRANS_WORKER_CONCURRENCY", 2)
