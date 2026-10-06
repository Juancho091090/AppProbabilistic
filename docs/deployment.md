# Despliegue y operación

## 1. Secretos de GitHub

En **Settings → Secrets and variables → Actions → New repository secret**:

| Secreto | Obligatorio | Dónde obtenerlo |
|---|---|---|
| `DATABASE_URL` | Sí | Connection string de PostgreSQL persistente (ver §2) |
| `API_FOOTBALL_KEY` | Sí | dashboard.api-football.com → *Account* (plan Pro) |
| `TENNIS_API_KEY` | Sí | RapidAPI → *Tennis API - ATP WTA ITF* → pestaña Endpoints → `X-RapidAPI-Key` |
| `ANTHROPIC_API_KEY` | No | console.anthropic.com → API Keys. Sin ella el informe usa factores deterministas |
| `TELEGRAM_BOT_TOKEN` | No | @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | No | Ver §3 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` | No | Ver §4 |

Nunca se escriben claves en archivos del repositorio ni en issues. Los logs redactan
automáticamente los valores registrados como secretos.

## 2. Base de datos PostgreSQL (Neon, gratis)

GitHub Actions no conserva estado entre ejecuciones; el histórico, las predicciones y los
resultados viven en PostgreSQL.

1. Crea una cuenta en https://neon.tech (puedes entrar con GitHub).
2. Crea el proyecto `appprobabilistic` (región AWS US East).
3. Copia la *Connection string* (`postgresql://…?sslmode=require`) y guárdala como
   `DATABASE_URL`. El código la convierte automáticamente al driver `psycopg`.
4. Las tablas se crean solas: el workflow ejecuta `alembic upgrade head` antes de cada corrida.

El plan gratuito de Neon (0.5 GB) basta para años de histórico (~150 MB estimados).

## 3. Telegram

1. En Telegram, abre @BotFather → `/newbot` → guarda el token como `TELEGRAM_BOT_TOKEN`.
2. Escríbele cualquier mensaje a tu bot.
3. Abre `https://api.telegram.org/bot<TOKEN>/getUpdates` en el navegador y copia
   `result[0].message.chat.id` como `TELEGRAM_CHAT_ID` (para un grupo es negativo).

El informe se envía en HTML y se divide en varios mensajes si supera el límite de 4096
caracteres.

## 4. Email (Gmail como ejemplo)

1. Activa la verificación en dos pasos en tu cuenta de Google.
2. Crea una *contraseña de aplicación* en https://myaccount.google.com/apppasswords.
3. Secretos: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME=<tu correo>`,
   `SMTP_PASSWORD=<contraseña de aplicación>`, `EMAIL_FROM=<tu correo>`,
   `EMAIL_TO=<destinatarios separados por coma>`.

## 5. Workflows

| Workflow | Disparo | Qué hace |
|---|---|---|
| `tests.yml` | push / PR | Lint + pytest (unitarios + integración con PostgreSQL de servicio) |
| `daily_prediction.yml` | 12:00 UTC (07:00 Bogotá) y manual | Migraciones → ingesta → resultados → modelos → informe → envío |
| `smoke_test.yml` | Manual | Igual que el diario, con APIs reales y una base efímera, **sin enviar** |
| `api_diagnostics.yml` | Manual / cambios en clientes | Verifica claves, plan, cobertura e IDs |

**Ejecutar manualmente:** pestaña *Actions* → workflow → *Run workflow*. En
`daily-prediction` puedes desmarcar *send* para generar el informe sin enviarlo.

El informe de cada corrida queda en el resumen del job y como artefacto descargable
durante 30 días. La caché HTTP y el contador de cuota se conservan entre corridas con
`actions/cache`.

### Zona horaria
Colombia usa UTC−5 todo el año (sin horario de verano), así que `0 12 * * *` equivale
siempre a las 07:00 de Bogotá. GitHub puede retrasar los cron unos minutos en horas pico.

## 6. Primera ejecución (carga de histórico)

* **Fútbol:** la primera corrida descarga 3 temporadas de las 26 competiciones (~78
  llamadas) y hasta 2.500 estadísticas de partidos. Las estadísticas restantes se
  completan en los días siguientes.
* **Tenis:** cada día se refrescan los últimos 3 días y se usan ~30 llamadas para
  retroceder en el histórico (ventanas de 7 días). Dos años de histórico se completan en
  unas dos semanas; mientras tanto la confianza de tenis será más baja.

Para acelerar la carga puedes lanzar `daily-prediction` manualmente con *send*
desmarcado varias veces el mismo día: la cuota se respeta con el contador persistido.

## 7. Ejecución local

```bash
cp .env.example .env            # completa las claves
docker compose up --build       # PostgreSQL + migraciones + pipeline
# o sin Docker:
pip install -e ".[dev]"
alembic upgrade head
sports-analytics check-apis
sports-analytics run-daily --no-send --output reports/output/hoy.md
sports-analytics backtest --sport football --start 2025-08-01 --end 2026-05-31 --refit-days 7
```

## 8. Operación y fallos

* Estado de cada corrida: tabla `pipeline_runs` (`success`, `partial`, `failed`) con
  errores, partidos encontrados y filtrados, predicciones y envíos.
* Si una competición o API falla, el informe sale igual y la marca como
  "Datos no disponibles para esta competición."
* Un error fatal envía una notificación por Telegram y Email (si están configurados)
  y el workflow queda en rojo.
