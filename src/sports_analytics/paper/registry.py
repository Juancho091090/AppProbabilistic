"""Registro simulado con reglas fijadas de antemano (investigación, sin dinero real).

Flujo:
1. Al generar el informe, cada regla elige resultados de los partidos del informe (1X2 o
   un lado de un mercado de línea: más/menos de 2.5 goles, más/menos de 8.5 córners,
   ambos marcan sí/no) y se guarda el precio de la casa de referencia en ESE momento
   (``price_taken``).
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
from sports_analytics.db.models import (
    FootballMatchRow,
    FootballStatistics,
    MarketOddsCheck,
    PaperSelection,
)
from sports_analytics.market.odds import LineQuote, OddsQuote, devig, overround
from sports_analytics.models.outputs import MatchForecast

log = get_logger(__name__)

OUTCOMES = ("home", "draw", "away")
LABEL = {"home": "Local", "draw": "Empate", "away": "Visitante"}
SIDES = ("yes", "no")  # yes = más de / sí; no = menos de / no


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


@dataclass(frozen=True)
class LineRule:
    """Regla de un mercado de dos lados: se toma el lado al que el modelo da más
    probabilidad (más/menos, sí/no). ``model_yes`` lee P(más de / sí) del pronóstico."""

    label: str
    market: str
    line: str
    model_yes: Callable[[MatchForecast], float | None]
    yes_label: str
    no_label: str


def _goals_over(line: str) -> Callable[[MatchForecast], float | None]:
    def fn(f: MatchForecast) -> float | None:
        p = ((f.markets.get("goals") or {}).get("goal_lines") or {}).get(line)
        return float(p["over"]) if p else None

    return fn


def _corners_over(line: str) -> Callable[[MatchForecast], float | None]:
    def fn(f: MatchForecast) -> float | None:
        p = ((f.markets.get("corners") or {}).get("lines") or {}).get(line)
        return float(p["over"]) if p else None

    return fn


def _btts(f: MatchForecast) -> float | None:
    p = (f.markets.get("goals") or {}).get("btts")
    return float(p) if p is not None else None


LINE_RULES: dict[str, LineRule] = {
    "goles_2_5_modelo": LineRule(
        "Goles 2.5: lado del modelo (más/menos)",
        "goals_total",
        "2.5",
        _goals_over("2.5"),
        "Más de 2.5 goles",
        "Menos de 2.5 goles",
    ),
    "corners_8_5_modelo": LineRule(
        "Córners 8.5: lado del modelo (más/menos)",
        "corners_total",
        "8.5",
        _corners_over("8.5"),
        "Más de 8.5 córners",
        "Menos de 8.5 córners",
    ),
    "btts_modelo": LineRule(
        "Ambos marcan: lado del modelo (sí/no)",
        "btts",
        "",
        _btts,
        "Ambos marcan: Sí",
        "Ambos marcan: No",
    ),
}


def rule_label(rule: str) -> str:
    if rule in RULES:
        return RULES[rule][0]
    if rule in LINE_RULES:
        return LINE_RULES[rule].label
    return rule


def selection_label(rule: str, selection: str) -> str:
    lr = LINE_RULES.get(rule)
    if lr is not None:
        return lr.yes_label if selection == "yes" else lr.no_label
    return LABEL.get(selection, selection)


def reference_line_quote(
    quotes: Sequence[LineQuote], market: str, line: str, preferred: Sequence[int]
) -> LineQuote | None:
    """Casa preferida que ofrezca esa línea exacta; si ninguna, la de menor margen."""
    offered = [q for q in quotes if q.market == market and q.line == line]
    if not offered:
        return None
    by_id = {q.bookmaker_id: q for q in offered}
    for b in preferred:
        if b in by_id:
            return by_id[b]
    return min(offered, key=lambda q: overround(q.prices))


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
    line_quotes: dict[str, list[LineQuote]] | None = None,
) -> int:
    """Una fila por (regla, partido, resultado). Idempotente: no duplica ni reescribe el
    precio tomado si el informe se vuelve a generar."""
    n = 0
    forecasts = list(forecasts)
    for f in forecasts:
        if f.sport != "football" or f.kickoff_utc <= now:
            continue
        n += _create_line_selections(
            session,
            f,
            (line_quotes or {}).get(f.event_id, []),
            rules,
            preferred,
            report_date,
            now,
        )
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


def _exists(session: Session, rule: str, event_id: str, selection: str) -> bool:
    return (
        session.scalar(
            select(PaperSelection.id).where(
                PaperSelection.rule == rule,
                PaperSelection.event_id == event_id,
                PaperSelection.selection == selection,
            )
        )
        is not None
    )


def _create_line_selections(
    session: Session,
    f: MatchForecast,
    quotes: Sequence[LineQuote],
    rules: Sequence[str],
    preferred: Sequence[int],
    report_date: date,
    now: datetime,
) -> int:
    n = 0
    for rule in rules:
        lr = LINE_RULES.get(rule)
        if lr is None:
            continue
        p_yes = lr.model_yes(f)
        q = reference_line_quote(quotes, lr.market, lr.line, preferred)
        if p_yes is None or q is None:
            continue
        i = 0 if p_yes >= 0.5 else 1
        side = SIDES[i]
        # Dos registros del mismo informe pueden cambiar de lado si el modelo cambia:
        # se conserva el primero (un solo lado por regla y partido).
        if _exists(session, rule, f.event_id, "yes") or _exists(session, rule, f.event_id, "no"):
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
                market=lr.market,
                line=lr.line,
                selection=side,
                bookmaker_id=q.bookmaker_id,
                bookmaker_name=q.bookmaker_name[:80],
                price_taken=float(q.prices[i]),
                fair_prob_taken=float(devig(q.prices)[i]),
                model_prob=float(p_yes if i == 0 else 1 - p_yes),
                confidence=None,
                status="open",
            )
        )
        n += 1
    return n


def _line_outcome(
    market: str, line: str, m: FootballMatchRow, st: FootballStatistics | None
) -> bool | None:
    """¿Ocurrió "más de / sí"? None si aún falta el dato (córners sin estadísticas)."""
    if market == "goals_total":
        return m.home_goals + m.away_goals > float(line)
    if market == "btts":
        return m.home_goals > 0 and m.away_goals > 0
    if market == "corners_total":
        if st is None or st.home_corners is None or st.away_corners is None:
            return None
        return st.home_corners + st.away_corners > float(line)
    raise ValueError(f"Mercado de línea desconocido: {market}")


def settle_selections(
    session: Session,
    quotes_by_event: Callable[[list[str]], dict[str, list[OddsQuote]]],
    now: datetime,
    void_after: timedelta = timedelta(days=7),
    lines_by_event: Callable[[list[str]], dict[str, list[LineQuote]]] | None = None,
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
    line_rows = [r for r in open_rows if (r.market or "1x2") != "1x2"]
    lines = lines_by_event(ids) if lines_by_event and line_rows else {}
    stats = (
        {
            st.match_id: st
            for st in session.scalars(
                select(FootballStatistics).where(
                    FootballStatistics.match_id.in_([m.id for m in matches.values()])
                )
            )
        }
        if line_rows
        else {}
    )
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
        if (r.market or "1x2") != "1x2":
            yes = _line_outcome(r.market, r.line or "", m, stats.get(m.id))
            if yes is None:  # córners: esperar estadísticas; anular si no llegan
                if late:
                    r.status, r.settled_at = "void", now
                    void += 1
                continue
            idx = SIDES.index(r.selection)
            r.outcome = int(yes == (idx == 0))
            close_l = next(
                (
                    q
                    for q in lines.get(r.event_id, [])
                    if q.bookmaker_id == r.bookmaker_id
                    and q.market == r.market
                    and q.line == (r.line or "")
                ),
                None,
            )
            if close_l is not None and m.id in finals:
                r.price_close = float(close_l.prices[idx])
                r.fair_prob_close = float(devig(close_l.prices)[idx])
            r.status, r.settled_at = "settled", now
            settled += 1
            continue
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
                "rule": rule_label(r.rule),
                "competition": r.competition,
                "match": r.match_label,
                "kickoff_local": r.kickoff_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                "selection": selection_label(r.rule, r.selection),
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
