"""Versión tenis de las 4 plantillas (gancho + probabilidades). Mismo sistema que build.py."""

import asyncio

from build import (
    CANCHA_CSS, CUADERNO_CSS, LED_CSS, LOGO_IMG, OUT, PIZARRA_CSS, ROOT, page,
)
from playwright.async_api import async_playwright

# Partido de ejemplo con las salidas reales del modelo de tenis (Markov + Elo)
T = dict(a="Sinner", b="Zverev", pa=68, pb=32, games="22,4", when="jueves 20, 1:30 p. m.",
         torneo="ATP Finals", superficie="dura", sets={"2-0": 41, "2-1": 27, "1-2": 18, "0-2": 14},
         saque_a=66, saque_b=62)

# Colores de superficie: la cancha cambia según el partido
SURF = {
    "dura": ("#2F62A8", "#2C7A5B"),      # pista azul, exterior verde
    "arcilla": ("#C8642F", "#B5562A"),
    "césped": ("#5E9E3E", "#4E8A33"),
}


# ------------------------------------------------------------------ 1. Cuaderno (planilla del juez)
def cuaderno_hook():
    cells = "".join(
        f'<div class="abs" style="left:{450 + c * 130}px;top:{r}px;width:130px;height:110px;border:4px solid #2342A8;'
        f'filter:url(#rough)"></div>'
        for r in (900, 1010) for c in range(3))
    b = f"""
<div class="abs margin"></div>
<div class="abs" style="left:190px;top:120px;width:120px;height:120px;transform:rotate(-7deg)">{LOGO_IMG}</div>
<div class="abs pencil" style="left:330px;top:150px;font-size:48px">JC Probabilidades</div>
<div class="abs pen" style="left:190px;top:330px;width:820px;font-size:76px;line-height:1.15;font-weight:700">
 {T['a']}, favorito:<br><span class="hl">¿lo cierra en dos sets?</span></div>
<div class="abs pencil" style="left:190px;top:620px;font-size:52px">planilla del partido, {T['torneo']}</div>
<div class="abs pen" style="left:450px;top:840px;font-size:44px;width:390px;display:flex;justify-content:space-around">
 <span>set 1</span><span>set 2</span><span>set 3</span></div>
<div class="abs pen" style="left:190px;top:915px;font-size:80px;font-weight:700">{T['a']}</div>
<div class="abs pen" style="left:190px;top:1025px;font-size:80px;font-weight:700">{T['b']}</div>
{cells}
<div class="abs pen" style="left:505px;top:915px;font-size:72px;color:#9AA6C4">?</div>
<div class="abs postit" style="left:330px;top:1180px;width:520px;height:250px;transform:rotate(-3deg);padding:40px 40px">
 <div class="abs tape" style="left:170px;top:-26px;transform:rotate(3deg)"></div>
 <div class="pen" style="font-size:44px;color:#3A3A3A">el modelo dice:</div>
 <div class="pen" style="font-size:84px;font-weight:700;line-height:1.1">{T['pa']} / {T['pb']}</div></div>
<div class="abs pencil" style="left:190px;top:1480px;font-size:50px;transform:rotate(-1deg)">{T['when']}</div>
"""
    return page(b, CUADERNO_CSS)


def cuaderno_prob():
    rows = list(T["sets"].items())
    marks = ""
    for i, (score, p) in enumerate(rows):
        y = 760 + i * 120
        marks += f"""
<div class="abs pen" style="left:190px;top:{y}px;font-size:64px;font-weight:700;width:150px">{score}</div>
<div class="abs" style="left:350px;top:{y + 22}px;width:{p * 11}px;height:58px;border:5px solid #2342A8;border-radius:5px 12px 6px 10px;
 background:repeating-linear-gradient(-45deg,#2342A8 0 4px,transparent 4px 15px);filter:url(#rough)"></div>
<div class="abs pen" style="left:{370 + p * 11}px;top:{y}px;font-size:64px;font-weight:700">{p}%</div>"""
    b = f"""
<div class="abs margin"></div>
<div class="abs" style="left:190px;top:120px;width:96px;height:96px;transform:rotate(-7deg)">{LOGO_IMG}</div>
<div class="abs pen" style="left:190px;top:270px;font-size:86px;font-weight:700">{T['a']} {T['pa']}%</div>
<div class="abs pen" style="left:190px;top:380px;font-size:86px;font-weight:700;color:#5D6A8F">{T['b']} {T['pb']}%</div>
<svg class="abs" style="left:186px;top:500px" width="620" height="40" filter="url(#rough)">
 <path d="M5 22 Q 70 2 140 20 T 280 20 T 420 20 T 600 18" fill="none" stroke="#2342A8" stroke-width="6"/></svg>
<div class="abs pencil" style="left:190px;top:620px;font-size:58px">¿cómo termina? (sets)</div>
{marks}
<div class="abs pen" style="left:190px;top:1260px;font-size:58px;color:#C83A3A;transform:rotate(-2deg)">games esperados: {T['games']}</div>
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap">{T['a']} gana <span>2 a 0</span> en 4 de cada 10</div></div>
"""
    return page(b, CUADERNO_CSS)


