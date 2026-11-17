"""Historial de cuotas de cierre (football-data.co.uk) para probar patrones de mercado.

Solo investigación: no genera recomendaciones. Pregunta central: ¿el favorito gana más de
lo que dice el precio de cierre (sesgo favorito–longshot) de forma estable por temporada y
por liga, y sobrevive al margen de la casa?

Precios usados (todos de CIERRE, el último antes del inicio):
* ``ps``: Pinnacle (referencia del precio justo; margen ~2-3 %).
* ``avg``: media del mercado (solo desde 2019/20 en las ligas principales).
* ``max``: mejor precio del mercado (idem).

Probabilidad justa = cierre de Pinnacle sin margen. Se comparan dos formas de quitar el
margen, porque la proporcional reparte el margen por igual y por construcción infravalora
al favorito: una "ventaja" que solo aparece con ella es un artefacto del método.
* proporcional: p_i = (1/o_i) / Σ(1/o_j)
* potencia: p_i = (1/o_i)^k con k tal que Σ p_i = 1 (carga más margen en el longshot).
"""

from __future__ import annotations

import io
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

BASE_URL = "https://www.football-data.co.uk"

# Ligas principales: un CSV por temporada en /mmz4281/{AABB}/{código}.csv
MAIN_LEAGUES = {
    "E0": "Inglaterra · Premier League",
    "E1": "Inglaterra · Championship",
    "SC0": "Escocia · Premiership",
    "D1": "Alemania · Bundesliga",
    "D2": "Alemania · 2. Bundesliga",
    "I1": "Italia · Serie A",
    "I2": "Italia · Serie B",
    "SP1": "España · LaLiga",
    "SP2": "España · Segunda",
    "F1": "Francia · Ligue 1",
    "F2": "Francia · Ligue 2",
    "N1": "Países Bajos · Eredivisie",
    "B1": "Bélgica · Pro League",
    "P1": "Portugal · Primeira Liga",
    "T1": "Turquía · Süper Lig",
    "G1": "Grecia · Super League",
}
# Ligas "extra": un CSV con todas las temporadas en /new/{código}.csv (solo 1X2)
EXTRA_LEAGUES = {
    "ARG": "Argentina · Liga Profesional",
    "BRA": "Brasil · Serie A",
    "MEX": "México · Liga MX",
    "USA": "EE. UU. · MLS",
}

OUTCOMES = ("Local", "Empate", "Visitante")
SOURCES = {
    "ps": "Pinnacle cierre",
    "avg": "Media del mercado al cierre",
    "max": "Mejor precio al cierre",
}


def season_codes(first: int, last: int) -> list[str]:
    """2012, 2025 -> ["1213", ..., "2526"] (año de inicio de cada temporada)."""
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(first, last + 1)]


# ------------------------------------------------------------------ lectura de CSV

_MAIN_COLS = {
    "HomeTeam": "home",
    "AwayTeam": "away",
    "FTHG": "hg",
    "FTAG": "ag",
    "PSCH": "ps_h",
    "PSCD": "ps_d",
    "PSCA": "ps_a",
    "AvgCH": "avg_h",
    "AvgCD": "avg_d",
    "AvgCA": "avg_a",
    "MaxCH": "max_h",
    "MaxCD": "max_d",
    "MaxCA": "max_a",
    "PC>2.5": "ps_over",
    "PC<2.5": "ps_under",
    "AvgC>2.5": "avg_over",
    "AvgC<2.5": "avg_under",
    "MaxC>2.5": "max_over",
    "MaxC<2.5": "max_under",
}
_EXTRA_COLS = {
    "Home": "home",
    "Away": "away",
    "HG": "hg",
    "AG": "ag",
    "PSCH": "ps_h",
    "PSCD": "ps_d",
    "PSCA": "ps_a",
    "AvgCH": "avg_h",
    "AvgCD": "avg_d",
    "AvgCA": "avg_a",
    "MaxCH": "max_h",
    "MaxCD": "max_d",
    "MaxCA": "max_a",
}
PRICE_COLS = [
    c for c in dict.fromkeys(_MAIN_COLS.values()) if c not in ("home", "away", "hg", "ag")
]


def _read_csv(text: str) -> pd.DataFrame:
    # Algunos ficheros traen columnas vacías al final o filas en blanco.
    df = pd.read_csv(io.StringIO(text), on_bad_lines="skip", low_memory=False)
    return df.dropna(axis=1, how="all")


