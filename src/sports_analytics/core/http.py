"""Cliente HTTP base para APIs deportivas.

Incluye lo que exige el proyecto para no malgastar cuotas diarias:

* timeout configurable;
* reintentos con backoff exponencial + jitter ante 429, 5xx y errores de red;
* respeto de ``Retry-After``;
* presupuesto diario de llamadas por proveedor (persistido en disco), que corta
  antes de superar el límite del plan;
* caché en disco con TTL por petición (las respuestas repetidas no gastan cuota);
* logging de cada llamada SIN la clave (solo endpoint, parámetros y estado).
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from sports_analytics.core.logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504, 499}


class ApiError(RuntimeError):
    """Error definitivo de una API (tras reintentos o no reintentable)."""

    def __init__(self, provider: str, message: str, status: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status = status


class BudgetExceeded(ApiError):
    """Se alcanzó el presupuesto diario configurado para el proveedor."""


class QuotaExhausted(BudgetExceeded):
    """El proveedor informó que la cuota del plan está agotada (HTTP 429 de cuota)."""


class DailyBudget:
    """Contador de llamadas por día UTC, persistido en un JSON."""

    def __init__(self, path: Path, provider: str, limit: int):
        self.path = path
        self.provider = provider
        self.limit = limit

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _key(self) -> str:
        return f"{self.provider}:{datetime.now(UTC).date().isoformat()}"

    @property
    def used(self) -> int:
        return int(self._load().get(self._key(), 0))

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    def consume(self) -> None:
        data = self._load()
        key = self._key()
        if int(data.get(key, 0)) >= self.limit:
            raise BudgetExceeded(self.provider, f"presupuesto diario agotado ({self.limit})")
        # Conserva solo los contadores del mes en curso (evita crecer sin límite)
        month = key.rsplit(":", 1)[-1][:7]
        data = {k: v for k, v in data.items() if k.rsplit(":", 1)[-1].startswith(month)}
        data[key] = int(data.get(key, 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))


class DiskCache:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str, ttl_seconds: float) -> Any | None:
        path = self._path(key)
        if ttl_seconds <= 0 or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        if time.time() - payload["stored_at"] > ttl_seconds:
            return None
        return payload["data"]

    def set(self, key: str, data: Any) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps({"stored_at": time.time(), "data": data}))


@dataclass
class HttpApiClient:
    provider: str
    base_url: str
    headers: Mapping[str, str]
    daily_limit: int
    cache_dir: Path
    timeout: float = 20.0
    max_retries: int = 4
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    min_interval: float = 0.0  # segundos mínimos entre llamadas (límite por segundo)
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    last_rate_headers: dict[str, str] = field(default_factory=dict)
    calls_made: int = 0
    cache_hits: int = 0

    def __post_init__(self) -> None:
        self._last_call = 0.0
        self.cache = DiskCache(self.cache_dir / self.provider)
        self.budget = DailyBudget(self.cache_dir / "budget.json", self.provider, self.daily_limit)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=dict(self.headers),
            timeout=self.timeout,
            transport=self.transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_max)
            except ValueError:
                pass
        delay = self.backoff_base * (2**attempt)
        return min(delay + random.uniform(0, delay / 2), self.backoff_max)

    def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        cache_ttl: float = 0,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_key = f"{self.provider}{path}?{json.dumps(params, sort_keys=True, default=str)}"
        cached = self.cache.get(cache_key, cache_ttl)
        if cached is not None:
            self.cache_hits += 1
            log.info(
                "api_cache_hit", extra={"provider": self.provider, "path": path, "params": params}
            )
            return cached

        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self.budget.consume()  # cada intento real cuenta contra la cuota
            if self.min_interval > 0:
                wait = self.min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    self.sleep(wait)
            self._last_call = time.monotonic()
            started = time.perf_counter()
            try:
                response = self._client.get(path, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}"
                log.warning(
                    "api_network_error",
                    extra={
                        "provider": self.provider,
                        "path": path,
                        "attempt": attempt,
                        "error": last_error,
                    },
                )
            else:
                self.calls_made += 1
                self.last_rate_headers = {
                    k.lower(): v for k, v in response.headers.items() if "ratelimit" in k.lower()
                }
                log.info(
                    "api_call",
                    extra={
                        "provider": self.provider,
                        "path": path,
                        "params": params,
                        "status": response.status_code,
                        "attempt": attempt,
                        "ms": round((time.perf_counter() - started) * 1000),
                        "rate": self.last_rate_headers,
                        "budget_remaining": self.budget.remaining,
                    },
                )
                if response.status_code < 400:
                    data = response.json()
                    if cache_ttl > 0:
                        self.cache.set(cache_key, data)
                    return data
                # El cuerpo del error (p. ej. "You are not subscribed to this API") es
                # clave para diagnosticar; se recorta y se redacta en el log.
                body = response.text[:200].replace("\n", " ")
                last_error = f"HTTP {response.status_code}: {body}"
                if response.status_code == 429 and "quota" in body.lower():
                    # Cuota diaria/mensual agotada: reintentar solo gasta más llamadas
                    raise QuotaExhausted(self.provider, f"{last_error} en {path}", 429)
                if response.status_code not in RETRYABLE_STATUS:
                    raise ApiError(self.provider, f"{last_error} en {path}", response.status_code)
                if attempt < self.max_retries:
                    self.sleep(self._backoff(attempt, response.headers.get("retry-after")))
                    continue
                break
            if attempt < self.max_retries:
                self.sleep(self._backoff(attempt, None))
        raise ApiError(self.provider, f"falló tras {self.max_retries + 1} intentos: {last_error}")
