"""Experimentos A/B de configuración sobre el mismo backtest walk-forward.

Cada variante es un conjunto de cambios sobre ``models.yaml`` con rutas con puntos,
p. ej. ``{"football.league_effects.enabled": False}``. Todas se evalúan con los mismos
partidos y el mismo periodo, así que las diferencias son atribuibles al cambio.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from sports_analytics.backtesting.engine import run_backtest
from sports_analytics.config.loader import AppConfig, ModelsConfig
from sports_analytics.evaluation import scores as sc
from sports_analytics.evaluation.export import CONF_LABEL


def with_overrides(config: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    data = copy.deepcopy(config.models.model_dump())
    for path, value in overrides.items():
        node = data
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    return config.model_copy(update={"models": ModelsConfig(**data)})


def _summary(rows: Sequence[dict]) -> dict[str, Any]:
    p = np.array([r["p"] for r in rows])
    y = np.array([r["y"] for r in rows])
    clim = np.bincount(y, minlength=3) / len(y)
    pc = np.tile(clim, (len(y), 1))
    pooled_p, pooled_y = p.ravel(), np.eye(3)[y].ravel()
    out = {
        "n": len(y),
        "rps": sc.rps(p, y),
        "rpss": sc.skill(sc.rps(p, y), sc.rps(pc, y)),
        "logloss": sc.log_loss_multiclass(p, y),
        "brier": sc.brier_multiclass(p, y),
        "fav": sc.favorite_accuracy(p, y),
        "ece": sc.ece(pooled_p, pooled_y),
        "home_pred": float(p[:, 0].mean()),
        "home_real": float((y == 0).mean()),
    }
    comp: dict[str, list[int]] = defaultdict(list)
    conf: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        comp[r["competition"]].append(i)
        conf[r["confidence"]].append(i)
    out["by_competition"] = {
        c: {
            "n": len(ix),
            "rps": sc.rps(p[ix], y[ix]),
            "home_pred": float(p[ix, 0].mean()),
            "home_real": float((y[ix] == 0).mean()),
            "goals_exp": float(np.mean([rows[i]["xg"] for i in ix])),
            "goals_real": float(np.mean([rows[i]["goals"] for i in ix])),
        }
        for c, ix in comp.items()
    }
    out["by_confidence"] = {
        CONF_LABEL.get(c, c): {
            "n": len(ix),
            "share": len(ix) / len(y),
            "fav": sc.favorite_accuracy(p[ix], y[ix]),
            "rps": sc.rps(p[ix], y[ix]),
        }
        for c, ix in conf.items()
    }
    return out


def run_ab(
    history,
    config: AppConfig,
    variants: dict[str, dict[str, Any]],
    start: date,
    end: date,
    tz: ZoneInfo,
    refit_days: int,
    names: dict[str, str],
) -> dict[str, Any]:
    results = {}
    for name, overrides in variants.items():
        cfg = with_overrides(config, overrides)
        bt = run_backtest("football", history, cfg, start, end, tz, refit_days, names)
        results[name] = {"overrides": overrides, **_summary(bt.match_rows)}
    return results


def to_markdown(results: dict[str, Any], start: date, end: date) -> str:
    names = list(results)
    base = names[0]
    lines = [f"# Experimento A/B · fútbol · {start} → {end}", ""]
    for n in names:
        lines.append(f"- **{n}**: {results[n]['overrides'] or 'configuración actual'}")
    lines += [
        "",
        "## Global",
        "",
        "| Métrica | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    fmt = {
        "n": "{:.0f}",
        "rps": "{:.4f}",
        "rpss": "{:.1%}",
        "logloss": "{:.4f}",
        "brier": "{:.4f}",
        "fav": "{:.1%}",
        "ece": "{:.4f}",
        "home_pred": "{:.1%}",
        "home_real": "{:.1%}",
    }
    labels = {
        "n": "Partidos",
        "rps": "RPS (menor mejor)",
        "rpss": "RPSS vs climatología",
        "logloss": "Log-loss",
        "brier": "Brier multiclase",
        "fav": "Acierto del favorito",
        "ece": "ECE",
        "home_pred": "Local predicho",
        "home_real": "Local real",
    }
    for k, f in fmt.items():
        lines.append(
            f"| {labels[k]} | " + " | ".join(f.format(results[n][k]) for n in names) + " |"
        )
    lines += [
        "",
        "## Por competición (RPS y sesgo de local)",
        "",
        "| Competición | Partidos | "
        + " | ".join(f"RPS {n}" for n in names)
        + " | "
        + " | ".join(f"Local pred. {n}" for n in names)
        + " | Local real |",
        "|---|---|" + "---|" * (2 * len(names) + 1),
    ]
    comps = sorted(results[base]["by_competition"].items(), key=lambda kv: -kv[1]["n"])
    for c, v in comps:
        rps = [results[n]["by_competition"].get(c, {}).get("rps", float("nan")) for n in names]
        hp = [results[n]["by_competition"].get(c, {}).get("home_pred", float("nan")) for n in names]
        lines.append(
            f"| {c} | {v['n']} | "
            + " | ".join(f"{x:.4f}" for x in rps)
            + " | "
            + " | ".join(f"{x:.1%}" for x in hp)
            + f" | {v['home_real']:.1%} |"
        )
    for n in names:
        lines += [
            "",
            f"## Confianza · {n}",
            "",
            "| Nivel | Partidos | % | Acierto del favorito | RPS |",
            "|---|---|---|---|---|",
        ]
        for lvl in ("Alta", "Media", "Baja"):
            v = results[n]["by_confidence"].get(lvl)
            if v:
                lines.append(
                    f"| {lvl} | {v['n']} | {v['share']:.1%} | {v['fav']:.1%} | {v['rps']:.4f} |"
                )
    return "\n".join(lines) + "\n"