def _normalize(df: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    out = df.rename(columns={k: v for k, v in cols.items() if k in df.columns})
    for c in ("home", "away", "hg", "ag", "date", *PRICE_COLS):
        if c not in out.columns:
            out[c] = np.nan
    for c in ("hg", "ag", *PRICE_COLS):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["hg", "ag"])
    return out


def parse_main_csv(text: str, league: str, season: str) -> pd.DataFrame:
    df = _normalize(_read_csv(text).rename(columns={"Date": "date"}), _MAIN_COLS)
    df["league"] = MAIN_LEAGUES.get(league, league)
    df["season"] = f"20{season[:2]}/{season[2:]}"
    return df[["league", "season", "date", "home", "away", "hg", "ag", *PRICE_COLS]]


def parse_extra_csv(text: str, league: str) -> pd.DataFrame:
    raw = _read_csv(text).rename(columns={"Date": "date"})
    df = _normalize(raw, _EXTRA_COLS)
    season = raw.get("Season", pd.Series([""] * len(raw))).astype(str)
    # "2012/2013" -> "2012/13"; "2012" -> "2012"
    df["season"] = season.str.replace(r"^(\d{4})/\d{2}(\d{2})$", r"\1/\2", regex=True).loc[df.index]
    df["league"] = EXTRA_LEAGUES.get(league, league)
    return df[["league", "season", "date", "home", "away", "hg", "ag", *PRICE_COLS]]


def download_history(
    get: Callable[[str], str | None],
    seasons: Iterable[str],
    main: Iterable[str] = MAIN_LEAGUES,
    extra: Iterable[str] = EXTRA_LEAGUES,
    pause: float = 0.3,
) -> tuple[pd.DataFrame, list[str]]:
    """``get(url)`` devuelve el texto del CSV o None si no existe. Devuelve los partidos y
    la lista de ficheros que faltaron (temporadas en que una liga no está publicada)."""
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for season in seasons:
        for code in main:
            url = f"{BASE_URL}/mmz4281/{season}/{code}.csv"
            text = get(url)
            if text:
                frames.append(parse_main_csv(text, code, season))
            else:
                missing.append(url)
            time.sleep(pause)
    for code in extra:
        url = f"{BASE_URL}/new/{code}.csv"
        text = get(url)
        if text:
            frames.append(parse_extra_csv(text, code))
        else:
            missing.append(url)
        time.sleep(pause)
    if not frames:
        return pd.DataFrame(columns=["league", "season", "hg", "ag", *PRICE_COLS]), missing
    return pd.concat(frames, ignore_index=True), missing


# --------------------------------------------------------------- probabilidades


def devig_matrix(odds: np.ndarray, method: str = "proportional") -> np.ndarray:
    """Filas de precios decimales -> probabilidades sin margen (NaN si falta un precio)."""
    odds = np.asarray(odds, dtype=float)
    inv = 1.0 / odds
    bad = ~np.isfinite(inv).all(axis=1) | (odds <= 1.0).any(axis=1)
    inv = np.where(bad[:, None], np.nan, inv)
    if method == "proportional":
        return inv / inv.sum(axis=1, keepdims=True)
    if method == "power":
        lo = np.ones(len(inv))
        hi = np.full(len(inv), 5.0)
        for _ in range(60):  # bisección: Σ inv^k = 1 (k > 1 si hay margen)
            k = (lo + hi) / 2
            s = np.nansum(inv ** k[:, None], axis=1)
            lo = np.where(s > 1, k, lo)
            hi = np.where(s > 1, hi, k)
        p = inv ** ((lo + hi) / 2)[:, None]
        return p / p.sum(axis=1, keepdims=True)
    raise ValueError(f"Método desconocido: {method}")


@dataclass
class Prepared:
    """Matrices alineadas por partido para 1X2 y más/menos de 2.5."""

    df: pd.DataFrame
    outcome: np.ndarray  # 0 local, 1 empate, 2 visitante
    over: np.ndarray  # 1 si hubo más de 2.5 goles
    fair: dict[str, np.ndarray]  # método -> probabilidades 1X2 (cierre Pinnacle)
    fair_ou: dict[str, np.ndarray]  # método -> [P(más), P(menos)]
    prices: dict[str, np.ndarray]  # fuente -> precios 1X2
    prices_ou: dict[str, np.ndarray]


