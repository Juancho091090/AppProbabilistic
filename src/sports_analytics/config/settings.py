"""Configuración de entorno (secretos y parámetros de ejecución).

Los secretos se declaran como ``SecretStr`` para que nunca aparezcan en logs,
``repr`` ni trazas de error. Los parámetros de negocio (ligas, pesos, líneas)
no viven aquí sino en los YAML de este mismo paquete (ver ``loader.py``).
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Fuentes de datos
    api_football_key: SecretStr | None = None
    api_football_base_url: str = "https://v3.football.api-sports.io"
    api_football_daily_limit: int = Field(default=100, ge=1)
    api_football_min_interval: float = Field(default=0.25, ge=0)  # ≤240 llamadas/min

    # Tennis API (RapidAPI: tennis-api-atp-wta-itf). Plan FREE = 50 llamadas/día.
    tennis_api_key: SecretStr | None = None
    tennis_api_host: str = "tennis-api-atp-wta-itf.p.rapidapi.com"
    tennis_api_base_url: str = "https://tennis-api-atp-wta-itf.p.rapidapi.com"
    tennis_api_daily_limit: int = Field(default=45, ge=1)  # margen bajo el tope de 50

    # Ingesta (presupuestos por ejecución)
    football_history_seasons: int = Field(default=3, ge=1)  # actual + 2 anteriores
    football_stats_per_run: int = Field(default=2500, ge=0)  # llamadas /fixtures/statistics
    tennis_history_days: int = Field(default=730, ge=30)
    tennis_refresh_days: int = Field(default=3, ge=1)  # días recientes que se re-consultan
    tennis_window_days: int = Field(default=7, ge=1)
    tennis_backfill_calls_per_run: int = Field(default=30, ge=0)

    # Claude
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    # Telegram
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_api_base_url: str = "https://api.telegram.org"

    # Email
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    email_to: str | None = None  # admite varios separados por coma

    # Base de datos
    database_url: str = "postgresql+psycopg://sports:sports@localhost:5432/sports"

    # Ejecución
    app_timezone: str = "America/Bogota"
    log_level: str = "INFO"
    dry_run: bool = False
    cache_dir: str = "data/cache"
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 4

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, value):
        # En GitHub Actions un secreto inexistente llega como "" -> se trata como no definido
        if isinstance(value, str) and value.strip() == "":
            raise PydanticUseDefault()
        return value

    @field_validator("app_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Zona horaria inválida: {value}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def email_recipients(self) -> list[str]:
        if not self.email_to:
            return []
        return [addr.strip() for addr in self.email_to.split(",") if addr.strip()]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_from and self.email_recipients)

    @property
    def claude_enabled(self) -> bool:
        return self.anthropic_api_key is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
