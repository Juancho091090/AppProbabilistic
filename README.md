# AppProbabilistic — Analítica y pronóstico probabilístico deportivo

Agente automatizado que cada día, a las **07:00 America/Bogota**:

1. Identifica los partidos de fútbol y tenis (ATP/WTA) del día en competiciones autorizadas.
2. Actualiza el histórico desde APIs deportivas y registra resultados reales de días previos.
3. Calcula probabilidades con modelos estadísticos (Elo, Poisson, Dixon-Coles, Binomial
   Negativa, regresión logística, Markov de tenis), las combina en un ensemble y las calibra.
4. Evalúa la calidad de los modelos (Brier, Log Loss, ECE) con las predicciones ya resueltas.
5. Genera un informe y lo envía por Telegram y Email.

> **Solo analítica estadística.** El sistema expresa probabilidades; no recomienda apuestas,
> cantidades ni casas de apuestas, y no consume cuotas.

## 1. Arquitectura

```
APIs → ingesta (caché, reintentos, cuotas) → PostgreSQL
     → features "as-of" (sin leakage) → modelos → ensemble → calibración
     → predicciones (tabla predictions) → informe determinista
     → Claude (solo narrativa, validada) → Telegram / Email
```

* Las probabilidades las calcula **solo Python**. Claude recibe un JSON con las cifras y
  devuelve texto; cualquier número que no exista en el JSON, o vocabulario de apuestas,
  hace que su texto se descarte (`reporting/claude_narrator.py`).
* Prevención de *data leakage*: cada ajuste recibe `as_of` y usa solo partidos terminados
  con inicio estrictamente anterior. Hay tests que añaden partidos "futuros" y verifican
  que la predicción no cambia.

Detalle en [`docs/architecture.md`](docs/architecture.md).

```
src/sports_analytics/
├── config/        settings (secretos) + YAML (competiciones, tenis, modelos)
├── core/          logging JSON con redacción de secretos, HTTP resiliente, zona horaria
├── data/          clientes API-Football y Tennis API, filtros, esquemas
├── features/      recencia y features cronológicas (fútbol, tenis)
├── models/        football/, tennis/, ensemble, calibración, confianza
├── db/            SQLAlchemy + repositorios idempotentes
├── pipeline/      ingesta, pipeline diario, liquidación de resultados, recalibración
├── backtesting/   walk-forward y métricas por segmento
├── reporting/     informe, Claude, Telegram, Email
└── cli.py         sports-analytics <comando>
```

## 2. Instalación

Requisitos: Python 3.12+, PostgreSQL 16 (o Docker).

```bash
git clone https://github.com/Juancho091090/AppProbabilistic.git
cd AppProbabilistic
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # completa tus claves; .env no se versiona
```

## 3. Variables de entorno

Ver [`.env.example`](.env.example). Las obligatorias son `DATABASE_URL`, `API_FOOTBALL_KEY` y
`TENNIS_API_KEY`. Claude, Telegram y Email son opcionales: si faltan, el pipeline funciona y
lo indica en el informe. En producción todas se definen como **GitHub Secrets**
([`docs/deployment.md`](docs/deployment.md)).

## 4. APIs necesarias

