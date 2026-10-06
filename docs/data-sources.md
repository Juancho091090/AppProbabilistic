# Fuentes de datos

Hallazgos verificados con las claves reales mediante el workflow `api-diagnostics`
(6 de octubre de 2026).

## API-Football (fútbol)

* Base: `https://v3.football.api-sports.io` · cabecera `x-apisports-key`.
* Cuenta: **plan Free**, con 100 llamadas al día.
* **IDs de competición verificados**: los 26 configurados en `competitions.yaml` existen y
  su país coincide con el de la API.
* **Cobertura**: todas las competiciones tienen estadísticas por partido (córners, tiros,
  posesión) y eventos.

### Restricciones del plan Free (confirmadas)

| Consulta | Resultado |
|---|---|
| `/fixtures?league&season` con temporada 2025 o 2026 | ❌ "Free plans do not have access to this season, try from 2022 to 2024" |
| `/fixtures?date=` hace 7, 60 o 400 días | ❌ "Free plans do not have access to this date, try from ayer to mañana" |
| `/fixtures?date=hoy` | ✅ 200 partidos, 10 en competiciones autorizadas |

Consecuencia: con el plan Free solo se puede leer (a) temporadas 2022–2024 completas y
(b) una ventana de ayer, hoy y mañana. No se puede cargar el histórico de la temporada en
curso, y los modelos lo necesitan para estimar la fuerza actual de cada equipo.

Planes de pago (precios publicados): Pro 19 USD/mes con 7.500 llamadas al día; todos los
planes incluyen todas las competiciones y endpoints.

### Presupuesto de llamadas (diseño)

| Uso | Llamadas | Frecuencia |
|---|---|---|
| `/leagues?current=true` (cobertura de todas las ligas) | 1 | caché de 24 h |
| `/fixtures?date=hoy` (partidos del día, todas las ligas) | 1 | diaria |
| `/fixtures?date=ayer` (resultados para registrar y actualizar) | 1 | diaria |
| `/fixtures/statistics?fixture=` (córners/tiros de partidos terminados) | 1 por partido | con tope por ejecución |
| `/fixtures?league&season` (carga inicial de histórico) | 1 por liga-temporada | una vez, en caché |

## Tennis API (tenis)

* Proveedor: Tennis API – ATP WTA ITF, distribuida por RapidAPI.
  Documentación: https://docs.tennis-api.com/
* Base: `https://tennis-api-atp-wta-itf.p.rapidapi.com` · cabeceras `X-RapidAPI-Key` y
  `X-RapidAPI-Host`.
* Plan FREE: 50 llamadas al día y 4 por segundo, con los mismos datos que el plan PRO.
  Las cuotas y las predicciones propias del proveedor están restringidas; este sistema no
  las usa.
* Endpoints relevantes: `/tennis/v2/{atp|wta}/fixtures/{fecha}`,
  `/tennis/v2/{atp|wta}/results/{fecha}`, `/tennis/v2/{atp|wta}/ranking/singles`,
  `/tennis/v2/{atp|wta}/tournament/calendar/{año}`, con resultados desde 2010.
* Filtro de circuito: `tournament.rankId` (0 = ITF $10K, 1 = Challenger/ITF, 2 = circuito
  principal, 3 = Masters, 4 = Grand Slam). El sistema admitirá solo `rankId ≥ 2`, además del
  filtro por texto de `tennis.yaml`.
* Estado: ✅ suscripción activa (plan Basic, 50 llamadas al día), verificada el 6 de octubre de 2026.

### Esquema verificado con la API real

| Campo | Significado |
|---|---|
| `tournament.courtId` | 1 = dura, 2 = arcilla, 3 = dura bajo techo, 4 = carpet, 5 = césped |
| `tournament.rankId` | 0 = ITF (M15/W15…), 1 = Challenger / WTA 125, 2 o más = circuito principal; 7 = Finals |
| `result` | marcador desde player1, p. ej. `"6-3 2-6 4-2 ret."`; tiebreak como `7-6(4)` |
| `result_type` | `completed` o `retired` |
| `match_winner` | id del jugador ganador |
| `best_of` | siempre `null`; se infiere (5 sets en Grand Slam ATP, `grand_slam_rank_id: 4`) |

* Los **fixtures** no traen el objeto torneo, solo `tournamentId`. Se cruzan con el
  calendario anual: 2 páginas por circuito, con caché de 7 días.
* Paginación: `pageSize` hasta 500 y `hasNextPage`.
* Cabeceras de cuota: `x-ratelimit-requests-remaining` (diaria) y `x-ratelimit-remaining`.
* **No hay estadísticas de saque y resto** en resultados ni fixtures. El modelo de Markov
  usa entonces probabilidades de saque derivadas del Elo (`serve_probs_from_match_prob`),
  y la confianza lo refleja.
* Volumen observado: más de 500 resultados ATP en 8 días (hay más páginas), de los que el circuito principal
  es una fracción pequeña; ~236 WTA por día.

### Presupuesto diario (50 llamadas)

| Uso | Llamadas |
|---|---|
| Fixtures de hoy ATP + WTA | 2 |
| Resultados de ayer ATP + WTA | 2 |
| Ranking ATP + WTA | 2 (caché de 12 h) |
| Calendario anual ATP + WTA | ~4 por semana |
| **Libre para cargar histórico** | ~35 por día (500 resultados por llamada) |
