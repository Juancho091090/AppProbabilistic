"""Precios decimales 1X2 → probabilidades justas (sin margen) por casa y de referencia.

Probabilidad implícita de un precio decimal ``o``: ``1/o``. La suma de las tres
implícitas supera 1; el exceso (*overround*) es el margen. El método proporcional
lo reparte en proporción a cada implícita:

    p_justa_i = (1/o_i) / Σ_j (1/o_j)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

OUTCOMES_1X2 = ("home", "draw", "away")
_API_VALUES = {"Home": "home", "Draw": "draw", "Away": "away"}


@dataclass(frozen=True)
class OddsQuote:
    """Precios 1X2 de una casa para un partido."""

    bookmaker_id: int
    bookmaker_name: str
    home: float
    draw: float
    away: float
    source_updated_at: datetime | None = None

    @property
    def prices(self) -> tuple[float, float, float]:
        return (self.home, self.draw, self.away)


@dataclass(frozen=True)
class MarketProbs:
    """Probabilidades justas de referencia del mercado para un partido."""

    home: float
    draw: float
    away: float
    source: str  # nombre de la casa o "consenso (n casas)"
    overround: float  # margen medio de las casas usadas (0.05 = 5 %)

    def as_dict(self) -> dict[str, float]:
        return {"home": self.home, "draw": self.draw, "away": self.away}


def overround(prices: Sequence[float]) -> float:
    return sum(1.0 / o for o in prices) - 1.0


def devig_proportional(prices: Sequence[float]) -> list[float]:
    if len(prices) < 2 or any(o <= 1.0 for o in prices):
        raise ValueError(f"Precios decimales inválidos: {prices}")
    implied = [1.0 / o for o in prices]
    total = sum(implied)
    return [p / total for p in implied]


DEVIG_METHODS = {"proportional": devig_proportional}


def devig(prices: Sequence[float], method: str = "proportional") -> list[float]:
    try:
        fn = DEVIG_METHODS[method]
    except KeyError as exc:
        raise ValueError(f"Método de eliminación de margen desconocido: {method}") from exc
    return fn(prices)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_odds_response(items: Iterable[dict[str, Any]], bet_id: int = 1) -> list[OddsQuote]:
    """Respuesta de ``/odds`` de API-Football → precios 1X2 por casa.

    Se ignoran casas sin las tres selecciones o con precios no válidos (≤ 1)."""
    quotes: list[OddsQuote] = []
    for item in items:
        updated = _parse_dt(item.get("update"))
        for bk in item.get("bookmakers") or []:
            bet = next((b for b in bk.get("bets") or [] if b.get("id") == bet_id), None)
            if not bet:
                continue
            vals: dict[str, float] = {}
            for v in bet.get("values") or []:
                key = _API_VALUES.get(str(v.get("value")))
                try:
                    odd = float(v.get("odd"))
                except (TypeError, ValueError):
                    continue
                if key and odd > 1.0:
                    vals[key] = odd
            if len(vals) == 3:
                quotes.append(
                    OddsQuote(
                        int(bk["id"]),
                        str(bk.get("name", "")),
                        vals["home"],
                        vals["draw"],
                        vals["away"],
                        updated,
                    )
                )
    return quotes


def reference_probs(
    quotes: Sequence[OddsQuote],
    preferred_bookmakers: Sequence[int],
    fallback: str = "consensus",
    method: str = "proportional",
) -> MarketProbs | None:
    """Casa preferida (la primera disponible de la lista) o, si ninguna está, el
    consenso: media de las probabilidades justas de todas las casas, renormalizada."""
    by_id = {q.bookmaker_id: q for q in quotes}
    for bid in preferred_bookmakers:
        q = by_id.get(bid)
        if q:
            h, d, a = devig(q.prices, method)
            return MarketProbs(h, d, a, q.bookmaker_name, overround(q.prices))
    if fallback != "consensus" or not quotes:
        return None
    fair = [devig(q.prices, method) for q in quotes]
    mean = [sum(f[i] for f in fair) / len(fair) for i in range(3)]
    total = sum(mean)
    margin = sum(overround(q.prices) for q in quotes) / len(quotes)
    return MarketProbs(
        mean[0] / total,
        mean[1] / total,
        mean[2] / total,
        f"consenso ({len(quotes)} casas)",
        margin,
    )
