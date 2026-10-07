"""Envío por email (SMTP configurable con STARTTLS)."""

from __future__ import annotations

import html
import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage

from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger, register_secret

log = get_logger(__name__)


def markdown_to_basic_html(md: str) -> str:
    out = []
    for line in md.splitlines():
        esc = html.escape(line)
        if line.startswith("### "):
            out.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith("- "):
            out.append(f"<div>• {esc[2:]}</div>".replace("**", ""))
        elif line.strip():
            while "**" in esc:
                esc = esc.replace("**", "<b>", 1).replace("**", "</b>", 1)
            out.append(f"<p>{esc}</p>")
    return (
        "<html><body style='font-family:sans-serif;max-width:720px'>"
        + "\n".join(out)
        + "</body></html>"
    )


class EmailService:
    def __init__(self, settings: Settings, smtp_factory: Callable[..., smtplib.SMTP] | None = None):
        if not settings.email_enabled:
            raise ValueError("Email no configurado (SMTP_HOST / EMAIL_FROM / EMAIL_TO)")
        self.s = settings
        self._factory = smtp_factory or smtplib.SMTP
        if settings.smtp_password:
            register_secret(settings.smtp_password.get_secret_value())

    def _send(
        self,
        subject: str,
        text: str,
        html_body: str | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.s.email_from
        msg["To"] = ", ".join(self.s.email_recipients)
        msg.set_content(text)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        for filename, data in attachments or []:
            msg.add_attachment(data, maintype="application", subtype="pdf", filename=filename)
        with self._factory(
            self.s.smtp_host, self.s.smtp_port, timeout=self.s.http_timeout_seconds
        ) as smtp:
            if self.s.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if self.s.smtp_username and self.s.smtp_password:
                smtp.login(self.s.smtp_username, self.s.smtp_password.get_secret_value())
            smtp.send_message(msg)
        log.info(
            "email_sent", extra={"subject": subject, "recipients": len(self.s.email_recipients)}
        )

    def send_daily_report(
        self, report_date: str, summary_text: str, pdf: bytes | None = None
    ) -> None:
        """Correo corto con el resumen; el detalle completo va en el PDF adjunto."""
        attachments = [(f"informe_{report_date}.pdf", pdf)] if pdf else []
        self._send(
            f"Informe deportivo {report_date}",
            summary_text,
            markdown_to_basic_html(summary_text),
            attachments,
        )

    def send_critical_error(self, error: str) -> None:
        self._send("⚠️ Error crítico en el pipeline deportivo", error[:10000])
