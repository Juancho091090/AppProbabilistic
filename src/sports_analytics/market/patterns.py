"""Búsqueda de patrones en precios reales del 1X2 (solo investigación, sin recomendaciones).

Para cada regla sistemática se simula 1 unidad por selección al precio guardado (el último
antes del inicio, cercano al de cierre) y se mide el rendimiento con un intervalo de
confianza bootstrap. Un intervalo que contiene 0 % significa que no hay evidencia de
ventaja: el resultado es compatible con el azar.

Advertencias que se imprimen junto a los resultados:
* Se prueban muchas reglas a la vez: alguna saldrá positiva por azar (comparaciones
  múltiples). Una regla solo merece seguimiento si su intervalo excluye 0 y se mantiene
  en datos nuevos (prueba hacia adelante).
* El precio guardado es casi el de cierre; en la práctica el precio disponible a la hora
  del informe es otro.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from sports_analytics.market.odds import OddsQuote, devig, overround

OUTCOMES = ("Local", "Empate", "Visitante")


@dataclass(frozen=True)
class PricedMatch:
    label: str
    competition: str
    model: tuple[float, float, float] | None  # None si no hay predicción del informe
    fair: tuple[float, float, float]  # probabilidades sin margen (casa de referencia)
    price: tuple[float, float, float]  # precios de la casa de referencia
    best: tuple[float, float, float]  # mejor precio entre las casas guardadas
    outcome: int  # 0 local, 1 empate, 2 visitante
    confidence: str | None = None


def priced_match(
    label: str,
    competition: str,
    quotes: Sequence[OddsQuote],
    outcome: int,
    preferred: Sequence[int],
    model: tuple[float, float, float] | None = None,
    confidence: str | None = None,
) -> PricedMatch | None:
    if not quotes:
        return None
    by_id = {q.bookmaker_id: q for q in quotes}
    ref = next((by_id[b] for b in preferred if b in by_id), None)
    if ref is None:  # sin casa preferida: la de menor margen
        ref = min(quotes, key=lambda q: overround(q.prices))
    fair = tuple(devig(ref.prices))
    best = tuple(max(q.prices[i] for q in quotes) for i in range(3))
    return PricedMatch(label, competition, model, fair, ref.prices, best, outcome, confidence)


# Regla: partido -> lista de índices de resultado seleccionados (0, 1 o 2)
Rule = Callable[[PricedMatch], list[int]]


def _fav(m: PricedMatch) -> int:
    return int(np.argmax(m.fair))


RULES: dict[str, Rule] = {
    "Favorito del mercado (todos)": lambda m: [_fav(m)],
    "Favorito del mercado ≥ 60 %": lambda m: [_fav(m)] if max(m.fair) >= 0.60 else [],
    "Resultado con ≤ 20 % en el mercado": lambda m: [i for i in range(3) if m.fair[i] <= 0.20],
    "Empate (todos)": lambda m: [1],
    "Local (todos)": lambda m: [0],
    "Visitante (todos)": lambda m: [2],
    "Modelo ≥ 5 pp sobre el mercado": lambda m: (
        [i for i in range(3) if m.model[i] - m.fair[i] >= 0.05] if m.model else []
    ),
    "Modelo ≥ 10 pp sobre el mercado": lambda m: (
        [i for i in range(3) if m.model[i] - m.fair[i] >= 0.10] if m.model else []
    ),
    "Valor esperado del modelo > 0 (p·precio > 1)": lambda m: (
        [i for i in range(3) if m.model[i] * m.price[i] > 1] if m.model else []
    ),
    "Favorito del modelo con confianza Alta": lambda m: (
        [int(np.argmax(m.model))] if m.model and m.confidence == "high" else []
    ),
}


def _returns(matches: Sequence[PricedMatch], rule: Rule, use_best: bool) -> np.ndarray:
    out = []
    for m in matches:
        prices = m.best if use_best else m.price
        for i in rule(m):
            out.append(prices[i] - 1 if m.outcome == i else -1.0)
    return np.array(out, dtype=float)


def evaluate_rules(
    matches: Sequence[PricedMatch], samples: int = 4000, seed: int = 7
) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for name, rule in RULES.items():
        r = _returns(matches, rule, use_best=False)
        if len(r) == 0:
            continue
        rb = _returns(matches, rule, use_best=True)
        boots = rng.choice(r, size=(samples, len(r)), replace=True).mean(axis=1)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        rows.append(
            {
                "rule": name,
                "n": len(r),
                "hits": int((r > 0).sum()),
                "roi": float(r.mean()),
                "roi_ci": (float(lo), float(hi)),
                "roi_best": float(rb.mean()),
                "avg_price": float(np.mean([p for p in (r[r > 0] + 1)])) if (r > 0).any() else 0.0,
            }
        )
    return rows


def market_calibration(matches: Sequence[PricedMatch]) -> list[dict]:
    """¿El mercado (sin margen) está bien calibrado? Por rango de probabilidad."""
    edges = [0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0]
    p = np.array([m.fair[i] for m in matches for i in range(3)])
    y = np.array([int(m.outcome == i) for m in matches for i in range(3)])
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lo) & (p < hi)
        if mask.sum():
            rows.append(
                {
                    "bin": f"{lo:.0%}–{hi:.0%}",
                    "n": int(mask.sum()),
                    "pred": float(p[mask].mean()),
                    "obs": float(y[mask].mean()),
                }
            )
    return rows


def to_markdown(matches: Sequence[PricedMatch]) -> str:
    n_model = sum(1 for m in matches if m.model)
    margins = [overround(m.price) for m in matches]
    lines = [
        "# Patrones en precios reales del 1X2 (investigación)",
        "",
        f"- Partidos con precio y resultado: {len(matches)} (con predicción del informe: {n_model})",
        f"- Margen medio de la casa de referencia: {np.mean(margins):.1%}",
        "- 1 unidad por selección al último precio antes del inicio. IC 95 % por bootstrap.",
        "",
        "## Reglas sistemáticas",
        "",
        "| Regla | Selecciones | Aciertos | Rendimiento | IC 95 % | Con mejor precio | Precio medio acertado |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in evaluate_rules(matches):
        lo, hi = r["roi_ci"]
        lines.append(
            f"| {r['rule']} | {r['n']} | {r['hits']} | {r['roi']:+.1%} | "
            f"[{lo:+.1%}, {hi:+.1%}] | {r['roi_best']:+.1%} | {r['avg_price']:.2f} |"
        )
    lines += [
        "",
        "## Calibración del mercado (probabilidad sin margen vs frecuencia real)",
        "",
        "| Rango | Resultados | Mercado decía | Ocurrió |",
        "|---|---|---|---|",
    ]
    for c in market_calibration(matches):
        lines.append(f"| {c['bin']} | {c['n']} | {c['pred']:.1%} | {c['obs']:.1%} |")
    lines += [
        "",
        "Se prueban varias reglas a la vez: alguna puede salir positiva por azar. Solo un "
        "intervalo que excluya 0 % y se repita en datos nuevos indicaría un patrón real.",
        "",
    ]
    return "\n".join(lines)
