"""Registro simulado con reglas fijadas de antemano (investigación, sin dinero real).

Flujo:
1. Al generar el informe, cada regla elige resultados 1X2 de los partidos del informe y
   se guarda el precio de la casa de referencia en ESE momento (``price_taken``).
2. Cuando el partido termina y ya hay consulta final de precios (último precio antes del
   inicio = cierre), se guardan el precio de cierre y el resultado.

Métricas (se calculan en la hoja con fórmulas):
* Ganancia por selección (1 unidad): precio − 1 si acierta, −1 si no.
* CLV = precio tomado × probabilidad sin margen al cierre − 1. Positivo de forma sostenida
  indica que se tomaron precios mejores que el consenso final del mercado, la señal más
  temprana de ventaja real.

Las reglas no cambian con los resultados: cambiar una regla crea otra con otro nombre.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sports_analytics.core.logging import get_logger
from sports_analytics.db.models import FootballMatchRow, MarketOddsCheck, PaperSelection
from sports_analytics.market.odds import OddsQuote, devig, overround
from sports_analytics.models.outputs import MatchForecast

log = get_logger(__name__)

OUTCOMES = ("home", "draw", "away")
LABEL = {"home": "Local", "draw": "Empate", "away": "Visitante"}


@dataclass(frozen=True)
class Candidate:
    forecast: MatchForecast
    quote: OddsQuote
    fair: tuple[float, float, float]
    model: tuple[float, float, float]


# Cada regla devuelve los índices (0 local, 1 empate, 2 visitante) que selecciona.
RuleFn = Callable[[Candidate], list[int]]


def _argmax(x: Sequence[float]) -> int:
    return max(range(len(x)), key=lambda i: x[i])


RULES: dict[str, tuple[str, RuleFn]] = {
    "mercado_favorito_60": (
        "Favorito del mercado con 60 % o más",
        lambda c: [_argmax(c.fair)] if max(c.fair) >= 0.60 else [],
    ),
    "modelo_alta": (
        "Favorito del modelo con confianza Alta",
        lambda c: [_argmax(c.model)] if c.forecast.confidence == "high" else [],
    ),
    "modelo_valor_esperado": (
        "Valor esperado del modelo positivo (control)",
        lambda c: [i for i in range(3) if c.model[i] * c.quote.prices[i] > 1],
    ),
    "mercado_favorito": (
        "Favorito del mercado, todos (referencia)",
        lambda c: [_argmax(c.fair)],
    ),
}


def reference_quote(quotes: Sequence[OddsQuote], preferred: Sequence[int]) -> OddsQuote | None:
    if not quotes:
        return None
    by_id = {q.bookmaker_id: q for q in quotes}
    for b in preferred:
        if b in by_id:
            return by_id[b]
    return min(quotes, key=lambda q: overround(q.prices))


def create_selections(
    session: Session,
    forecasts: Iterable[MatchForecast],
    quotes: dict[str, list[OddsQuote]],
    rules: Sequence[str],
    preferred: Sequence[int],
    report_date: date,
    now: datetime,
) -> int:
    """Una fila por (regla, partido, resultado). Idempotente: no duplica ni reescribe el
    precio tomado si el informe se vuelve a generar."""
    n = 0
    for f in forecasts:
        if f.sport != "football" or f.kickoff_utc <= now:
            continue
        q = reference_quote(quotes.get(f.event_id, []), preferred)
        if q is None:
            continue
        x = f.markets["1x2"]
        cand = Candidate(
            f,
            q,
            tuple(devig(q.prices)),
            (float(x["home"]), float(x["draw"]), float(x["away"])),
        )
        for rule in rules:
            if rule not in RULES:
                continue
            for i in RULES[rule][1](cand):
                exists = session.scalar(
                    select(PaperSelection.id).where(
                        PaperSelection.rule == rule,
                        PaperSelection.event_id == f.event_id,
                        PaperSelection.selection == OUTCOMES[i],
                    )
                )
                if exists:
                    continue
                session.add(
                    PaperSelection(
                        created_at=now,
                        report_date=report_date,
                        rule=rule,
                        event_id=f.event_id,
                        competition=f.competition[:200],
                        match_label=f"{f.home_or_a} vs {f.away_or_b}"[:200],
                        kickoff_utc=f.kickoff_utc,
                        selection=OUTCOMES[i],
                        bookmaker_id=q.bookmaker_id,
                        bookmaker_name=q.bookmaker_name[:80],
                        price_taken=float(q.prices[i]),
                        fair_prob_taken=float(cand.fair[i]),
                        model_prob=float(cand.model[i]),
                        confidence=f.confidence,
                        status="open",
                    )
                )
                n += 1
    session.flush()
    log.info("paper_selections_created", extra={"n": n})
    return n


def settle_selections(
    session: Session,
    quotes_by_event: Callable[[list[str]], dict[str, list[OddsQuote]]],
    now: datetime,
    void_after: timedelta = timedelta(days=7),
) -> dict[str, int]:
    """Añade resultado y precio de cierre a las selecciones abiertas ya jugadas."""
    open_rows = list(session.scalars(select(PaperSelection).where(PaperSelection.status == "open")))
    if not open_rows:
        return {"settled": 0, "void": 0}
    ids = sorted({r.event_id for r in open_rows})
    matches = {
        m.external_id: m
        for m in session.scalars(
            select(FootballMatchRow).where(FootballMatchRow.external_id.in_(ids))
        )
    }
    finals = {
        mid
        for (mid,) in session.execute(
            select(MarketOddsCheck.match_id).where(MarketOddsCheck.final.is_(True))
        )
    }
    quotes = quotes_by_event(ids)
    settled = void = 0
    for r in open_rows:
        m = matches.get(r.event_id)
        late = now - r.kickoff_utc > void_after
        if m is None or m.status != "finished" or m.home_goals is None:
            if m is not None and m.status in ("postponed", "cancelled", "abandoned") or late:
                r.status, r.settled_at = "void", now
                void += 1
            continue
        if m.id not in finals and not late:
            continue  # esperar la consulta final de precios (cierre)
        res = 0 if m.home_goals > m.away_goals else 1 if m.home_goals == m.away_goals else 2
        idx = OUTCOMES.index(r.selection)
        r.outcome = int(res == idx)
        close = next(
            (q for q in quotes.get(r.event_id, []) if q.bookmaker_id == r.bookmaker_id), None
        )
        if close is not None and m.id in finals:
            r.price_close = float(close.prices[idx])
            r.fair_prob_close = float(devig(close.prices)[idx])
        r.status, r.settled_at = "settled", now
        settled += 1
    session.flush()
    return {"settled": settled, "void": void}


def export_rows(session: Session, tz) -> list[dict]:
    """Filas para la hoja, en orden de creación (la fila de la hoja = posición + 1)."""
    rows = []
    for i, r in enumerate(
        session.scalars(select(PaperSelection).order_by(PaperSelection.id)), start=1
    ):
        rows.append(
            {
                "n": i,
                "id": r.id,
                "report_date": r.report_date.isoformat(),
                "rule": RULES.get(r.rule, (r.rule,))[0],
                "competition": r.competition,
                "match": r.match_label,
                "kickoff_local": r.kickoff_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                "selection": LABEL.get(r.selection, r.selection),
                "bookmaker": r.bookmaker_name,
                "price_taken": r.price_taken,
                "fair_prob_taken": round(r.fair_prob_taken, 4),
                "model_prob": round(r.model_prob, 4) if r.model_prob is not None else None,
                "confidence": {"high": "Alta", "medium": "Media", "low": "Baja"}.get(
                    r.confidence or "", r.confidence
                ),
                "price_close": r.price_close,
                "fair_prob_close": (
                    round(r.fair_prob_close, 4) if r.fair_prob_close is not None else None
                ),
                "result": (
                    "Pendiente"
                    if r.status == "open"
                    else "Anulado"
                    if r.status == "void"
                    else "Acertó"
                    if r.outcome == 1
                    else "Falló"
                ),
                "updated": (r.settled_at or r.created_at).isoformat(),
            }
        )
    return rows


SHEET_COLUMNS = [
    "n",
    "report_date",
    "rule",
    "competition",
    "match",
    "kickoff_local",
    "selection",
    "bookmaker",
    "price_taken",
    "fair_prob_taken",
    "model_prob",
    "confidence",
    "price_close",
    "fair_prob_close",
    "result",
]


def _formulas(row: int) -> list[str]:
    return [
        f'=IF(O{row}="Acertó",I{row}-1,IF(O{row}="Falló",-1,""))',
        f'=IF(N{row}="","",I{row}*N{row}-1)',
        f'=IF(M{row}="","",I{row}/M{row}-1)',
    ]


def sheet_updates(rows: list[dict], since: datetime, tab: str) -> dict | None:
    """Un solo bloque A:R desde la primera fila creada o liquidada desde ``since`` hasta
    la última. A:O son datos; P:R fórmulas (ganancia, CLV, precio frente a cierre)."""
    changed = [r["n"] for r in rows if datetime.fromisoformat(r["updated"]) >= since]
    if not changed:
        return None
    first, last = min(changed), len(rows)
    values = []
    for r in rows[first - 1 : last]:
        row = r["n"] + 1  # fila 1 = encabezado
        values.append(["" if r[c] is None else r[c] for c in SHEET_COLUMNS] + _formulas(row))
    return {"range": f"'{tab}'!A{first + 1}:R{last + 1}", "formulas": values}
