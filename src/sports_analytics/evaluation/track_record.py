"""Balance de aciertos en lenguaje sencillo: lo que el modelo esperaba frente a lo que pasó.

Para cada partido ya resuelto se toma el resultado más probable según el modelo final
(el favorito del 1X2, el ganador en tenis, más/menos de 2.5 goles) y se mira si ocurrió.

* ``hit_rate``: porcentaje de partidos en que ocurrió lo más probable.
* ``expected_rate``: media de la probabilidad asignada a esa opción, es decir, el
  porcentaje de aciertos que el propio modelo esperaba. Si ambos se parecen, las
  probabilidades están bien medidas; si el real es mucho menor, el modelo se confía.

Solo lectura de predicciones ya guardadas; no recalcula probabilidades.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

GOALS_MODEL = "dixon_coles"
CONF_ORDER = ("high", "medium", "low")
CONF_LABEL = {"high": "Alta", "medium": "Media", "low": "Baja"}


@dataclass(frozen=True)
class TrackRow:
    label: str
    n: int
    hits: int
    expected_rate: float

    @property
    def hit_rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "n": self.n,
            "hits": self.hits,
            "hit_rate": self.hit_rate,
            "expected_rate": self.expected_rate,
        }


@dataclass
class _Pick:
    prob: float
    hit: bool
    confidence: str


def _latest(pairs: Iterable, final_model: str, sport: str, market: str, line_key: str = ""):
    """Agrupa por partido y se queda con la predicción más reciente de cada uno."""
    by_event: dict[str, list] = defaultdict(list)
    for p, r in pairs:
        model_ok = (
            p.model.startswith(final_model[:-1])
            if final_model.endswith("*")
            else p.model == final_model
        )
        if p.sport != sport or p.market != market or not model_ok:
            continue
        if (p.line_key or "") != line_key or r.outcome is None:
            continue
        by_event[p.event_id].append((p, r))
    out = {}
    for event_id, items in by_event.items():
        last_date = max(p.prediction_date for p, _ in items)
        out[event_id] = [(p, r) for p, r in items if p.prediction_date == last_date]
    return out


def _favorite_picks(pairs, final_model: str, sport: str, market: str) -> list[_Pick]:
    picks = []
    for items in _latest(pairs, final_model, sport, market).values():
        p, r = max(items, key=lambda pr: pr[0].probability)
        picks.append(_Pick(p.probability, int(r.outcome) == 1, p.confidence))
    return picks


def _binary_picks(pairs, final_model: str, market: str, line_key: str) -> list[_Pick]:
    picks = []
    for items in _latest(pairs, final_model, "football", market, line_key).values():
        p, r = items[0]
        yes = p.probability >= 0.5
        picks.append(
            _Pick(max(p.probability, 1 - p.probability), int(r.outcome) == int(yes), p.confidence)
        )
    return picks


def _row(label: str, picks: list[_Pick]) -> TrackRow:
    n = len(picks)
    return TrackRow(
        label,
        n,
        sum(x.hit for x in picks),
        sum(x.prob for x in picks) / n if n else 0.0,
    )


def track_record(pairs: Iterable, final_model: str, min_n: int = 1) -> list[dict]:
    pairs = list(pairs)
    rows: list[TrackRow] = []
    fav = _favorite_picks(pairs, final_model, "football", "1x2")
    if fav:
        rows.append(_row("Fútbol 1X2 · favorito (todos)", fav))
        for c in CONF_ORDER:
            sub = [x for x in fav if x.confidence == c]
            if sub:
                rows.append(_row(f"Fútbol 1X2 · confianza {CONF_LABEL[c]}", sub))
    # Goles y BTTS salen de la matriz de Dixon-Coles y se guardan con ese nombre de modelo.
    goals = _binary_picks(pairs, GOALS_MODEL, "goals_total", "2.5")
    if goals:
        rows.append(_row("Fútbol · más/menos de 2.5 goles", goals))
    btts = _binary_picks(pairs, GOALS_MODEL, "btts", "")
    if btts:
        rows.append(_row("Fútbol · ambos marcan (sí/no)", btts))
    tennis = _favorite_picks(pairs, final_model, "tennis", "winner")
    if tennis:
        rows.append(_row("Tenis · ganador", tennis))
    return [r.as_dict() for r in rows if r.n >= min_n]


EVENT_LABEL = {"home_win": "Local", "draw": "Empate", "away_win": "Visitante"}


def football_1x2_detail(pairs: Iterable, final_model: str) -> list[dict]:
    """Un renglón por partido de fútbol resuelto: favorito, su probabilidad y si ocurrió."""
    out = []
    for event_id, items in _latest(list(pairs), final_model, "football", "1x2").items():
        p, r = max(items, key=lambda pr: pr[0].probability)
        real = next((q.event for q, rr in items if int(rr.outcome) == 1), None)
        out.append(
            {
                "event_id": event_id,
                "date": p.prediction_date.isoformat(),
                "competition": p.competition,
                "favorite": EVENT_LABEL.get(p.event, p.event),
                "probability": p.probability,
                "result": EVENT_LABEL.get(real, real or "?"),
                "confidence": CONF_LABEL.get(p.confidence, p.confidence),
                "hit": int(r.outcome) == 1,
            }
        )
    return sorted(out, key=lambda x: (x["date"], x["competition"]))


GOAL_LINES = ("1.5", "2.5", "3.5")
CORNER_LINES = ("8.5", "9.5", "10.5")
CORNERS_MODEL = "corners_*"  # corners_<familia de distribución>


def _line_row(label: str, items: list[tuple[float, int]]) -> dict:
    """items: (probabilidad de 'sí/más de', ocurrió 0/1) por partido."""
    n = len(items)
    pred = sum(p for p, _ in items) / n
    real = sum(y for _, y in items) / n
    hits = sum(int((p >= 0.5) == bool(y)) for p, y in items)
    return {
        "label": label,
        "n": n,
        "predicted_rate": pred,
        "observed_rate": real,
        "hits": hits,
        "hit_rate": hits / n,
        "expected_rate": sum(max(p, 1 - p) for p, _ in items) / n,
    }


def lines_record(pairs: Iterable, min_n: int = 1) -> list[dict]:
    """Por línea de goles y córners (y BTTS): % predicho medio frente a % que ocurrió, y
    cuántas veces acertó el lado más probable (más/menos)."""
    pairs = list(pairs)
    specs = [
        *((f"Más de {ln} goles", GOALS_MODEL, "goals_total", ln) for ln in GOAL_LINES),
        ("Ambos marcan", GOALS_MODEL, "btts", ""),
        *((f"Más de {ln} córners", CORNERS_MODEL, "corners_total", ln) for ln in CORNER_LINES),
    ]
    out = []
    for label, model, market, line in specs:
        groups = _latest(pairs, model, "football", market, line)
        items = [(its[0][0].probability, int(its[0][1].outcome)) for its in groups.values()]
        if len(items) >= min_n:
            out.append(_line_row(label, items))
    return out
