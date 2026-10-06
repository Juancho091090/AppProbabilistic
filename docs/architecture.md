# Arquitectura

## 1. Principio rector

Las probabilidades **solo** las calcula Python. Claude recibe un JSON ya calculado y
produce texto. Ningún número del informe proviene del LLM: el informe se construye
de forma determinista a partir del JSON y Claude solo aporta párrafos explicativos,
que además se validan (ver §6).

```
 APIs (API-Football, API tenis)
        │  httpx + caché + retry/backoff + rate limit
        ▼
 ┌──────────────────┐
 │ data/            │  clientes, normalización, filtros de competición
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ features/        │  construcción "as-of": solo partidos con kickoff < t_pred
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ models/          │  Elo, Poisson, Dixon-Coles, NegBin, Logística, Markov tenis
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ ensemble/        │  P_final = Σ wi·Pi  (pesos en YAML, recalibrables)
 │ calibration/     │  Platt / Isotónica (solo con datos anteriores)
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ predictions (DB) │  registro estructurado, una fila por evento/probabilidad
 └────────┬─────────┘
          ▼
 ┌──────────────────┐
 │ reporting/       │  informe determinista + narrativa Claude validada
 └────────┬─────────┘
          ▼
   Telegram / Email
```

## 2. Estructura del paquete

```
src/sports_analytics/
├── config/            settings.py (pydantic-settings) + *.yaml (competiciones, modelos)
├── core/              logging estructurado, zona horaria, http base, errores
├── data/
│   ├── clients/       api_football.py, tennis_api.py
│   ├── filters.py     filtro de competiciones fútbol / circuito tenis
│   └── schemas.py     modelos Pydantic normalizados (Fixture, TennisMatch…)
├── features/          recencia (decay), features fútbol/tenis "as-of"
├── models/
│   ├── football/      elo, poisson, dixon_coles, corners, logistic
│   ├── tennis/        elo (general + superficie), markov, logistic
│   ├── ensemble.py
│   └── calibration.py
├── db/                SQLAlchemy ORM, sesión, repositorios
├── backtesting/       walk-forward sin leakage + métricas por segmento
├── reporting/         builder, claude_narrator, telegram, email
├── pipeline/          daily.py (predicciones), results.py (resultados reales)
└── cli.py             punto de entrada: `sports-analytics run-daily`, etc.
alembic/               migraciones
tests/                 unitarios + integración
.github/workflows/     tests.yml, daily_prediction.yml
```

## 3. Configuración

* **Secretos** → variables de entorno (`.env` local, GitHub Secrets en CI). Leídos por
  `Settings` (pydantic-settings). Los campos secretos son `SecretStr`: nunca se
  imprimen en logs ni en `repr`.
* **Parámetros de negocio** → YAML versionado en `config/`:
  * `competitions.yaml`: ligas autorizadas, IDs de API-Football, tipo (liga / copa
    internacional), flag de amistosos.
  * `tennis.yaml`: categorías incluidas/excluidas del circuito.
  * `models.yaml`: pesos de ensemble, decay de recencia, líneas de goles/córners/juegos,
    umbrales de confianza, ρ de Dixon-Coles, K de Elo, etc.

Ningún ID, peso, URL o umbral vive en el código.

## 4. Prevención de data leakage

Toda función que construye features o entrena recibe un parámetro explícito
`as_of: datetime` (UTC). Reglas:

1. Solo se usan partidos con `kickoff_utc < as_of` **y** estado finalizado.
2. Ratings (Elo) se reconstruyen secuencialmente; el rating usado para un partido es
   el anterior a su kickoff.
3. Rankings de tenis se toman de la última publicación con fecha `< as_of`.
4. La calibración (Platt/Isotónica) y la estimación de pesos del ensemble se ajustan
   solo sobre predicciones cuyo partido terminó antes de `as_of`.
5. En backtesting el bucle es *walk-forward*: para cada día D se usan solo datos < D.

Hay tests específicos que inyectan un partido "futuro" y verifican que no altera la
predicción.

## 5. Base de datos (PostgreSQL + Alembic)

| Tabla | Propósito |
|---|---|
| `competitions` | competiciones (deporte, proveedor, id externo, tipo) |
| `teams` | equipos de fútbol |
| `players` | jugadores de tenis |
| `football_matches` | partidos de fútbol (kickoff, estado, marcador final) |
| `tennis_matches` | partidos de tenis (superficie, ronda, mejor de, resultado) |
| `football_statistics` | estadísticas por partido y equipo (tiros, córners, posesión…) |
| `tennis_statistics` | estadísticas por partido y jugador (saque, resto, BP…) |
| `ratings` | historial de ratings (Elo general/superficie) con `valid_from` |
| `predictions` | una fila por (evento, mercado, modelo): probabilidad, confianza, as_of |
| `prediction_results` | resultado real del evento y si la predicción acertó |
| `model_metrics` | Brier, LogLoss, ECE… por modelo/segmento/ventana |
| `data_sources` | estado y consumo de cada API (llamadas usadas/día) |
| `pipeline_runs` | cada ejecución: inicio, fin, partidos, errores, envío |

## 6. Integración con Claude

1. Python produce `DailyReportPayload` (Pydantic) con todas las probabilidades.
2. El informe (cabeceras, porcentajes, tablas) se renderiza **sin LLM**.
3. Claude recibe el JSON y devuelve, por partido, 2–4 "factores estadísticos" y un
   resumen general, solo como texto.
4. Validador: se extraen todos los números con `%` del texto de Claude; si alguno no
   coincide (±0.05 pp) con un valor del payload, se descarta la narrativa y se usa
   un texto de respaldo generado por plantilla. También se filtra vocabulario de
   apuestas prohibido.
5. Si la API de Anthropic falla, el informe se envía igual (sin narrativa).

## 7. Tolerancia a fallos

* Cada competición se procesa de forma aislada; un error se registra y la sección se
  marca como "Datos no disponibles para esta competición".
* Cliente HTTP: timeout, reintentos con backoff exponencial + jitter, respeto de
  `Retry-After`/429, contador diario de llamadas con tope configurable, caché en disco
  con TTL por endpoint.
* `pipeline_runs` guarda el estado final (`success`, `partial`, `failed`).

## 8. Programación

GitHub Actions con cron `0 12 * * *` (12:00 UTC = 07:00 America/Bogota; Colombia no
usa horario de verano, así que el desfase es fijo). El pipeline calcula "hoy" siempre
en `America/Bogota`, no en UTC. La base de datos en producción debe ser un PostgreSQL
accesible desde Actions (p. ej. Neon/Supabase gratuito) vía `DATABASE_URL` secreto.

## 9. Persistencia y operación

* Producción: PostgreSQL gestionado (Neon) vía `DATABASE_URL`; Actions es efímero.
* La caché HTTP y el contador de cuota diaria se conservan entre corridas con
  `actions/cache`.
* `smoke_test.yml` ejecuta el pipeline con APIs reales sobre una base efímera sin enviar
  nada (verificado el 6-oct-2026: 9 partidos de fútbol reales predichos en < 3 min).

## 10. Fuera de alcance

Recomendaciones de apuesta, cuotas, stakes o casas de apuestas. El sistema no consume
cuotas ni calcula "valor".