# ------------------------------------------------------------------ 2. Pizarra
def court_chalk(op=0.5):
    return f"""<svg class="abs" style="left:0;top:0;opacity:{op}" width="1080" height="1920" filter="url(#chalk)">
 <g fill="none" stroke="#EEF1EA" stroke-width="7">
  <rect x="200" y="540" width="680" height="1000"/><rect x="270" y="540" width="540" height="1000"/>
  <line x1="150" y1="1040" x2="930" y2="1040" stroke-width="10"/>
  <line x1="270" y1="790" x2="810" y2="790"/><line x1="270" y1="1290" x2="810" y2="1290"/>
  <line x1="540" y1="790" x2="540" y2="1290"/></g></svg>"""


def pizarra_hook():
    b = f"""
<div class="abs board"></div>{court_chalk()}
<div class="abs" style="left:60px;top:110px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs ch" style="left:200px;top:115px;font-size:60px">la pizarra de mañana</div>
<div class="abs ch" style="left:80px;top:250px;font-size:132px;line-height:1">{T['a']} vs {T['b']}</div>
<svg class="abs" style="left:90px;top:395px" width="860" height="60" filter="url(#chalk)">
 <path d="M8 34 C 200 10, 480 50, 850 22" stroke="#F3D77F" stroke-width="12" fill="none" stroke-linecap="round"/></svg>
<div class="abs magnet" style="left:445px;top:600px;width:170px;height:170px;background:#E7E1C9"></div>
<div class="abs ch" style="left:640px;top:630px;font-size:86px">{T['a']}</div>
<div class="abs magnet" style="left:445px;top:1310px;width:170px;height:170px;background:#3A3F44"></div>
<div class="abs ch" style="left:160px;top:1340px;font-size:86px">{T['b']}</div>
<svg class="abs" style="left:0;top:0" width="1080" height="1920" filter="url(#chalk)">
 <path d="M520 780 C 420 900, 360 1100, 330 1230" stroke="#EE9384" stroke-width="8" fill="none" stroke-dasharray="26 18"/>
 <path d="M330 1230 l-10 -44 M330 1230 l30 -32" stroke="#EE9384" stroke-width="8"/></svg>
<div class="abs ch red" style="left:110px;top:1000px;font-size:62px;transform:rotate(-8deg)">saque abierto</div>
<div class="abs ch yel" style="left:200px;top:1560px;font-size:70px">{T['torneo']}, pista {T['superficie']}</div>
"""
    return page(b, PIZARRA_CSS)


