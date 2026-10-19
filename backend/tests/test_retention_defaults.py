import os
import unittest
from unittest.mock import patch

import main


class RetentionDefaultTests(unittest.TestCase):
    def test_retention_is_enabled_for_thirty_days_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(main.retention_enabled())
            self.assertEqual(main.default_video_retention_days(), 30)

    def test_retention_can_be_disabled_explicitly(self) -> None:
        with patch.dict(os.environ, {"VIDTRANS_RETENTION_ENABLED": "0"}, clear=True):
            self.assertFalse(main.retention_enabled())
            self.assertIsNone(main.default_video_retention_days())


if __name__ == "__main__":
    unittest.main()