def prepare(df: pd.DataFrame) -> Prepared:
    df = df[df[["ps_h", "ps_d", "ps_a"]].notna().all(axis=1)].reset_index(drop=True)
    outcome = np.where(df.hg > df.ag, 0, np.where(df.hg == df.ag, 1, 2))
    over = ((df.hg + df.ag) > 2.5).astype(int).to_numpy()
    ps = df[["ps_h", "ps_d", "ps_a"]].to_numpy(float)
    ps_ou = df[["ps_over", "ps_under"]].to_numpy(float)
    return Prepared(
        df=df,
        outcome=outcome,
        over=over,
        fair={m: devig_matrix(ps, m) for m in ("proportional", "power")},
        fair_ou={m: devig_matrix(ps_ou, m) for m in ("proportional", "power")},
        prices={s: df[[f"{s}_h", f"{s}_d", f"{s}_a"]].to_numpy(float) for s in SOURCES},
        prices_ou={s: df[[f"{s}_over", f"{s}_under"]].to_numpy(float) for s in SOURCES},
    )


# ------------------------------------------------------------------- reglas


# Una regla devuelve, por partido, el índice elegido (o -1 si no elige) sobre el mercado
# indicado. Se definen sobre la probabilidad de Pinnacle sin margen (proporcional), igual
# que el registro simulado.
@dataclass(frozen=True)
class HistRule:
    label: str
    market: str  # "1x2" | "ou25"
    pick: Callable[[np.ndarray], np.ndarray]


def _fav(min_p: float = 0.0, max_p: float = 1.01) -> Callable[[np.ndarray], np.ndarray]:
    def f(p: np.ndarray) -> np.ndarray:
        i = np.nanargmax(np.nan_to_num(p, nan=-1), axis=1)
        m = p[np.arange(len(p)), i]
        return np.where((m >= min_p) & (m < max_p), i, -1)

    return f


def _fixed(idx: int, min_p: float = 0.0) -> Callable[[np.ndarray], np.ndarray]:
    return lambda p: np.where(p[:, idx] >= min_p, idx, -1)


HIST_RULES: dict[str, HistRule] = {
    "fav_all": HistRule("Favorito del mercado (todos)", "1x2", _fav()),
    "fav_45_55": HistRule("Favorito entre 45 % y 55 %", "1x2", _fav(0.45, 0.55)),
    "fav_55": HistRule("Favorito con 55 % o más", "1x2", _fav(0.55)),
    "fav_60": HistRule("Favorito con 60 % o más", "1x2", _fav(0.60)),
    "fav_70": HistRule("Favorito con 70 % o más", "1x2", _fav(0.70)),
    "home": HistRule("Local (todos)", "1x2", _fixed(0)),
    "draw": HistRule("Empate (todos)", "1x2", _fixed(1)),
    "away": HistRule("Visitante (todos)", "1x2", _fixed(2)),
    "over": HistRule("Más de 2.5 goles (todos)", "ou25", _fixed(0)),
    "under": HistRule("Menos de 2.5 goles (todos)", "ou25", _fixed(1)),
    "over_60": HistRule("Más de 2.5 con 60 % o más", "ou25", _fixed(0, 0.60)),
    "under_60": HistRule("Menos de 2.5 con 60 % o más", "ou25", _fixed(1, 0.60)),
}


def _selected(prep: Prepared, rule: HistRule, mask: np.ndarray | None = None):
    fair = prep.fair["proportional"] if rule.market == "1x2" else prep.fair_ou["proportional"]
    pick = rule.pick(fair)
    ok = pick >= 0
    if mask is not None:
        ok &= mask
    rows = np.where(ok)[0]
    return rows, pick[rows]


def rule_stats(
    prep: Prepared, rule: HistRule, mask: np.ndarray | None = None, source: str = "ps"
) -> dict | None:
    rows, cols = _selected(prep, rule, mask)
    if rule.market == "1x2":
        hit = prep.outcome[rows] == cols
        prices = prep.prices[source][rows, cols]
        fair_p = prep.fair["proportional"][rows, cols]
        fair_w = prep.fair["power"][rows, cols]
    else:
        hit = (prep.over[rows] == 1) == (cols == 0)
        prices = prep.prices_ou[source][rows, cols]
        fair_p = prep.fair_ou["proportional"][rows, cols]
        fair_w = prep.fair_ou["power"][rows, cols]
    ok = np.isfinite(prices) & np.isfinite(fair_p)
    hit, prices, fair_p, fair_w = hit[ok], prices[ok], fair_p[ok], fair_w[ok]
    n = int(len(hit))
    if n == 0:
        return None
    ret = np.where(hit, prices - 1.0, -1.0)
    sd = float(ret.std(ddof=1)) if n > 1 else 0.0
    hr = float(hit.mean())
    fp, fw = float(fair_p.mean()), float(np.nanmean(fair_w))
    se_hit = math.sqrt(max(fp * (1 - fp), 1e-9) / n)
    return {
        "n": n,
        "hit_rate": hr,
        "fair_prop": fp,
        "fair_power": fw,
        "edge_prop_pp": hr - fp,
        "edge_power_pp": hr - fw,
        "z_prop": (hr - fp) / se_hit,
        "z_power": (hr - fw) / math.sqrt(max(fw * (1 - fw), 1e-9) / n),
        "avg_price": float(prices.mean()),
        "roi": float(ret.mean()),
        "roi_lo": float(ret.mean() - 1.96 * sd / math.sqrt(n)),
        "roi_hi": float(ret.mean() + 1.96 * sd / math.sqrt(n)),
    }


