# Registro simulado (sin dinero real)

Mide, con reglas fijadas de antemano, si alguna forma de elegir resultados 1X2 tendría
ventaja frente al mercado. No es una recomendación y no aparece en el informe diario.

## Flujo automático (cada noche)

1. **18:45–19:05** · `daily_prediction` (job `daily`): genera el informe y, con los partidos
   del día siguiente, registra en `paper_selections` cada selección de cada regla con el
   precio de la casa de referencia en ese momento (Pinnacle; si falta, la de menor margen).
   En la misma corrida liquida las selecciones ya jugadas: resultado y precio de cierre
   (último precio de la misma casa antes del inicio, de la consulta final de `/odds`).
2. **Job `registro-simulado`** (mismo workflow): `sports-analytics export-paper` publica
   `paper/latest.json` y `paper/sheet_update.json` en la rama `metrics-data`.
3. **19:32** · tarea programada de Claude "Sincronizar registro simulado con Google
   Sheets": descarga `paper/latest.json` y `scripts/paper_sheet_block.py`, calcula el bloque
   de filas nuevas o liquidadas (últimos 3 días o las que falten) y lo escribe en la
   pestaña **Registro simulado** de la hoja "Éxito Deportivo · Métricas de modelos".
4. La pestaña **Simulación: resumen** calcula con fórmulas, por regla: liquidadas,
   aciertos, rendimiento con IC 95 %, CLV medio y % de selecciones con precio mejor que el
   cierre.

## Reglas (models.yaml → `paper_trading.rules`)

| Clave | Descripción |
|---|---|
| `mercado_favorito_60` | Favorito del mercado con 60 % o más (hipótesis: sesgo favorito–longshot) |
| `modelo_alta` | Favorito del modelo con confianza Alta |
| `modelo_valor_esperado` | Probabilidad del modelo × precio > 1 (control: en el histórico pierde) |
| `mercado_favorito` | Favorito del mercado en todos los partidos (referencia) |

Una regla no se modifica con los resultados: un cambio crea una regla nueva con otra clave.

## Métricas

* Ganancia por selección (1 unidad): precio − 1 si acierta, −1 si falla.
* **CLV** = precio tomado × probabilidad sin margen al cierre − 1. Positivo de forma
  sostenida es la señal más temprana de ventaja real.
* Muestra mínima orientativa: ~1.500 selecciones por regla (IC del rendimiento ≈ ±3 pp
  con precios cercanos a 1.45).

## Mejora opcional: escritura directa desde GitHub Actions

Hoy la escritura en la hoja la hace la tarea programada con el conector de Google. Para que
la haga el propio workflow, sin depender de esa tarea, haría falta una cuenta de servicio
de Google Cloud con la API de Sheets activada y la hoja compartida con su correo; su
clave JSON iría como secreto de GitHub (`GOOGLE_SERVICE_ACCOUNT_JSON`), nunca en el
repositorio.