def pizarra_prob():
    b = f"""
<div class="abs board"></div>
<div class="abs" style="left:60px;top:110px;width:96px;height:96px">{LOGO_IMG}</div>
<div class="abs ch" style="left:90px;top:255px;font-size:120px">¿Quién gana?</div>
<div class="abs ch" style="left:90px;top:440px;font-size:90px">{T['a']}</div>
<div class="abs" style="left:90px;top:560px;width:{T['pa'] * 9}px;height:90px;filter:url(#chalk);
 background:repeating-linear-gradient(100deg,#F3D77F 0 9px,transparent 9px 15px)"></div>
<div class="abs ch yel" style="left:{120 + T['pa'] * 9}px;top:520px;font-size:140px">{T['pa']}%</div>
<div class="abs ch" style="left:90px;top:720px;font-size:90px">{T['b']}</div>
<div class="abs" style="left:90px;top:840px;width:{T['pb'] * 9}px;height:90px;filter:url(#chalk);
 background:repeating-linear-gradient(100deg,#EEF1EA 0 9px,transparent 9px 15px)"></div>
<div class="abs ch" style="left:{120 + T['pb'] * 9}px;top:800px;font-size:140px">{T['pb']}%</div>
<div class="abs ch" style="left:90px;top:1030px;font-size:70px">puntos ganados con su saque</div>
<div class="abs ch" style="left:90px;top:1120px;font-size:110px">{T['saque_a']}% <span style="font-size:70px">vs</span> {T['saque_b']}%</div>
<svg class="abs" style="left:600px;top:1220px" width="420" height="170" filter="url(#chalk)">
 <ellipse cx="210" cy="85" rx="190" ry="70" stroke="#EE9384" stroke-width="9" fill="none"/></svg>
<div class="abs ch red" style="left:650px;top:1255px;font-size:66px">{T['games']} games</div>
<div class="abs" style="left:110px;top:1430px;width:860px;text-align:center"><div class="cap">el modelo le da a {T['a']} <span>68 por ciento</span></div></div>
"""
    return page(b, PIZARRA_CSS)


# ------------------------------------------------------------------ 3. Marcador LED (tablero de tenis)
def led_hook():
    def row(y, name, serve):
        dot = '<span class="led r" style="font-size:70px">●</span>' if serve else ""
        cells = "".join(
            f'<div class="abs led w" style="left:{620 + k * 120}px;top:{y}px;font-size:110px">-</div>' for k in range(3))
        return f'<div class="abs led" style="left:110px;top:{y}px;font-size:110px">{name.upper()}</div>{dot and f"<div class=abs style=left:560px;top:{y + 20}px>{dot}</div>"}{cells}'
    b = f"""
<div class="abs housing"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs panel"></div>
<div class="abs led r" style="left:110px;top:240px;font-size:58px">{T['torneo'].upper()}</div>
<div class="abs led w" style="left:110px;top:330px;font-size:58px">FAVORITO CLARO 68%</div>
<div class="abs led r" style="left:625px;top:560px;font-size:46px">S1</div><div class="abs led r" style="left:745px;top:560px;font-size:46px">S2</div><div class="abs led r" style="left:865px;top:560px;font-size:46px">S3</div>
{row(640, T['a'], True)}{row(820, T['b'], False)}
<div class="abs led w" style="left:110px;top:1080px;font-size:58px">GAMES ESPERADOS</div>
<div class="abs led" style="left:110px;top:1160px;font-size:150px">22.4</div>
<div class="abs led w" style="left:110px;top:1390px;font-size:64px">JUE 20  13:30</div>
"""
    return page(b, LED_CSS)


def led_prob():
    def bar(y, name, p, lit_cls):
        n = 24
        lit = round(p / 100 * n)
        dots = "".join(
            f'<div class="abs dot {lit_cls if k < lit else "off"}" style="left:{110 + k * 34}px;top:{y + 140}px;width:26px;height:26px"></div>'
            f'<div class="abs dot {lit_cls if k < lit else "off"}" style="left:{110 + k * 34}px;top:{y + 176}px;width:26px;height:26px"></div>'
            for k in range(n))
        return (f'<div class="abs led {"w" if lit_cls == "onw" else ""}" style="left:110px;top:{y}px;font-size:96px">{name.upper()}</div>'
                f'<div class="abs led {"w" if lit_cls == "onw" else ""}" style="left:720px;top:{y}px;font-size:96px">{p}%</div>{dots}')
    sets = "".join(
        f'<div class="abs led {"r" if s.startswith("2") else "w"}" style="left:{110 + i * 215}px;top:1080px;font-size:66px">{s}</div>'
        f'<div class="abs led" style="left:{110 + i * 215}px;top:1170px;font-size:84px">{p}</div>'
        for i, (s, p) in enumerate(T["sets"].items()))
    b = f"""
<div class="abs housing"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs panel"></div>
<div class="abs led w" style="left:110px;top:240px;font-size:58px">¿QUIÉN GANA?</div>
{bar(380, T['a'], T['pa'], 'on')}{bar(660, T['b'], T['pb'], 'onw')}
<div class="abs led r" style="left:110px;top:990px;font-size:52px">RESULTADO EN SETS %</div>
{sets}
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap">el modelo le da a {T['a']} <span>68 por ciento</span></div></div>
"""
    return page(b, LED_CSS)


