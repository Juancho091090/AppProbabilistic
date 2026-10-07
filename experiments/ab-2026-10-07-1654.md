# Experimento A/B · fútbol · 2026-05-09 → 2026-10-06

- **actual**: configuración actual
- **fase_a**: {'football.league_effects.enabled': True, 'confidence.method': 'favorite_probability'}

## Global

| Métrica | actual | fase_a |
|---|---|---|
| Partidos | 2181 | 2181 |
| RPS (menor mejor) | 0.2083 | 0.2085 |
| RPSS vs climatología | 8.5% | 8.4% |
| Log-loss | 1.0169 | 1.0172 |
| Brier multiclase | 0.6091 | 0.6093 |
| Acierto del favorito | 49.1% | 49.2% |
| ECE | 0.0100 | 0.0136 |
| Local predicho | 45.9% | 46.1% |
| Local real | 44.0% | 44.0% |

## Por competición (RPS y sesgo de local)

| Competición | Partidos | RPS actual | RPS fase_a | Local pred. actual | Local pred. fase_a | Local real |
|---|---|---|---|---|---|---|
| UEFA Conference League | 236 | 0.2083 | 0.2085 | 46.3% | 47.2% | 44.9% |
| Liga Profesional Argentina | 179 | 0.2211 | 0.2213 | 44.4% | 43.4% | 44.7% |
| LigaPro Serie A | 150 | 0.2063 | 0.2065 | 44.2% | 43.9% | 45.3% |
| Brasileirão Serie A | 142 | 0.2026 | 0.2027 | 45.9% | 47.2% | 45.8% |
| Liga BetPlay (Primera A) | 131 | 0.1986 | 0.2005 | 45.2% | 47.5% | 39.7% |
| Liga 1 | 125 | 0.2067 | 0.2054 | 45.9% | 50.1% | 50.4% |
| LaLiga | 105 | 0.2078 | 0.2085 | 45.5% | 46.4% | 46.7% |
| UEFA Nations League | 104 | 0.2023 | 0.2023 | 46.7% | 43.6% | 37.5% |
| UEFA Champions League | 103 | 0.2064 | 0.2056 | 46.8% | 48.3% | 50.5% |
| Liga MX | 97 | 0.2349 | 0.2358 | 46.8% | 47.2% | 41.2% |
| UEFA Europa League | 92 | 0.2176 | 0.2183 | 45.3% | 45.7% | 48.9% |
| Eredivisie | 84 | 0.2010 | 0.1973 | 47.7% | 46.3% | 34.5% |
| FIFA World Cup | 82 | 0.2030 | 0.2094 | 49.2% | 52.5% | 42.7% |
| Premier League | 79 | 0.1990 | 0.1983 | 45.6% | 43.3% | 36.7% |
| Primeira Liga | 79 | 0.1933 | 0.1926 | 45.6% | 44.2% | 40.5% |
| Serie A | 78 | 0.2111 | 0.2060 | 44.8% | 40.4% | 38.5% |
| Copa Sudamericana | 72 | 0.1950 | 0.1950 | 46.7% | 46.9% | 50.0% |
| 2. Bundesliga | 69 | 0.2321 | 0.2318 | 46.2% | 44.9% | 42.0% |
| Ligue 1 | 63 | 0.2246 | 0.2249 | 43.9% | 41.8% | 41.3% |
| Copa Libertadores | 56 | 0.1996 | 0.2012 | 47.5% | 50.3% | 46.4% |
| Bundesliga | 55 | 0.1965 | 0.1992 | 46.4% | 44.5% | 50.9% |

## Confianza · actual

| Nivel | Partidos | % | Acierto del favorito | RPS |
|---|---|---|---|---|
| Alta | 1652 | 75.7% | 48.1% | 0.2090 |
| Media | 319 | 14.6% | 53.6% | 0.2032 |
| Baja | 210 | 9.6% | 50.0% | 0.2107 |

## Confianza · fase_a

| Nivel | Partidos | % | Acierto del favorito | RPS |
|---|---|---|---|---|
| Alta | 282 | 12.9% | 74.5% | 0.1398 |
| Media | 888 | 40.7% | 48.6% | 0.2103 |
| Baja | 1011 | 46.4% | 42.5% | 0.2261 |