| API | Plan | Uso | Cuota |
|---|---|---|---|
| [API-Football](https://www.api-football.com) | Pro (19 USD/mes) | Fixtures, resultados, córners, cobertura | 7.500/día |
| [Tennis API – ATP WTA ITF](https://rapidapi.com) (RapidAPI) | Basic (gratis) | Calendario, resultados, rankings, superficie | 50/día |
| [Anthropic](https://console.anthropic.com) | Pago por uso | Narrativa del informe | ~1 llamada/día |

Cobertura y esquemas **verificados con las claves reales** en
[`docs/data-sources.md`](docs/data-sources.md).

## 5. Configuración

Todo parámetro de negocio vive en YAML (nada está fijo en el código):

* `config/competitions.yaml`: las 26 competiciones de fútbol con su ID de API-Football
  (verificados), tipo y temporada. Lista blanca: lo que no está aquí se excluye; los
  amistosos se excluyen salvo `include_friendlies: true`.
* `config/tennis.yaml`: filtro del circuito principal (`min_rank_id: 2` excluye ITF y
  Challenger), patrones excluidos (WTA 125, M15, juniors, exhibiciones…), solo individuales.
* `config/models.yaml`: pesos iniciales del ensemble, decay de recencia, K de Elo, ρ de
  Dixon-Coles, umbral de sobredispersión, líneas de goles, córners y juegos, umbrales de
  confianza y de calibración.

## 6. Ejecución local

```bash
docker compose up --build                     # PostgreSQL + migraciones + pipeline
# o paso a paso:
alembic upgrade head
sports-analytics check-apis                   # verifica claves, plan y cobertura
sports-analytics run-daily --no-send --output reports/output/hoy.md
sports-analytics settle                       # solo registra resultados y métricas
```

## 7. Tests

```bash
pytest                                        # unitarios (los de integración se omiten)
TEST_DATABASE_URL=postgresql+psycopg://sports:sports@localhost:5432/sports_test pytest
ruff check src tests
```

La suite incluye modelos validados contra simulación Monte Carlo y datos sintéticos con
parámetros conocidos, tests de *leakage*, clientes HTTP con respuestas reales simuladas,
PostgreSQL real (incluida la coincidencia migración/modelo) y una prueba *end-to-end* de
dos días que predice, envía y luego registra resultados.

## 8. Telegram

`TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`; pasos en
[`docs/deployment.md#3-telegram`](docs/deployment.md#3-telegram). Métodos:
`send_message()`, `send_daily_report()` y `send_error_notification()`. Los mensajes largos
se dividen automáticamente.

## 9. Email

SMTP configurable con STARTTLS (`SMTP_*`, `EMAIL_FROM`, `EMAIL_TO`). Un correo diario con el
resumen arriba y el detalle completo debajo (texto + HTML), y correos de error crítico.

## 10. GitHub Actions

* `daily_prediction.yml`: lunes a viernes 06:37 Bogotá con los partidos del día; viernes y
  sábado 18:45 Bogotá con los partidos del día siguiente (hay ligas desde las 05:00). No
  reenvía si el informe de esa fecha ya salió.
* `backtest.yml`: backtest, benchmark de mercado o recalibración 1X2 (manual); la
  recalibración corre sola cada lunes a las 05:20 Bogotá.
* `tests.yml`: lint y tests en cada push.
* `smoke_test.yml`: corrida real sin envío sobre base efímera (manual).
* `api_diagnostics.yml`: diagnóstico de APIs (manual).

## 11. Modelos estadísticos

| Deporte | Modelos | Salidas |
|---|---|---|
| Fútbol | Elo, Poisson (ataque/defensa con rival y localía), Dixon-Coles (ρ por MLE), logística multinomial, Binomial Negativa para córners | 1X2, goles esperados y su distribución, líneas de goles, córners esperados y sus líneas |
| Tenis | Elo general, Elo por superficie, logística, Markov exacto punto→partido | Ganador, al menos un set, juegos esperados, su distribución y sus líneas |

* **Ensemble** `P = Σ wᵢ·Pᵢ`: los pesos iniciales vienen del YAML y se **re-estiman con
  predicciones resueltas** (mínima log-loss) cuando hay muestras suficientes.
* **Calibración**: Platt (200 o más muestras) o isotónica (1.000 o más), sin leakage.
* **Confianza** Alta / Media / Baja: combina cantidad de datos y acuerdo entre modelos.

Detalle, fórmulas y validaciones en [`docs/models.md`](docs/models.md).

## 12. Backtesting

```bash
sports-analytics backtest --sport football --start 2025-08-01 --end 2026-05-31 --refit-days 7
sports-analytics backtest --sport tennis --start 2026-01-01 --end 2026-09-30
```

Walk-forward: en cada ventana se entrena con partidos anteriores a su inicio y se predicen
solo los de la ventana. Reporta Brier, Log Loss, Accuracy y ECE por modelo, mercado,
competición, tipo de evento y rango de probabilidad, más la curva de calibración.

**Benchmark de mercado (solo evaluación).** Si hay precios 1X2 guardados para los
partidos del periodo, el backtest añade la sección *Modelo vs mercado*: Brier multiclase,
log-loss y acierto del favorito del modelo y del mercado (probabilidades sin margen) sobre
los mismos partidos, con intervalo de confianza del 95 % de la diferencia de log-loss.
`sports-analytics market-benchmark` hace lo mismo con las predicciones reales del informe
diario ya liquidadas. Los precios nunca aparecen en el informe ni se usan para recomendar
nada. API-Football solo conserva los precios unos días: el pipeline los guarda cada día
(partidos de hoy + los de los últimos `MARKET_ODDS_BACKFILL_DAYS`), así que la muestra
crece desde el 8-oct-2026. Detalle en [docs/models.md](docs/models.md#benchmark-de-mercado).

Ambos se pueden lanzar desde GitHub: **Actions → backtest → Run workflow**.

## 13. Base de datos

PostgreSQL con migraciones Alembic (`alembic/versions`). Tablas: `competitions`, `teams`,
`players`, `football_matches`, `tennis_matches`, `football_statistics`,
`tennis_statistics`, `ratings`, `predictions`, `prediction_results`, `model_metrics`,
`data_sources` y `pipeline_runs`. Cada predicción es una fila estructurada
(deporte, competición, evento, mercado, línea, probabilidad, modelo, `as_of`, confianza).

## 14. Limitaciones

* **Tenis sin estadísticas de saque y resto**: la API no las publica. El Markov se
  parametriza desde el Elo (no con Barnett-Clarke), y su peso en el ganador se reparte
  entre los demás modelos.
* **Histórico de tenis inicial**: con 50 llamadas al día, dos años de histórico tardan unas
  dos semanas en cargarse.
* **Córners**: falta una regresión con tiros y posesión previos al partido como covariables.
* **Copas internacionales**: la fuerza Poisson de equipos de ligas distintas no es del todo
  comparable; el Elo se ajusta con todas las competiciones a la vez y ayuda a corregirlo.
* **Rankings de tenis en el histórico**: solo se usa el ranking actual para los partidos del
  día, y el ranking histórico no se reconstruye, para no introducir leakage.
* Los pesos y la calibración necesitan cientos de predicciones resueltas antes de
  re-estimarse; hasta entonces se usan los valores del YAML.
