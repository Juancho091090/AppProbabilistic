"""Generadores de datos sintéticos con parámetros conocidos (para validar modelos)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from sports_analytics.data.schemas import FootballMatch, MatchStatus, TennisMatch

START = datetime(2024, 1, 6, 18, 0, tzinfo=UTC)


def football_league(
    n_teams: int = 12,
    rounds: int = 4,
    seed: int = 7,
    mu_home: float = 1.5,
    mu_away: float = 1.1,
    corners_nb_r: float | None = 8.0,
    start: datetime = START,
) -> tuple[list[FootballMatch], dict[str, tuple[float, float]]]:
    """Liga doble vuelta repetida ``rounds`` veces. Devuelve partidos y (att, def) reales."""
    rng = np.random.default_rng(seed)
    teams = [f"Team{i:02d}" for i in range(n_teams)]
    att = np.exp(rng.normal(0, 0.25, n_teams))
    dfn = np.exp(rng.normal(0, 0.25, n_teams))
    att /= np.exp(np.log(att).mean())
    dfn /= np.exp(np.log(dfn).mean())
    matches = []
    day = 0
    for _ in range(rounds):
        for h in range(n_teams):
            for a in range(n_teams):
                if h == a:
                    continue
                lam = mu_home * att[h] * dfn[a]
                mu = mu_away * att[a] * dfn[h]
                c_mean = 5.0 * att[h] / dfn[a] ** 0.2 + 4.3 * att[a] / dfn[h] ** 0.2
                if corners_nb_r:
                    p = corners_nb_r / (corners_nb_r + c_mean)
                    total_c = rng.negative_binomial(corners_nb_r, p)
                else:
                    total_c = rng.poisson(c_mean)
                hc = rng.binomial(total_c, 0.55)
                matches.append(
                    FootballMatch(
                        match_id=f"m{len(matches)}",
                        competition_key="test_league",
                        home_team=teams[h],
                        away_team=teams[a],
                        kickoff_utc=start + timedelta(days=day // 6, hours=day % 6),
                        status=MatchStatus.FINISHED,
                        home_goals=int(rng.poisson(lam)),
                        away_goals=int(rng.poisson(mu)),
                        home_corners=int(hc),
                        away_corners=int(total_c - hc),
                    )
                )
                day += 1
    truth = {t: (float(att[i]), float(dfn[i])) for i, t in enumerate(teams)}
    return matches, truth


def tennis_tour(
    n_players: int = 30, n_matches: int = 3000, seed: int = 11, start: datetime = START
) -> tuple[list[TennisMatch], dict[str, float]]:
    """Partidos con P(A gana) = logística de la diferencia de habilidad latente."""
    rng = np.random.default_rng(seed)
    players = [f"P{i:02d}" for i in range(n_players)]
    skill = rng.normal(0, 1.0, n_players)
    surfaces = ["hard", "clay", "grass"]
    out = []
    for k in range(n_matches):
        a, b = rng.choice(n_players, 2, replace=False)
        surface = surfaces[rng.choice(3, p=[0.55, 0.35, 0.10])]
        bonus = 0.3 if (surface == "clay" and a % 3 == 0) else 0.0
        p_a = 1 / (1 + np.exp(-(skill[a] + bonus - skill[b])))
        a_wins = rng.random() < p_a
        base = 0.62
        spw_a = float(np.clip(base + 0.03 * (skill[a] - skill[b]) + rng.normal(0, 0.03), 0.4, 0.85))
        spw_b = float(np.clip(base + 0.03 * (skill[b] - skill[a]) + rng.normal(0, 0.03), 0.4, 0.85))
        out.append(
            TennisMatch(
                match_id=f"t{k}",
                tour="ATP",
                tournament="Synthetic Open",
                category="ATP 250",
                surface=surface,
                player_a=players[a],
                player_b=players[b],
                kickoff_utc=start + timedelta(hours=6 * k),
                status=MatchStatus.FINISHED,
                winner="A" if a_wins else "B",
                a_spw=spw_a,
                b_spw=spw_b,
                a_rpw=1 - spw_b,
                b_rpw=1 - spw_a,
            )
        )
    return out, {p: float(s) for p, s in zip(players, skill, strict=True)}
