# Modelos estadísticos

Todos los modelos se ajustan con partidos **estrictamente anteriores** a `as_of` y
leen sus parámetros de `src/sports_analytics/config/models.yaml`.

## Recencia (`features/recency.py`)

* `exponential` (por defecto): `w = 0.5 ** (días / half_life)`; vida media 180 días en
  fútbol y 120 en tenis. Hay un `min_weight` para que el histórico antiguo no se anule.
* `buckets`: últimos 5 → 1.00, 10 → 0.75, 20 → 0.50, resto → 0.25.

## Fútbol

| Modelo | Archivo | Qué estima |
|---|---|---|
| Elo (World Football Elo) | `models/football/elo.py` | Rating por equipo; 1X2 con P(empate) decreciente en \|ΔElo\| y P(L)+½P(E) = We |
| Poisson | `models/football/poisson.py` | `λ_local = μ_local·att_local·def_visit`, con fuerza del rival iterativa, recencia y shrinkage |
| Dixon-Coles | `models/football/dixon_coles.py` | Corrige 0-0, 1-0, 0-1 y 1-1 con τ(ρ); ρ por MLE ponderado (≥150 partidos), si no −0.10 |
| Córners | `models/football/corners.py` | Media multiplicativa (genera/concede + localía + rival); Binomial Negativa si var/media > 1.10 |
| Logística multinomial | `models/logistic.py` + `features/football.py` | ΔElo, goles a favor/en contra ponderados, forma (puntos) |

Los mercados de goles (esperados, distribución, líneas, ambos marcan, marcadores
más probables) salen de la matriz Dixon-Coles. El 1X2 final es el ensemble.

**Validación incluida en los tests**

* El modelo Poisson recupera el orden de fuerza ofensiva en datos simulados (correlación > 0.8).
* Dixon-Coles recupera ρ = −0.10 en una simulación con 2.280 partidos.
* El estimador de dispersión recupera el tamaño r de una Binomial Negativa (±15%).

## Tenis

| Modelo | Archivo | Qué estima |
|---|---|---|
| Elo general | `models/tennis/elo.py` | K dinámico `250/(n+5)^0.4`; los retiros no cuentan |
| Elo de superficie | ídem | Ratings hard/clay/grass, mezclados con el general según `n/(n+15)` |
| Logística | `models/logistic.py` + `features/tennis.py` | ΔElo, ΔElo de superficie, log(rank_B/rank_A), forma, ΔSPW, ΔRPW |
| Markov exacto | `models/tennis/markov.py` | Punto → juego → tiebreak → set → partido; distribución exacta de sets y juegos |

* **Barnett-Clarke**: `p_saque_A = SPW_A − (RPW_B − RPW_medio_circuito)`. Se usa solo si
  ambos jugadores tienen al menos 5 partidos con estadísticas.
* **Coherencia**: después del ensemble se buscan las probabilidades de punto al saque
  que reproducen exactamente la P(ganador) final. De ahí salen "al menos un set" y los
  juegos, así que las tres salidas no se contradicen.
* Grand Slams: al mejor de 5 (ATP) y tiebreak a 10 puntos en el set decisivo.
* **Validación**: el modelo exacto coincide con un Monte Carlo independiente, y reproduce
  el valor de referencia de la hoja "Apuestas deportivas" (Over 21.5 = 42.1% con
  SPW 56.8% contra 47.8%).

## Ensemble (`models/ensemble.py`)

`P_final = Σ wᵢ·Pᵢ`. Los pesos iniciales están en YAML. Si falta un modelo, su peso se
reparte entre los demás. `fit_weights` reestima los pesos minimizando log-loss sobre
predicciones ya resueltas y rechaza cualquier evento con fecha ≥ `as_of`.

## Calibración (`models/calibration.py`)

* Métricas: Brier, Log Loss, Accuracy, curva de calibración y ECE (top-label para 1X2).
* Calibradores: identidad con menos de 200 muestras, Platt entre 200 y 1.000, isotónica
  con 1.000 o más. Se ajusta una clase contra el resto y se renormaliza. Las filas
  posteriores a `as_of` se descartan.

### Recalibración multinomial del 1X2 (`MultinomialRecalibrator`)

