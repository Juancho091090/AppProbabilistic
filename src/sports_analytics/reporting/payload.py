"""Payload del informe diario: fuente de verdad numérica para el informe y para Claude."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from sports_analytics.models.outputs import MatchForecast


class CompetitionSection(BaseModel):
    name: str
    key: str
    forecasts: list[MatchForecast] = Field(default_factory=list)
    unavailable_reason: str | None = None  # "Datos no disponibles para esta competición."


class DailyReportPayload(BaseModel):
    report_date: date
    generated_at: datetime
    timezone: str
    football: list[CompetitionSection] = Field(default_factory=list)
    tennis: dict[str, list[MatchForecast]] = Field(default_factory=dict)  # ATP / WTA
    skipped: list[str] = Field(default_factory=list)  # partidos omitidos y motivo
    data_issues: list[str] = Field(default_factory=list)
    model_quality: list[dict[str, Any]] = Field(default_factory=list)  # métricas vivas globales
    track_record: list[dict[str, Any]] = Field(default_factory=list)  # aciertos esperado vs real
    load_summary: list[str] = Field(default_factory=list)  # estado de la ingesta

    @property
    def n_football(self) -> int:
        return sum(len(s.forecasts) for s in self.football)

    def n_tennis(self, tour: str) -> int:
        return len(self.tennis.get(tour, []))

    def all_forecasts(self) -> list[MatchForecast]:
        out = [f for s in self.football for f in s.forecasts]
        for items in self.tennis.values():
            out.extend(items)
        return out


def statistical_factors(f: MatchForecast) -> list[str]:
    """Factores deterministas (sin LLM) que explican la predicción con datos del modelo."""
    ctx, out = f.context, []
    if f.sport == "football":
        elo = ctx.get("elo", {})
        if elo:
            diff = elo["home"] - elo["away"]
            out.append(
                f"Elo: {f.home_or_a} {elo['home']:.0f} vs {f.away_or_b} {elo['away']:.0f} "
                f"(diferencia {diff:+.0f}, sin contar localía)"
            )
        lam = ctx.get("lambda", {})
        if lam:
            out.append(f"Goles esperados (Poisson): {lam['home']:.2f} – {lam['away']:.2f}")
        used = ctx.get("matches_used", {})
        if used:
            out.append(f"Partidos de histórico usados: {used['home']} y {used['away']}")
    else:
        elo, selo = ctx.get("elo", {}), ctx.get("surface_elo", {})
        if elo:
            out.append(f"Elo general: {elo['A']:.0f} vs {elo['B']:.0f}")
        if selo:
            out.append(
                f"Elo en {ctx.get('surface', 'superficie')}: {selo['A']:.0f} vs {selo['B']:.0f}"
            )
        rk = ctx.get("ranking", {})
        if rk and rk.get("A") and rk.get("B"):
            out.append(f"Ranking: #{rk['A']} vs #{rk['B']}")
    spread = ctx.get("model_spread")
    if spread is not None:
        out.append(f"Diferencia máxima entre modelos: {spread * 100:.1f} pp")
    missing = ctx.get("models_missing") or []
    if missing:
        out.append("Modelos sin datos suficientes: " + ", ".join(missing))
    return out
