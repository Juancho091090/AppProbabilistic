# Plantillas de Shorts (18-nov-2026)

Cuatro estilos visuales para rotar por día, cada uno en versión **fútbol** y **tenis**.
Son fotogramas de referencia (gancho + probabilidades) en HTML/CSS, renderizados con Chrome.
El motor de video actual (`src/sports_analytics/video/`) todavía no los usa: migrarlo es el
siguiente paso.

| Estilo | Fútbol | Tenis | Idea |
|---|---|---|---|
| 1. Cuaderno cuadriculado | `1_cuaderno_*` | `t1_cuaderno_*` | Cuaderno escolar: esfero azul, resaltador, post-it. En tenis, planilla de sets. |
| 2. Pizarra del DT | `2_pizarra_*` | `t2_pizarra_*` | Tiza, fichas magnéticas, flechas. En tenis, cancha y dirección del saque. |
| 3. Marcador del estadio | `3_marcador_*` | `t3_marcador_*` | Todo el lienzo es una matriz LED ámbar. En tenis, tablero de sets. |
| 4. La cancha | `4_cancha_*` | `t4_cancha_*` | Vista cenital. Fútbol: tres carriles con ancho = probabilidad. Tenis: la red se corre según la probabilidad; color según superficie (dura, arcilla, césped). |

Reglas de diseño aplicadas
- Cada estilo sale de un objeto real del mundo del fútbol/tenis; nada de tarjetas redondeadas
  iguales, etiquetas en mayúsculas de plantilla ni puntos medios.
- Subtítulos a ~1.400 px de alto: fuera de la franja inferior donde YouTube pone título y botones.
- Lo importante dentro de x 60–900 (a la derecha están los botones de Shorts).

Regenerar
```
cd design/plantillas_shorts
npm install          # fuentes OFL (Kalam, Caveat, Barlow Condensed, Doto, Archivo, Oswald)
pip install playwright
python build.py && python build_tenis.py   # requiere Chrome; salen en out/
```
