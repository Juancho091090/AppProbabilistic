"""Métricas avanzadas a partir de las filas por partido de un backtest walk-forward.

``football_tables`` y ``tennis_tables`` devuelven tablas {título, columnas, filas} listas
para volcar en la hoja de métricas. Las referencias usadas:

* Climatología: las frecuencias reales del propio periodo (p. ej. 44 % local, 27 %
  empate, 29 % visitante). Superarla exige discriminar partido a partido.
* Uniforme: 1/3 a cada resultado (o 50 % en tenis).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import numpy as np

from sports_analytics.evaluation import scores as sc

CONF_LABEL = {"high": "Alta", "medium": "Media", "low": "Baja"}
EVENTS_1X2 = ("Gana local", "Empate", "Gana visitante")


def _r(x: Any, nd: int = 4) -> Any:
    if isinstance(x, float | np.floating):
        return None if np.isnan(x) else round(float(x), nd)
    return x


def _table(title: str, columns: list[str], rows: list[list], note: str = "") -> dict:
    return {
        "title": title,
        "columns": columns,
        "rows": [[_r(v) for v in r] for r in rows],
        "note": note,
    }


def _block_1x2(p: np.ndarray, y: np.ndarray, clim: np.ndarray) -> dict[str, float]:
    pc = np.tile(clim, (len(y), 1))
    return {
        "n": len(y),
        "rps": sc.rps(p, y),
        "rps_clim": sc.rps(pc, y),
        "brier": sc.brier_multiclass(p, y),
        "logloss": sc.log_loss_multiclass(p, y),
        "fav": sc.favorite_accuracy(p, y),
    }


def _week_start(dt) -> str:
    d = dt.date()
    return (d - timedelta(days=d.weekday())).isoformat()


def football_tables(rows: Sequence[dict]) -> list[dict]:
    if not rows:
        return []
    p = np.array([r["p"] for r in rows])
    y = np.array([r["y"] for r in rows])
    n = len(y)
    clim = np.bincount(y, minlength=3) / n
    unif = np.full((n, 3), 1 / 3)
    pc = np.tile(clim, (n, 1))
    pooled_p = p.ravel()
    pooled_y = np.eye(3)[y].ravel()
    tables = []

    # 1. Resumen global 1X2
    g = {
        "RPS": (sc.rps(p, y), sc.rps(pc, y), sc.rps(unif, y)),
        "Brier multiclase": (
            sc.brier_multiclass(p, y),
            sc.brier_multiclass(pc, y),
            sc.brier_multiclass(unif, y),
        ),
        "Log-loss": (
            sc.log_loss_multiclass(p, y),
            sc.log_loss_multiclass(pc, y),
            sc.log_loss_multiclass(unif, y),
        ),
        "Acierto del favorito": (sc.favorite_accuracy(p, y), float(clim.max()), 1 / 3),
    }
    tables.append(
        _table(
            "Resumen 1X2",
            [
                "Métrica",
                "Modelo",
                "Climatología",
                "Uniforme",
                "Skill vs climatología",
                "Skill vs uniforme",
            ],
            [
                [
                    k,
                    m,
                    c,
                    u,
                    sc.skill(m, c) if k != "Acierto del favorito" else None,
                    sc.skill(m, u) if k != "Acierto del favorito" else None,
                ]
                for k, (m, c, u) in g.items()
            ]
            + [
                ["ECE (eventos 1X2)", sc.ece(pooled_p, pooled_y), None, None, None, None],
                ["Partidos", n, None, None, None, None],
            ],
            "Skill = 1 − métrica del modelo / métrica de referencia. Positivo es mejor que la "
            "referencia. Climatología = frecuencias reales del periodo.",
        )
    )

    # 2. Por resultado: descomposición de Murphy y AUC
    ev_rows = []
    for k, name in enumerate(EVENTS_1X2):
        pk, yk = p[:, k], (y == k).astype(int)
        mu = sc.murphy(pk, yk)
        ev_rows.append(
            [
                name,
                n,
                pk.mean(),
                yk.mean(),
                sc.brier_binary(pk, yk),
                mu["reliability"],
                mu["resolution"],
                mu["uncertainty"],
                sc.auc(pk, yk),
            ]
        )
    tables.append(
        _table(
            "Por resultado (descomposición de Murphy)",
            [
                "Resultado",
                "n",
                "Prob. media",
                "Frecuencia real",
                "Brier",
                "Fiabilidad",
                "Resolución",
                "Incertidumbre",
                "AUC",
            ],
            ev_rows,
            "Brier ≈ fiabilidad − resolución + incertidumbre. Fiabilidad cerca de 0 = bien "
            "calibrado; resolución alta = distingue partidos. AUC 0.5 = azar, 1 = perfecto.",
        )
    )

    # 3. Componentes del ensemble sobre los mismos partidos
    names = sorted({k for r in rows for k in r["per_model"]})
    common = [r for r in rows if all(k in r["per_model"] for k in names)]
    mrows = []
    if common:
        yc = np.array([r["y"] for r in common])
        for name in [*names, "ensemble"]:
            pm = np.array([r["p"] if name == "ensemble" else r["per_model"][name] for r in common])
            b = _block_1x2(pm, yc, clim)
            mrows.append(
                [
                    name,
                    b["n"],
                    b["rps"],
                    sc.skill(b["rps"], b["rps_clim"]),
                    b["brier"],
                    b["logloss"],
                    b["fav"],
                ]
            )
    tables.append(
        _table(
            "Modelos (mismos partidos)",
            [
                "Modelo",
                "Partidos",
                "RPS",
                "RPSS vs climatología",
                "Brier multiclase",
                "Log-loss",
                "Acierto del favorito",
            ],
            mrows,
        )
    )

    # 4. Por competición
    by_comp: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_comp[r["competition"]].append(r)
    crows = []
    for comp, items in sorted(by_comp.items(), key=lambda kv: -len(kv[1])):
        pp = np.array([r["p"] for r in items])
        yy = np.array([r["y"] for r in items])
        b = _block_1x2(pp, yy, clim)
        crows.append(
            [
                comp,
                b["n"],
                float((yy == 0).mean()),
                float((yy == 1).mean()),
                float((yy == 2).mean()),
                float(pp[:, 0].mean()),
                float(np.mean([r["goals"] for r in items])),
                float(np.mean([r["xg"] for r in items])),
                b["rps"],
                sc.skill(b["rps"], b["rps_clim"]),
                b["logloss"],
                b["fav"],
            ]
        )
    tables.append(
        _table(
            "Por competición",
            [
                "Competición",
                "Partidos",
                "Local real",
                "Empate real",
                "Visitante real",
                "Local predicho",
                "Goles por partido",
                "Goles esperados",
                "RPS",
                "RPSS vs climatología",
                "Log-loss",
                "Acierto del favorito",
            ],
            crows,
            "RPSS usa la climatología global del periodo como referencia.",
        )
    )

    # 5. Por nivel de confianza
    by_conf: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_conf[r["confidence"]].append(i)
    frows = []
    for level in ("high", "medium", "low"):
        idx = by_conf.get(level)
        if not idx:
            continue
        b = _block_1x2(p[idx], y[idx], clim)
        frows.append(
            [
                CONF_LABEL[level],
                b["n"],
                b["n"] / n,
                float(p[idx].max(axis=1).mean()),
                b["fav"],
                b["rps"],
                sc.skill(b["rps"], b["rps_clim"]),
                b["logloss"],
            ]
        )
    tables.append(
        _table(
            "Por nivel de confianza",
            [
                "Confianza",
                "Partidos",
                "% del total",
                "Prob. media del favorito",
                "Acierto del favorito",
                "RPS",
                "RPSS vs climatología",
                "Log-loss",
            ],
            frows,
        )
    )

    # 6. Tendencia semanal
    by_week: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_week[_week_start(r["kickoff"])].append(i)
    wrows = []
    for wk in sorted(by_week):
        idx = by_week[wk]
        b = _block_1x2(p[idx], y[idx], clim)
        wrows.append([wk, b["n"], b["rps"], b["rps_clim"], b["logloss"], b["fav"]])
    tables.append(
        _table(
            "Tendencia semanal",
            [
                "Semana (lunes)",
                "Partidos",
                "RPS modelo",
                "RPS climatología",
                "Log-loss",
                "Acierto del favorito",
            ],
            wrows,
        )
    )

    # 7. Calibración 1X2 (eventos agrupados)
    tables.append(
        _table(
            "Calibración 1X2",
            ["Rango", "n", "Predicha", "Observada"],
            sc.calibration_table(pooled_p, pooled_y),
        )
    )

    # 8-11. Goles y córners
    tables += _totals_tables(rows, "goals", "goal_lines", "xg", "Goles")
    tables += _totals_tables(
        [r for r in rows if r["corners"] is not None and r["xc"] is not None],
        "corners",
        "corner_lines",
        "xc",
        "Córners",
    )
    return tables


def _totals_tables(
    rows: Sequence[dict], actual: str, lines_key: str, exp_key: str, label: str
) -> list[dict]:
    if not rows:
        return []
    act = np.array([r[actual] for r in rows], dtype=float)
    exp = np.array([r[exp_key] for r in rows], dtype=float)
    err = exp - act
    corr = float(np.corrcoef(exp, act)[0, 1]) if len(rows) > 2 else float("nan")
    reg = _table(
        f"{label}: total esperado vs real",
        [
            "Partidos",
            "Media real",
            "Media esperada",
            "Sesgo (esperado − real)",
            "MAE",
            "RMSE",
            "Correlación",
        ],
        [
            [
                len(rows),
                act.mean(),
                exp.mean(),
                err.mean(),
                np.abs(err).mean(),
                float(np.sqrt((err**2).mean())),
                corr,
            ]
        ],
    )
    lrows, cal_p, cal_y = [], [], []
    for line in sorted({ln for r in rows for ln in r[lines_key]}):
        pl = np.array([r[lines_key][line] for r in rows])
        yl = (act > line).astype(int)
        clim = yl.mean()
        b = sc.brier_binary(pl, yl)
        lrows.append(
            [
                f"Más de {line}",
                len(rows),
                pl.mean(),
                clim,
                b,
                sc.skill(b, clim * (1 - clim)),
                sc.log_loss_binary(pl, yl),
                sc.auc(pl, yl),
                sc.ece(pl, yl),
            ]
        )
        cal_p.append(pl)
        cal_y.append(yl)
    lines = _table(
        f"{label}: líneas",
        [
            "Línea",
            "Partidos",
            "Prob. media",
            "Frecuencia real",
            "Brier",
            "BSS vs climatología",
            "Log-loss",
            "AUC",
            "ECE",
        ],
        lrows,
    )
    cal = _table(
        f"{label}: calibración (todas las líneas)",
        ["Rango", "n", "Predicha", "Observada"],
        sc.calibration_table(np.concatenate(cal_p), np.concatenate(cal_y)),
    )
    return [reg, lines, cal]


def tennis_tables(rows: Sequence[dict]) -> list[dict]:
    if not rows:
        return []
    p = np.array([r["pa"] for r in rows])
    y = np.array([r["y"] for r in rows])
    # El favorito del modelo como evento: elimina la asimetría arbitraria de "jugador A"
    pf = np.maximum(p, 1 - p)
    yf = np.where(p >= 0.5, y, 1 - y)
    b = sc.brier_binary(p, y)
    mu = sc.murphy(pf, yf)
    tables = [
        _table(
            "Resumen tenis (ganador)",
            [
                "Partidos",
                "Brier",
                "BSS vs 50 %",
                "Log-loss",
                "Acierto del favorito",
                "Prob. media del favorito",
                "AUC",
                "ECE (favorito)",
                "Fiabilidad",
                "Resolución",
            ],
            [
                [
                    len(y),
                    b,
                    sc.skill(b, 0.25),
                    sc.log_loss_binary(p, y),
                    float(yf.mean()),
                    float(pf.mean()),
                    sc.auc(p, y),
                    sc.ece(pf, yf),
                    mu["reliability"],
                    mu["resolution"],
                ]
            ],
        )
    ]
    seg_rows = []
    for seg, key in (("Circuito", "tour"), ("Superficie", "surface"), ("Confianza", "confidence")):
        groups: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            groups[r[key]].append(i)
        for val, idx in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            label = CONF_LABEL.get(val, val) if key == "confidence" else val
            seg_rows.append(
                [
                    seg,
                    label,
                    len(idx),
                    float(yf[idx].mean()),
                    float(pf[idx].mean()),
                    sc.brier_binary(p[idx], y[idx]),
                    sc.log_loss_binary(p[idx], y[idx]),
                ]
            )
    tables.append(
        _table(
            "Tenis por segmento",
            [
                "Segmento",
                "Valor",
                "Partidos",
                "Acierto del favorito",
                "Prob. media del favorito",
                "Brier",
                "Log-loss",
            ],
            seg_rows,
        )
    )
    names = sorted({k for r in rows for k in r["per_model"]})
    common = [i for i, r in enumerate(rows) if all(k in r["per_model"] for k in names)]
    mrows = []
    for name in [*names, "ensemble"]:
        pm = np.array(
            [rows[i]["pa"] if name == "ensemble" else rows[i]["per_model"][name] for i in common]
        )
        yc = y[common]
        if len(yc):
            mrows.append(
                [
                    name,
                    len(yc),
                    sc.brier_binary(pm, yc),
                    sc.log_loss_binary(pm, yc),
                    float(((pm >= 0.5) == (yc == 1)).mean()),
                    sc.auc(pm, yc),
                ]
            )
    tables.append(
        _table(
            "Tenis: modelos (mismos partidos)",
            ["Modelo", "Partidos", "Brier", "Log-loss", "Acierto", "AUC"],
            mrows,
        )
    )
    tables.append(
        _table(
            "Tenis: calibración del favorito",
            ["Rango", "n", "Predicha", "Observada"],
            sc.calibration_table(pf, yf),
        )
    )
    g = [r for r in rows if r["games"] is not None and r["xgames"]]
    if g:
        act = np.array([r["games"] for r in g], dtype=float)
        exp = np.array([r["xgames"] for r in g], dtype=float)
        err = exp - act
        tables.append(
            _table(
                "Tenis: juegos esperados vs reales",
                ["Partidos", "Media real", "Media esperada", "Sesgo", "MAE", "RMSE"],
                [
                    [
                        len(g),
                        act.mean(),
                        exp.mean(),
                        err.mean(),
                        np.abs(err).mean(),
                        float(np.sqrt((err**2).mean())),
                    ]
                ],
            )
        )
    return tables
