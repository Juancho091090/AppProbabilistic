"""Bloque A:R para la pestaña "Registro simulado" a partir de paper/latest.json.

Uso: python paper_sheet_block.py latest.json FILAS_EN_HOJA
Imprime JSON {"range", "formulas"} para update_formulas, o NADA si no hay cambios.
Incluye desde la primera fila que falta en la hoja o que cambió en los últimos 3 días.
Lo usa la tarea programada que sincroniza la hoja de Google cada noche.
"""

import json
import sys
from datetime import UTC, datetime, timedelta

COLS = [
    "n",
    "report_date",
    "rule",
    "competition",
    "match",
    "kickoff_local",
    "selection",
    "bookmaker",
    "price_taken",
    "fair_prob_taken",
    "model_prob",
    "confidence",
    "price_close",
    "fair_prob_close",
    "result",
]


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as fh:
        rows = json.load(fh)
    in_sheet = int(sys.argv[2])  # filas de datos ya escritas (sin el encabezado)
    since = datetime.now(UTC) - timedelta(days=3)
    changed = [r["n"] for r in rows if datetime.fromisoformat(r["updated"]) >= since]
    start = min([in_sheet + 1, *changed])
    if start > len(rows):
        print("NADA")
        return
    out = []
    for r in rows[start - 1 :]:
        i = r["n"] + 1
        out.append(
            ["" if r[c] is None else r[c] for c in COLS]
            + [
                f'=IF(O{i}="Acertó",I{i}-1,IF(O{i}="Falló",-1,""))',
                f'=IF(N{i}="","",I{i}*N{i}-1)',
                f'=IF(M{i}="","",I{i}/M{i}-1)',
            ]
        )
    rng = f"'Registro simulado'!A{start + 1}:R{len(rows) + 1}"
    print(json.dumps({"range": rng, "formulas": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