def calibration(prep: Prepared, method: str) -> list[dict]:
    """Todas las opciones del 1X2 por rango de probabilidad justa: predicho vs ocurrido."""
    edges = [0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.0]
    p = prep.fair[method].ravel()
    y = (prep.outcome[:, None] == np.arange(3)[None, :]).ravel().astype(float)
    ok = np.isfinite(p)
    p, y = p[ok], y[ok]
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & (p < hi)
        n = int(m.sum())
        if n:
            pred, obs = float(p[m].mean()), float(y[m].mean())
            rows.append(
                {
                    "bin": f"{lo:.0%}–{hi:.0%}",
                    "n": n,
                    "pred": pred,
                    "obs": obs,
                    "z": (obs - pred) / math.sqrt(max(pred * (1 - pred), 1e-9) / n),
                }
            )
    return rows


def breakdown(prep: Prepared, rule_key: str, by: str, source: str = "ps") -> list[dict]:
    """Estadísticas de una regla por liga o por temporada."""
    rule = HIST_RULES[rule_key]
    out = []
    for value in sorted(prep.df[by].dropna().unique()):
        st = rule_stats(prep, rule, (prep.df[by] == value).to_numpy(), source)
        if st:
            out.append({by: value, **st})
    return out


# ------------------------------------------------------------------ informe


def analyze(df: pd.DataFrame, recent_from: str = "2022/23") -> dict:
    prep = prepare(df)
    seasons = sorted(prep.df.season.unique())
    recent = (prep.df.season >= recent_from).to_numpy()
    result: dict = {
        "matches": int(len(prep.df)),
        "matches_raw": int(len(df)),
        "leagues": sorted(prep.df.league.unique()),
        "seasons": [seasons[0], seasons[-1]] if seasons else [],
        "recent_from": recent_from,
        "rules": [],
        "calibration": {m: calibration(prep, m) for m in ("proportional", "power")},
        "by_season": {k: breakdown(prep, k, "season") for k in ("fav_all", "fav_60")},
        "by_league": {
            k: breakdown(prep, k, "league") for k in ("fav_all", "fav_60", "under", "over")
        },
        "by_league_recent": {},
    }
    for key, rule in HIST_RULES.items():
        row = {"key": key, "label": rule.label}
        for src in SOURCES:
            row[src] = rule_stats(prep, rule, source=src)
            row[f"{src}_recent"] = rule_stats(prep, rule, recent, source=src)
        result["rules"].append(row)
    sub = Prepared(
        prep.df[recent].reset_index(drop=True),
        prep.outcome[recent],
        prep.over[recent],
        {k: v[recent] for k, v in prep.fair.items()},
        {k: v[recent] for k, v in prep.fair_ou.items()},
        {k: v[recent] for k, v in prep.prices.items()},
        {k: v[recent] for k, v in prep.prices_ou.items()},
    )
    result["by_league_recent"] = {k: breakdown(sub, k, "league") for k in ("fav_all", "fav_60")}
    return result


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _pp(x: float) -> str:
    return f"{x * 100:+.1f}"


