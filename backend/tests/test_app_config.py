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
