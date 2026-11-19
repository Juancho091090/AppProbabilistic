"""Envío por Telegram (Bot API). Divide mensajes largos respetando el límite de 4096."""

from __future__ import annotations

import json

import httpx

from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger, register_secret

log = get_logger(__name__)
TELEGRAM_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_LIMIT - 96) -> list[str]:
    """Corta por bloques (líneas en blanco) y, si hace falta, por líneas; nunca a mitad de etiqueta."""
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(block) <= limit:
            current = block
            continue
        current = ""
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    parts.append(current)
                current = line[:limit]
    if current:
        parts.append(current)
    return parts


class TelegramService:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        chat_id: str | None = None,
    ):
        """``chat_id`` permite usar el mismo bot para otro destino (p. ej. el canal público)."""
        target = chat_id or settings.telegram_chat_id
        if not (settings.telegram_bot_token and target):
            raise ValueError("Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        token = settings.telegram_bot_token.get_secret_value()
        register_secret(token)
        self._base = f"{settings.telegram_api_base_url}/bot{token}"
        self._url = f"{self._base}/sendMessage"
        self.chat_id = target
        self.last_message_id: int | None = None
        self._client = httpx.Client(timeout=settings.http_timeout_seconds, transport=transport)

    def close(self) -> None:
        self._client.close()

    def send_message(
        self, text: str, parse_mode: str | None = "HTML", reply_to: int | None = None
    ) -> int:
        """Envía (dividiendo si hace falta). Devuelve el número de mensajes enviados.

        ``reply_to``: id de un mensaje del mismo chat al que responde la primera parte (si ya
        no existe, se publica igual). El id del primer mensaje queda en ``last_message_id``."""
        sent = 0
        self.last_message_id = None
        for part in split_message(text):
            payload = {"chat_id": self.chat_id, "text": part, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_to and not sent:
                payload["reply_parameters"] = _reply(reply_to)
            resp = self._client.post(self._url, json=payload)
            if resp.status_code == 400 and parse_mode:
                # HTML rechazado: reintenta como texto plano para no perder el informe
                payload.pop("parse_mode")
                resp = self._client.post(self._url, json=payload)
            if resp.status_code >= 400:
                # No se registra la URL (contiene el token)
                raise RuntimeError(f"Telegram HTTP {resp.status_code}: {resp.text[:200]}")
            if not sent:
                self.last_message_id = _message_id(resp)
            sent += 1
        log.info("telegram_sent", extra={"messages": sent})
        return sent

    def send_video(self, path, caption: str = "", attempts: int = 3, wait: float = 10.0) -> None:
        """Envía un video (máx. 50 MB por la Bot API). El caption admite 1024 caracteres."""
        self._send_file(
            "sendVideo",
            "video",
            path,
            "video/mp4",
            caption,
            attempts,
            wait,
            {"supports_streaming": "true"},
        )

    def send_photo(
        self,
        path,
        caption: str = "",
        attempts: int = 3,
        wait: float = 10.0,
        reply_to: int | None = None,
    ) -> int | None:
        """Envía una imagen (máx. 10 MB). El caption admite 1024 caracteres (HTML).
        Devuelve el id del mensaje publicado (si Telegram lo informa)."""
        extra = {"parse_mode": "HTML"}
        if reply_to:
            extra["reply_parameters"] = json.dumps(_reply(reply_to))
        return self._send_file(
            "sendPhoto", "photo", path, "image/png", caption, attempts, wait, extra
        )

    def _send_file(self, method, field, path, mime, caption, attempts, wait, extra) -> int | None:
        """Reintenta ante errores 5xx o de conexión de Telegram (p. ej. 502 Bad Gateway)."""
        import time
        from pathlib import Path

        last = ""
        for attempt in range(1, attempts + 1):
            try:
                with open(path, "rb") as fh:
                    resp = self._client.post(
                        f"{self._base}/{method}",
                        data={"chat_id": self.chat_id, "caption": caption[:1024], **extra},
                        files={field: (Path(path).name, fh, mime)},
                        timeout=180,
                    )
            except httpx.TransportError as exc:
                last = f"conexión: {type(exc).__name__}"
            else:
                if resp.status_code < 400:
                    log.info("telegram_file_sent", extra={"method": method, "attempt": attempt})
                    self.last_message_id = _message_id(resp)
                    return self.last_message_id
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code < 500 and resp.status_code != 429:
                    break  # error del pedido: reintentar no sirve
            if attempt < attempts:
                time.sleep(wait * attempt)
        raise RuntimeError(f"Telegram {last}")

    def send_daily_report(self, html_text: str) -> int:
        return self.send_message(html_text, parse_mode="HTML")

    def send_error_notification(self, error: str) -> int:
        return self.send_message(f"⚠️ Error en el pipeline diario:\n{error[:3000]}", parse_mode=None)


def _reply(message_id: int) -> dict:
    return {"message_id": int(message_id), "allow_sending_without_reply": True}


def _message_id(resp) -> int | None:
    try:
        return int(resp.json()["result"]["message_id"])
    except Exception:  # noqa: BLE001 - respuesta sin id (p. ej. en pruebas)
        return None