El backtest de producción mostró que el ensemble es demasiado conservador (favoritos
claros por debajo de su frecuencia real) y sobreestima al local. Tras el ensemble y
antes del calibrador vivo se aplica

    q_k ∝ p_k^a · exp(b_k),   b_empate = 0

`a` > 1 separa las probabilidades; `b_local` y `b_visitante` corrigen sesgos. Se ajusta
cada lunes (`fit-recalibration`, workflow `backtest`) minimizando log-loss sobre las
predicciones walk-forward de los últimos `window_days` días, con penalización L2 hacia
la identidad. Antes de aplicarla se valida fuera de muestra: se ajusta con el primer
70 % cronológico y se mide en el 30 % final; **solo se aplica si mejora la log-loss**.
Los parámetros caducan a los `max_age_days`. Las líneas de goles (Dixon-Coles) no se
modifican.

Primer ajuste (7-oct-2026, 2.181 partidos del 9-may al 6-oct): a = 1.209,
b_local = −0.215, b_visitante = −0.042. Fuera de muestra (654 partidos): log-loss
1.0020 → 0.9976, Brier 0.5993 → 0.5962, probabilidad media del local 45.9 % → 44.6 %
(real 42.5 %). Persiste que los favoritos de más del 60 % ganan más de lo predicho:
una sola potencia no lo corrige del todo (ver *Pendientes*).

## Confianza (`models/confidence.py`)

`score = 0.5·calidad_de_datos + 0.5·acuerdo_entre_modelos` → Alta (≥ 0.70),
Media (≥ 0.45), Baja. **No es una probabilidad.**

## Benchmark de mercado

Módulos `market/odds.py` y `market/benchmark.py`. **Solo evaluación**: mide si el modelo
predice mejor o peor que el mercado; nunca entra en el informe ni genera recomendaciones.

1. **Datos.** `/odds?fixture=&bet=1` de API-Football (mercado *Match Winner*). Se guardan
   los precios de todas las casas en `market_odds`. Los partidos de hoy se consultan antes
   del inicio; los ya iniciados de los últimos `MARKET_ODDS_BACKFILL_DAYS` días se
   reconsultan una vez (`market_odds_checks.final`) para quedarse con el último precio
   previo al inicio. Presupuesto: `FOOTBALL_ODDS_CALLS_PER_RUN`.
2. **Margen.** Probabilidad implícita `1/precio`; se elimina el margen con el método
   proporcional: `p_i = (1/o_i) / Σ_j (1/o_j)`.
3. **Referencia** (`models.yaml → market`): la primera casa disponible de
   `preferred_bookmakers` (por defecto Pinnacle, de margen bajo); si no está, el
   consenso (media de las probabilidades justas de todas las casas, renormalizada).
4. **Comparación** sobre los mismos partidos: Brier multiclase `Σ_k (p_k − y_k)²`,
   log-loss `−ln p_resultado`, acierto del favorito y diferencia de log-loss
   (modelo − mercado) con IC 95 % por bootstrap pareado. Si el intervalo incluye 0, la
   muestra no permite decir cuál es mejor.
5. **Dónde aparece:** sección *Modelo vs mercado* del backtest; `model_metrics` con los
   modelos `mercado` y `ensemble_v1@mercado` (el modelo final limitado a los mismos
   partidos); comando `market-benchmark`.

Advertencia de interpretación: en el informe diario el modelo predice a las 06:37 y el
mercado se toma con el último precio previo al inicio, que ya incorpora alineaciones y
noticias. La comparación es por tanto exigente con el modelo.

## Pendientes conocidos

* 1X2: los favoritos con más del 60 % siguen por debajo de su frecuencia real tras la
  recalibración. Origen probable en los componentes (shrinkage de Poisson, K y ventaja
  de local del Elo, peso de la recencia); revisar con backtests por componente.

* Córners: regresión Binomial Negativa con tiros y posesión previos al partido como
  covariables. Requiere estadísticas reales por partido de API-Football.
* Tenis: estadísticas de saque/resto separadas por superficie (`stats_shrink_prior`
  ya está en la configuración). Depende de la API de tenis elegida.
* Torneos internacionales de clubes: las fuerzas Poisson de equipos de ligas distintas
  no son del todo comparables. El Elo se ajusta con todas las competiciones a la vez y
  ayuda a corregirlo.
