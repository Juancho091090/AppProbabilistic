"""Narrativa del informe con Claude, subordinada a los números de Python.

Claude recibe un JSON con las probabilidades YA calculadas y devuelve solo texto
explicativo (resumen + factores por partido). Antes de usarlo se valida:

1. Toda cifra con % debe coincidir (±0.05 pp) con una probabilidad del payload.
2. Todo número decimal debe existir en el payload (goles esperados, líneas, Elo…).
3. No puede contener vocabulario de apuestas.

Si la validación falla o la API no responde, se devuelve ``None`` y el informe usa los
factores deterministas. Claude nunca modifica ni reemplaza probabilidades.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sports_analytics.config.settings import Settings
from sports_analytics.core.logging import get_logger, register_secret
from sports_analytics.reporting.payload import DailyReportPayload, statistical_factors

log = get_logger(__name__)

BANNED_TERMS = [
    "apuesta",
    "apuestas",
    "apostar",
    "apostador",
    "pick",
    "picks",
    "stake",
    "cuota",
    "cuotas",
    "momio",
    "bookmaker",
    "casa de apuestas",
    "value bet",
    "valor esperado",
    "apuesta segura",
    "parlay",
    "combinada",
    "bankroll",
    "tipster",
    "fija",
    "segura",
]

SYSTEM_PROMPT = """Eres un analista de datos deportivos. Redactas en español explicaciones \
breves de probabilidades YA calculadas por modelos estadísticos.

Reglas estrictas:
- Usa SOLO los números que aparecen en el JSON. No calcules, redondees de otra forma, \
estimes ni inventes cifras, porcentajes o estadísticas.
- Si mencionas un porcentaje, cópialo exactamente con un decimal tal como aparece en "pct".
- No modifiques ni reinterpretes probabilidades. No digas qué resultado "va a pasar".
- Lenguaje de analítica estadística. Prohibido cualquier lenguaje de apuestas \
(apuesta, pick, stake, cuota, valor, segura, etc.).
- Señala datos faltantes o desacuerdo entre modelos cuando el JSON lo indique.

Responde SOLO con JSON válido:
{"summary": "2-3 frases sobre la jornada",
 "matches": {"<event_id>": ["factor 1", "factor 2", "factor 3"]}}
Máximo 3 factores por partido, cada uno de una frase."""


def _pct(p: float) -> float:
    return round(p * 100, 1)


def compact_payload(payload: DailyReportPayload) -> dict[str, Any]:
    matches = []
    for f in payload.all_forecasts():
        m = f.markets
        item: dict[str, Any] = {
            "event_id": f.event_id,
            "sport": f.sport,
            "competition": f.competition,
            "match": f"{f.home_or_a} vs {f.away_or_b}",
            "confidence": f.confidence,
            "notes": f.data_notes,
            "factors": statistical_factors(f),
        }
        if f.sport == "football":
            item["pct"] = {k: _pct(v) for k, v in m["1x2"].items()}
            item["per_model_pct"] = {
                name: {k: _pct(v) for k, v in probs.items()}
                for name, probs in f.per_model.items()
                if probs
            }
            item["expected_goals"] = round(m["goals"]["expected_goals"]["total"], 2)
            item["goals_over_pct"] = {
                k: _pct(v["over"]) for k, v in m["goals"]["goal_lines"].items()
            }
            if m.get("corners"):
                item["expected_corners"] = round(m["corners"]["expected"]["total"], 2)
        else:
            item["pct"] = {"A": _pct(m["winner"]["A"]), "B": _pct(m["winner"]["B"])}
            item["at_least_one_set_pct"] = {k: _pct(v) for k, v in m["at_least_one_set"].items()}
            item["per_model_pct"] = {
                name: _pct(probs["A"]) for name, probs in f.per_model.items() if probs
            }
            item["expected_games"] = round(m["expected_games"], 1)
            item["surface"] = f.context.get("surface")
        matches.append(item)
    return {
        "date": payload.report_date.isoformat(),
        "matches": matches,
        "data_issues": payload.data_issues[:20],
    }


_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|pp)?")


def _allowed_numbers(obj: Any, acc: set[float]) -> set[float]:
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, int | float):
        acc.add(round(float(obj), 2))
    elif isinstance(obj, str):
        for num, _ in _NUM_RE.findall(obj):
            acc.add(round(float(num.replace(",", ".")), 2))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _allowed_numbers(k, acc)
            _allowed_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _allowed_numbers(v, acc)
    return acc


@dataclass
class ValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def validate_text(text: str, allowed: set[float]) -> ValidationResult:
    problems = []
    lowered = text.lower()
    for term in BANNED_TERMS:
        if re.search(rf"(?<![a-záéíóúñ]){re.escape(term)}(?![a-záéíóúñ])", lowered):
            problems.append(f"término prohibido: {term}")
    for num, unit in _NUM_RE.findall(text):
        value = round(float(num.replace(",", ".")), 2)
        if unit == "%":
            if not any(abs(value - a) <= 0.05 for a in allowed):
                problems.append(f"porcentaje no presente en el payload: {num}%")
        elif ("." in num or "," in num or value > 5) and not any(
            abs(value - a) <= 0.011 for a in allowed
        ):
            problems.append(f"número no presente en el payload: {num}")
    return ValidationResult(not problems, problems)


@dataclass
class Narrative:
    summary: str
    matches: dict[str, list[str]]


def validate_narrative(
    raw: dict[str, Any], compact: dict[str, Any]
) -> tuple[Narrative | None, list[str]]:
    allowed = _allowed_numbers(compact, set())
    valid_ids = {m["event_id"] for m in compact["matches"]}
    problems: list[str] = []
    summary = str(raw.get("summary") or "").strip()
    res = validate_text(summary, allowed)
    problems += [f"summary: {p}" for p in res.problems]
    matches: dict[str, list[str]] = {}
    for event_id, factors in (raw.get("matches") or {}).items():
        if event_id not in valid_ids or not isinstance(factors, list):
            continue
        clean = []
        for factor in factors[:3]:
            text = str(factor).strip()
            r = validate_text(text, allowed)
            if r.ok and text:
                clean.append(text)
            else:
                problems += [f"{event_id}: {p}" for p in r.problems]
        if clean:
            matches[event_id] = clean
    if problems:
        return None, problems
    return Narrative(summary, matches), []


class ClaudeNarrator:
    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    def _get_client(self):
        if self._client is None:
            import anthropic

            key = self.settings.anthropic_api_key.get_secret_value()
            register_secret(key)
            self._client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2)
        return self._client

    def narrate(self, payload: DailyReportPayload) -> Narrative | None:
        if not self.settings.claude_enabled or not payload.all_forecasts():
            return None
        compact = compact_payload(payload)
        try:
            response = self._get_client().messages.create(
                model=self.settings.anthropic_model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(compact, ensure_ascii=False)}],
            )
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            raw = json.loads(text[text.find("{") : text.rfind("}") + 1])
        except Exception as exc:  # API caída, JSON inválido…: el informe sigue sin narrativa
            log.warning("claude_narrative_failed", extra={"error": type(exc).__name__})
            return None
        narrative, problems = validate_narrative(raw, compact)
        if narrative is None:
            log.warning("claude_narrative_rejected", extra={"problems": problems[:10]})
        return narrative
