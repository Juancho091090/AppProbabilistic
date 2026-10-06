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
* Estado: ❌ **"You are not subscribed to this API."** La clave de RapidAPI es válida, pero
  la cuenta no está suscrita a esta API. Hay que pulsar *Subscribe* en el plan FREE de la
  página de la API en RapidAPI.
* Pendiente de verificar con la API real (lo hará la sonda del diagnóstico):
  * nombres exactos de los campos de partido y resultado;
  * identificadores de superficie (`courtId`);
  * cómo se marcan los retiros;
  * endpoint de estadísticas de saque y resto por partido.