# ------------------------------------------------------------------ 4. La cancha (según superficie)
def court(surface, op=0.95):
    inner, outer = SURF[surface]
    return f"""
<div class="abs" style="inset:0;background:{outer}"></div>
<div class="abs" style="left:150px;top:220px;width:780px;height:1500px;background:{inner}"></div>
<div class="abs" style="inset:0;filter:url(#grain);opacity:.10"></div>
<svg class="abs" style="left:0;top:0;opacity:{op}" width="1080" height="1920" filter="url(#paint)">
 <g fill="none" stroke="#fff" stroke-width="9">
  <rect x="190" y="260" width="700" height="1420"/><rect x="270" y="260" width="540" height="1420"/>
  <line x1="270" y1="610" x2="810" y2="610"/><line x1="270" y1="1330" x2="810" y2="1330"/>
  <line x1="540" y1="610" x2="540" y2="1330"/><line x1="540" y1="260" x2="540" y2="285"/>
  <line x1="540" y1="1655" x2="540" y2="1680"/></g></svg>"""


def net(y):
    return (f'<div class="abs" style="left:120px;top:{y - 14}px;width:840px;height:28px;'
            f'background:repeating-linear-gradient(90deg,rgba(255,255,255,.55) 0 3px,transparent 3px 12px),#1d1d1d;'
            f'border-top:7px solid #fff;box-shadow:0 10px 18px rgba(0,0,0,.35)"></div>')


def cancha_hook():
    b = f"""
{court(T['superficie'])}{net(970)}
<div class="abs shade"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:420px;font-size:170px;line-height:1">{T['a'].upper()}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:1180px;font-size:170px;line-height:1">{T['b'].upper()}</div>
<div class="abs" style="left:0;width:1080px;text-align:center;top:1440px"><span class="chip" style="font-size:52px">{T['torneo']}, pista {T['superficie']}</span></div>
"""
    return page(b, CANCHA_CSS)


def cancha_prob():
    # La red se desplaza: cada jugador "ocupa" una parte de la cancha igual a su probabilidad
    top, h = 260, 1420
    y_net = top + round(h * T["pa"] / 100)
    b = f"""
{court(T['superficie'], 0.6)}
<div class="abs" style="left:190px;top:{top}px;width:700px;height:{y_net - top}px;
 background:repeating-linear-gradient(135deg,rgba(255,255,255,.14) 0 28px,transparent 28px 56px)"></div>
{net(y_net)}
<div class="abs shade"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:{top + 140}px;font-size:84px">{T['a']}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:{top + 250}px;font-size:260px;line-height:1">{T['pa']}<span style="font-size:120px">%</span></div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:{y_net + 34}px;font-size:96px;line-height:1">{T['b']} {T['pb']}<span style="font-size:56px">%</span></div>
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap" style="text-shadow:0 3px 12px rgba(0,0,0,.6)">la red, corrida <span>hacia {T['b']}</span></div></div>
"""
    return page(b, CANCHA_CSS)


FRAMES = {
    "t1_cuaderno_gancho": cuaderno_hook, "t1_cuaderno_prob": cuaderno_prob,
    "t2_pizarra_gancho": pizarra_hook, "t2_pizarra_prob": pizarra_prob,
    "t3_marcador_gancho": led_hook, "t3_marcador_prob": led_prob,
    "t4_cancha_gancho": cancha_hook, "t4_cancha_prob": cancha_prob,
}


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--allow-file-access-from-files"])
        pg = await b.new_page(viewport={"width": 1080, "height": 1920})
        for name, fn in FRAMES.items():
            html = ROOT / f"{name}.html"
            html.write_text(fn(), encoding="utf-8")
            await pg.goto(f"file://{html}")
            await pg.wait_for_timeout(400)
            await pg.screenshot(path=str(OUT / f"{name}.png"))
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
