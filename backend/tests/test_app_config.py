import os
import unittest
from unittest.mock import patch

from app.config import AppSettings


class AppSettingsTests(unittest.TestCase):
    def test_disables_paddle_ocr_by_default_on_linux_arm64(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("platform.system", return_value="Linux"), patch(
            "platform.machine", return_value="aarch64"
        ):
            self.assertFalse(AppSettings._paddle_ocr_enabled())

    def test_environment_can_explicitly_disable_paddle_ocr(self) -> None:
        with patch.dict(os.environ, {"VIDTRANS_ENABLE_PADDLE_OCR": "0"}, clear=True):
            self.assertFalse(AppSettings._paddle_ocr_enabled())

    def test_positive_worker_configuration(self) -> None:
        with patch.dict(os.environ, {"VIDTRANS_WORKER_CONCURRENCY": "3"}, clear=True):
            self.assertEqual(AppSettings._positive_int("VIDTRANS_WORKER_CONCURRENCY", 2), 3)
        with patch.dict(os.environ, {"VIDTRANS_WORKER_CONCURRENCY": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "at least 1"):
                AppSettings._positive_int("VIDTRANS_WORKER_CONCURRENCY", 2)
