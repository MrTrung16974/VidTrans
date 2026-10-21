"""Outgoing transactional e-mail over SMTP (password-reset codes)."""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


class MailDeliveryError(RuntimeError):
    pass


SECURITY_MODES = {"starttls", "ssl", "none"}


@dataclass(frozen=True)
class MailConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = ""
    sender_name: str = "VidTrans"
    security: str = "starttls"
    timeout: float = 15.0

    @classmethod
    def from_env(cls) -> "MailConfig":
        security = os.environ.get("VIDTRANS_SMTP_SECURITY", "starttls").strip().lower() or "starttls"
        if security not in SECURITY_MODES:
            raise ValueError("VIDTRANS_SMTP_SECURITY must be starttls, ssl or none")
        default_port = 465 if security == "ssl" else 587
        try:
            port = int(os.environ.get("VIDTRANS_SMTP_PORT", "").strip() or default_port)
        except ValueError as exc:
            raise ValueError("VIDTRANS_SMTP_PORT must be an integer") from exc
        username = os.environ.get("VIDTRANS_SMTP_USERNAME", "").strip()
        return cls(
            host=os.environ.get("VIDTRANS_SMTP_HOST", "").strip(),
            port=port,
            username=username,
            password=os.environ.get("VIDTRANS_SMTP_PASSWORD", ""),
            sender=os.environ.get("VIDTRANS_SMTP_FROM", "").strip() or username,
            sender_name=os.environ.get("VIDTRANS_SMTP_FROM_NAME", "VidTrans").strip() or "VidTrans",
            security=security,
        )

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and "@" in self.sender)


class SmtpMailer:
    def __init__(self, config: MailConfig, *, smtp_factory=None, ssl_factory=None) -> None:
        self.config = config
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._ssl_factory = ssl_factory or smtplib.SMTP_SSL

    @property
    def configured(self) -> bool:
        return self.config.configured

    def send(self, *, to: str, subject: str, text: str) -> None:
        if not self.configured:
            raise MailDeliveryError("SMTP chưa được cấu hình")
        message = EmailMessage()
        message["From"] = formataddr((self.config.sender_name, self.config.sender))
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.config.sender.rsplit("@", 1)[-1])
        message.set_content(text)
        context = ssl.create_default_context()
        try:
            if self.config.security == "ssl":
                client = self._ssl_factory(
                    self.config.host, self.config.port, timeout=self.config.timeout, context=context
                )
            else:
                client = self._smtp_factory(self.config.host, self.config.port, timeout=self.config.timeout)
            with client:
                if self.config.security == "starttls":
                    client.starttls(context=context)
                if self.config.username:
                    client.login(self.config.username, self.config.password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            # Never include the message body: it carries the one-time code.
            raise MailDeliveryError(f"Không gửi được email ({type(exc).__name__})") from exc
