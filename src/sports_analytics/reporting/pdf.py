"""Informe diario en PDF (adjunto del correo).

Se genera directamente desde el ``DailyReportPayload`` (mismos números que el informe
en Markdown/Telegram). La narrativa de Claude, si existe y fue validada, se incluye solo
como texto explicativo.
"""

from __future__ import annotations

import io
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sports_analytics.models.confidence import ConfidenceLevel
from sports_analytics.models.outputs import MatchForecast
from sports_analytics.reporting.builder import DISCLAIMER, UNAVAILABLE, pct
from sports_analytics.reporting.payload import DailyReportPayload, statistical_factors

# Fuente Unicode (nombres con tildes, ñ, ş, ł…). Si no existe, Helvetica.
_FONT_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu")]
FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
for _d in _FONT_DIRS:
    if (_d / "DejaVuSans.ttf").exists() and (_d / "DejaVuSans-Bold.ttf").exists():
        pdfmetrics.registerFont(TTFont("DejaVu", str(_d / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_d / "DejaVuSans-Bold.ttf")))
        FONT, FONT_BOLD = "DejaVu", "DejaVu-Bold"
        break

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#616e7c")
ACCENT = colors.HexColor("#0b6e4f")
GRID = colors.HexColor("#d9e2ec")
HEAD_BG = colors.HexColor("#eef2f6")
CONF_COLORS = {
    "high": colors.HexColor("#0b6e4f"),
    "medium": colors.HexColor("#b7791f"),
    "low": colors.HexColor("#c53030"),
}


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "t",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=20,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "sub": ParagraphStyle("s", fontName=FONT, fontSize=9.5, textColor=MUTED, spaceAfter=10),
        "h1": ParagraphStyle(
            "h1", fontName=FONT_BOLD, fontSize=15, textColor=ACCENT, spaceBefore=10, spaceAfter=6
        ),
        "h2": ParagraphStyle(
            "h2", fontName=FONT_BOLD, fontSize=12, textColor=INK, spaceBefore=8, spaceAfter=4
        ),
        "match": ParagraphStyle("m", fontName=FONT_BOLD, fontSize=11, textColor=INK, spaceAfter=1),
        "meta": ParagraphStyle("meta", fontName=FONT, fontSize=8.5, textColor=MUTED, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=FONT, fontSize=9, textColor=INK, leading=12),
        "small": ParagraphStyle("sm", fontName=FONT, fontSize=8, textColor=MUTED, leading=10),
        "cell": ParagraphStyle("c", fontName=FONT, fontSize=8.5, textColor=INK, leading=10),
        "cellb": ParagraphStyle("cb", fontName=FONT_BOLD, fontSize=8.5, textColor=INK, leading=10),
    }


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(rows: list[list], widths: list[float], header: bool = True) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ]
    t.setStyle(TableStyle(style))
    return t


def _confidence(f: MatchForecast, st) -> Paragraph:
    color = CONF_COLORS.get(f.confidence, INK).hexval().replace("0x", "#")
    label = ConfidenceLevel(f.confidence).label_es
    return Paragraph(f'Confianza: <font color="{color}"><b>{label}</b></font>', st["body"])


def _factors(f: MatchForecast, narrative, st) -> list:
    items = (narrative or {}).get(f.event_id) or statistical_factors(f)
    out = [Paragraph("<b>Factores estadísticos</b>", st["body"])]
    out += [Paragraph(f"• {_esc(x)}", st["small"]) for x in items]
    if f.data_notes:
        out.append(Paragraph(f"Notas de datos: {_esc('; '.join(f.data_notes))}", st["small"]))
    return out


def _football(f: MatchForecast, tz: ZoneInfo, narrative, st) -> KeepTogether:
    m = f.markets
    x = m["1x2"]
    when = f.kickoff_utc.astimezone(tz).strftime("%H:%M")
    flow = [
        Paragraph(f"{_esc(f.home_or_a)} vs {_esc(f.away_or_b)}", st["match"]),
        Paragraph(f"Hora: {when} · {_esc(f.competition)}", st["meta"]),
        _table(
            [
                ["Local", "Empate", "Visitante", "Goles esperados", "Córners esperados"],
                [
                    pct(x["home"]),
                    pct(x["draw"]),
                    pct(x["away"]),
                    f"{m['goals']['expected_goals']['total']:.2f}",
                    f"{m['corners']['expected']['total']:.2f}" if m.get("corners") else "s/d",
                ],
            ],
            [24 * mm, 24 * mm, 24 * mm, 34 * mm, 36 * mm],
        ),
        Spacer(1, 3),
    ]
    goal_lines = m["goals"]["goal_lines"]
    rows = [
        ["Línea", *[f"+{k}" for k in goal_lines]],
        ["Goles", *[pct(v["over"]) for v in goal_lines.values()]],
    ]
    if m.get("corners"):
        c_lines = m["corners"]["lines"]
        rows_c = [
            ["Línea", *[f"+{k}" for k in c_lines]],
            ["Córners", *[pct(v["over"]) for v in c_lines.values()]],
        ]
    else:
        rows_c = None
    flow.append(_table(rows, [20 * mm] + [24.4 * mm] * len(goal_lines)))
    if rows_c:
        flow += [Spacer(1, 2), _table(rows_c, [20 * mm] + [24.4 * mm] * (len(rows_c[0]) - 1))]
    else:
        flow.append(Paragraph("Córners: sin datos suficientes", st["small"]))
    flow += [Spacer(1, 3), _confidence(f, st), *_factors(f, narrative, st), Spacer(1, 8)]
    return KeepTogether(flow)


