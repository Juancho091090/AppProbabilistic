"""Voz sintética con Google Cloud Text-to-Speech (REST + clave de API).

Chirp 3 HD: el primer millón de caracteres de cada mes es gratis; un Short usa ~600.
"""

from __future__ import annotations

import base64
import io
import wave

import httpx

from sports_analytics.core.logging import get_logger, register_secret

log = get_logger(__name__)
TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_VOICE = "es-US-Chirp3-HD-Puck"


class GoogleTTS:
    def __init__(
        self,
        api_key: str,
        voice: str = DEFAULT_VOICE,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ):
        register_secret(api_key)
        self._key = api_key
        self.voice = voice
        self.language = "-".join(voice.split("-")[:2])  # es-US-Chirp3-HD-X -> es-US
        self.characters = 0
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def synthesize(self, text: str) -> bytes:
        """Devuelve un WAV (LINEAR16, 24 kHz mono)."""
        body = {
            "input": {"text": text},
            "voice": {"languageCode": self.language, "name": self.voice},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
        }
        resp = self._client.post(TTS_URL, params={"key": self._key}, json=body)
        if resp.status_code >= 400:
            # Sin la URL (lleva la clave). Se resume el error con su motivo (ErrorInfo.reason)
            raise RuntimeError(f"Google TTS HTTP {resp.status_code}: {_error_summary(resp)}")
        self.characters += len(text)
        return base64.b64decode(resp.json()["audioContent"])


def _error_summary(resp: httpx.Response) -> str:
    try:
        err = resp.json()["error"]
    except Exception:  # noqa: BLE001
        return resp.text[:300]
    reasons = [d.get("reason") for d in err.get("details", []) if d.get("reason")]
    return f"{err.get('status')} · {err.get('message', '')[:200]} · motivo: {', '.join(reasons) or '-'}"


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def concat_wavs(parts: list[tuple[bytes | None, float]], rate: int = 24000) -> bytes:
    """Une los audios de cada escena; cada una ocupa exactamente su duración (con silencio
    al final o, si no hay audio, todo silencio)."""
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        for data, seconds in parts:
            total = int(round(seconds * rate))
            frames = b""
            if data:
                with wave.open(io.BytesIO(data)) as src:
                    if src.getframerate() != rate or src.getsampwidth() != 2:
                        raise ValueError("Formato de audio inesperado")
                    frames = src.readframes(src.getnframes())
            frames = frames[: total * 2]
            dst.writeframes(frames + b"\x00\x00" * (total - len(frames) // 2))
    return out.getvalue()