def to_markdown(res: dict) -> str:
    n_tests = len(HIST_RULES) * len(SOURCES)
    lines = [
        "# Historial de cuotas de cierre: ¿hay un patrón estable? (investigación)",
        "",
        f"- Partidos con cierre de Pinnacle: {res['matches']:,} de {res['matches_raw']:,}"
        f" · temporadas {res['seasons'][0]} a {res['seasons'][-1]} · {len(res['leagues'])} ligas",
        "- Probabilidad justa = cierre de Pinnacle sin margen. «Ventaja» = % acertado − % justo"
        " (puntos porcentuales). z > 3 en valor absoluto es evidencia fuerte"
        f" (se prueban {n_tests} combinaciones a la vez).",
        "- Rendimiento = ganancia media por unidad al precio indicado. La media y el mejor precio"
        " del mercado solo existen desde 2019/20.",
        "",
        "## Reglas (todas las temporadas)",
        "",
        "| Regla | Partidos | Acierto | Justo (prop.) | Ventaja prop. | z | Justo (potencia) |"
        " Ventaja pot. | z | Rend. Pinnacle | Rend. media | Rend. mejor precio |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["rules"]:
        ps = r["ps"]
        if not ps:
            continue
        lines.append(
            f"| {r['label']} | {ps['n']:,} | {_pct(ps['hit_rate'])} | {_pct(ps['fair_prop'])} | "
            f"{_pp(ps['edge_prop_pp'])} | {ps['z_prop']:+.1f} | {_pct(ps['fair_power'])} | "
            f"{_pp(ps['edge_power_pp'])} | {ps['z_power']:+.1f} | {_pct(ps['roi'], True)} "
            f"[{_pct(ps['roi_lo'], True)}, {_pct(ps['roi_hi'], True)}] | "
            f"{_pct((r['avg'] or {}).get('roi'), True)} | {_pct((r['max'] or {}).get('roi'), True)} |"
        )
    lines += [
        "",
        f"## Reglas desde {res['recent_from']} (mercado actual)",
        "",
        "| Regla | Partidos | Acierto | Ventaja prop. | z | Ventaja pot. | z |"
        " Rend. Pinnacle | Rend. mejor precio |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res["rules"]:
        ps = r["ps_recent"]
        if not ps:
            continue
        lines.append(
            f"| {r['label']} | {ps['n']:,} | {_pct(ps['hit_rate'])} | {_pp(ps['edge_prop_pp'])} | "
            f"{ps['z_prop']:+.1f} | {_pp(ps['edge_power_pp'])} | {ps['z_power']:+.1f} | "
            f"{_pct(ps['roi'], True)} | {_pct((r['max_recent'] or {}).get('roi'), True)} |"
        )
    for method, title in (("proportional", "proporcional"), ("power", "potencia")):
        lines += [
            "",
            f"## Calibración del cierre de Pinnacle (margen quitado: {title})",
            "",
            "| Rango | Opciones | Precio decía | Ocurrió | z |",
            "|---|---|---|---|---|",
        ]
        for c in res["calibration"][method]:
            lines.append(
                f"| {c['bin']} | {c['n']:,} | {_pct(c['pred'])} | {_pct(c['obs'])} | {c['z']:+.1f} |"
            )
    for key, title in (("fav_all", "Favorito (todos)"), ("fav_60", "Favorito con 60 % o más")):
        lines += [
            "",
            f"## {title}: por temporada (precio Pinnacle de cierre)",
            "",
            "| Temporada | Partidos | Acierto | Ventaja prop. | Ventaja pot. | z pot. | Rendimiento |",
            "|---|---|---|---|---|---|---|",
        ]
        for b in res["by_season"][key]:
            lines.append(
                f"| {b['season']} | {b['n']:,} | {_pct(b['hit_rate'])} | {_pp(b['edge_prop_pp'])} | "
                f"{_pp(b['edge_power_pp'])} | {b['z_power']:+.1f} | {_pct(b['roi'], True)} |"
            )
    for key, title in (
        ("fav_all", "Favorito (todos)"),
        ("fav_60", "Favorito con 60 % o más"),
        ("under", "Menos de 2.5 goles"),
        ("over", "Más de 2.5 goles"),
    ):
        lines += [
            "",
            f"## {title}: por liga (todas las temporadas, precio Pinnacle de cierre)",
            "",
            "| Liga | Partidos | Acierto | Ventaja pot. | z pot. | Rendimiento | IC 95 % |",
            "|---|---|---|---|---|---|---|",
        ]
        for b in sorted(res["by_league"][key], key=lambda x: -x["roi"]):
            lines.append(
                f"| {b['league']} | {b['n']:,} | {_pct(b['hit_rate'])} | {_pp(b['edge_power_pp'])} | "
                f"{b['z_power']:+.1f} | {_pct(b['roi'], True)} | "
                f"[{_pct(b['roi_lo'], True)}, {_pct(b['roi_hi'], True)}] |"
            )
    lines += [
        "",
        "Lectura: con el margen quitado de forma proporcional el favorito parece infravalorado "
        "por construcción; la columna «potencia» y el rendimiento a precio real son las que "
        "cuentan. Un patrón solo es candidato si es positivo a precio real, estable por "
        "temporada y se repite en los datos nuevos del registro simulado.",
        "",
    ]
    return "\n".join(lines)