def _tennis(f: MatchForecast, tz: ZoneInfo, narrative, st) -> KeepTogether:
    m = f.markets
    when = f.kickoff_utc.astimezone(tz).strftime("%H:%M")
    surface = f.context.get("surface", "desconocida")
    lines = m["games_lines"]
    flow = [
        Paragraph(f"{_esc(f.home_or_a)} vs {_esc(f.away_or_b)}", st["match"]),
        Paragraph(
            f"{_esc(f.competition)} · Hora: {when} · Superficie: {surface} · "
            f"Mejor de {m['best_of']}",
            st["meta"],
        ),
        _table(
            [
                ["", "Gana el partido", "Gana al menos un set"],
                [
                    Paragraph(_esc(f.home_or_a), st["cell"]),
                    pct(m["winner"]["A"]),
                    pct(m["at_least_one_set"]["A"]),
                ],
                [
                    Paragraph(_esc(f.away_or_b), st["cell"]),
                    pct(m["winner"]["B"]),
                    pct(m["at_least_one_set"]["B"]),
                ],
            ],
            [70 * mm, 36 * mm, 40 * mm],
        ),
        Spacer(1, 3),
        Paragraph(f"Juegos esperados: <b>{m['expected_games']:.1f}</b>", st["body"]),
        _table(
            [
                ["Línea", *[f"+{k}" for k in lines]],
                ["Juegos", *[pct(v["over"]) for v in lines.values()]],
            ],
            [18 * mm] + [min(19 * mm, 160 * mm / max(len(lines), 1))] * len(lines),
        ),
        Spacer(1, 3),
        _confidence(f, st),
        *_factors(f, narrative, st),
        Spacer(1, 8),
    ]
    return KeepTogether(flow)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(15 * mm, 10 * mm, DISCLAIMER)
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Página {doc.page}")
    canvas.restoreState()


def render_pdf(
    payload: DailyReportPayload,
    narrative: dict[str, list[str]] | None = None,
    summary: str | None = None,
) -> bytes:
    st = _styles()
    tz = ZoneInfo(payload.timezone)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"Informe deportivo {payload.report_date}",
        author="AppProbabilistic",
    )
    gen = payload.generated_at.astimezone(tz)
    flow: list = [
        Paragraph("Informe deportivo", st["title"]),
        Paragraph(
            f"{payload.report_date.isoformat()} · generado a las {gen:%H:%M} ({payload.timezone})",
            st["sub"],
        ),
        _table(
            [
                ["Fútbol", "ATP", "WTA"],
                [
                    str(payload.n_football),
                    str(payload.n_tennis("ATP")),
                    str(payload.n_tennis("WTA")),
                ],
            ],
            [30 * mm] * 3,
        ),
        Spacer(1, 6),
    ]
    if summary:
        flow += [Paragraph(_esc(summary), st["body"]), Spacer(1, 4)]

    flow.append(Paragraph("Fútbol", st["h1"]))
    if not payload.football:
        flow.append(Paragraph("Sin partidos en competiciones autorizadas hoy.", st["body"]))
    for section in payload.football:
        flow.append(Paragraph(_esc(section.name), st["h2"]))
        if section.unavailable_reason:
            flow.append(Paragraph(f"{UNAVAILABLE} {_esc(section.unavailable_reason)}", st["small"]))
            continue
        for f in sorted(section.forecasts, key=lambda f: f.kickoff_utc):
            flow.append(_football(f, tz, narrative, st))

    flow.append(Paragraph("Tenis", st["h1"]))
    if not any(payload.tennis.values()):
        flow.append(Paragraph("Sin partidos ATP/WTA del circuito principal hoy.", st["body"]))
    for tour in ("ATP", "WTA"):
        items = payload.tennis.get(tour) or []
        if items:
            flow.append(Paragraph(tour, st["h2"]))
            for f in sorted(items, key=lambda f: f.kickoff_utc):
                flow.append(_tennis(f, tz, narrative, st))

    annex = []
    if payload.model_quality:
        annex.append(Paragraph("Calidad de los modelos (predicciones resueltas)", st["h2"]))
        annex.append(
            _table(
                [["Deporte", "Mercado", "n", "Brier", "LogLoss", "ECE"]]
                + [
                    [
                        q["sport"],
                        q["market"],
                        str(q["n"]),
                        f"{q['brier']:.3f}",
                        f"{q['log_loss']:.3f}",
                        f"{q['ece']:.3f}",
                    ]
                    for q in payload.model_quality
                ],
                [26 * mm, 30 * mm, 16 * mm, 22 * mm, 22 * mm, 22 * mm],
            )
        )
    if payload.load_summary:
        annex.append(Paragraph("Carga de datos", st["h2"]))
        annex += [Paragraph(f"• {_esc(x)}", st["small"]) for x in payload.load_summary]
    issues = payload.skipped + payload.data_issues
    if issues:
        annex.append(Paragraph("Datos faltantes e incidencias", st["h2"]))
        annex += [Paragraph(f"• {_esc(x)}", st["small"]) for x in issues]
    if annex:
        flow += [PageBreak(), Paragraph("Anexo", st["h1"]), *annex]

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
