"""Published legal terms and support contacts.

TERMS_VERSION names the current /terms + /privacy revision: it is what an account
records when it signs up or accepts. Bump it (to the new effective date) whenever
either page changes.

TERMS_MIN_ACCEPTED_VERSION is the oldest revision that still counts. Accounts that
accepted an older one are asked again on their next sign-in. Raise it to the new
TERMS_VERSION only when existing users must accept the change too; leave it when
only new accounts need to (terms 1.1, 2026-11-04: novel adaptation clause).
"""
from __future__ import annotations

import os

TERMS_VERSION = "2026-11-04"
TERMS_MIN_ACCEPTED_VERSION = "2026-11-02"


def terms_outdated(accepted_version: object) -> bool:
    """True when an account never accepted, or accepted a revision older than the minimum."""
    # Versions are ISO dates, so string order is date order.
    return not accepted_version or str(accepted_version) < TERMS_MIN_ACCEPTED_VERSION

SUPPORT_EMAIL_DEFAULT = "mr.trung16974@gmail.com"
SUPPORT_PHONE = "0965 510 400"


def support_email() -> str:
    """Inbox that receives contact-form notifications."""
    value = os.environ.get("VIDTRANS_SUPPORT_EMAIL", "").strip()
    return value if "@" in value else SUPPORT_EMAIL_DEFAULT
