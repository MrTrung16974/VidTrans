"""Published legal terms and support contacts.

TERMS_VERSION names the /terms + /privacy revision a user agreed to. Bump it (to the
new effective date) whenever either page changes materially: every account whose
stored version differs is asked to accept again on its next sign-in.
"""
from __future__ import annotations

import os

TERMS_VERSION = "2026-11-04"

SUPPORT_EMAIL_DEFAULT = "mr.trung16974@gmail.com"
SUPPORT_PHONE = "0965 510 400"


def support_email() -> str:
    """Inbox that receives contact-form notifications."""
    value = os.environ.get("VIDTRANS_SUPPORT_EMAIL", "").strip()
    return value if "@" in value else SUPPORT_EMAIL_DEFAULT
