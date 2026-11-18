"""Fotogramas de estilo (1080x1920) para 4 plantillas del Short. Render con Chrome (Playwright)."""

import asyncio
import base64
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
FONTS = ROOT / "node_modules/@fontsource"
LOGO = base64.b64encode((ROOT.parents[1] / "src/sports_analytics/video/assets/logo.png").read_bytes()).decode()
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def ff(family, pkg, weight, fname=None):
    fname = fname or f"{pkg}-latin-{weight}-normal.woff2"
    return (
        f"@font-face{{font-family:'{family}';font-weight:{weight};"
        f"src:url('file://{FONTS}/{pkg}/files/{fname}') format('woff2');}}"
    )


FACES = "".join(
    [
        ff("Kalam", "kalam", 400),
        ff("Kalam", "kalam", 700),
        ff("Caveat", "caveat", 400),
        ff("Caveat", "caveat", 700),
        ff("Barlow Condensed", "barlow-condensed", 700),
        ff("Barlow Condensed", "barlow-condensed", 800),
        ff("Oswald", "oswald", 700),
        ff("Archivo", "archivo", 800),
        ff("Doto", "doto", 900),
        ff("Doto", "doto", 700),
    ]
)

# Partido de ejemplo (mismos datos del Short de prueba)
M = dict(home="Atlético de Madrid", home_s="Atlético", away="Barcelona", h=36, d=29, a=35,
         elo_h=1832, elo_a=1851, when="miércoles 19, 3:00 p. m.")

SVG_DEFS = """
<svg width="0" height="0" style="position:absolute">
 <filter id="rough"><feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3"/>
  <feDisplacementMap in="SourceGraphic" scale="7"/></filter>
 <filter id="chalk"><feTurbulence type="fractalNoise" baseFrequency="1.1" numOctaves="2" seed="8" result="n"/>
  <feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 -0.9 1.45" result="m"/>
  <feComposite in="SourceGraphic" in2="m" operator="in" result="c"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.04" numOctaves="1" seed="2" result="w"/>
  <feDisplacementMap in="c" in2="w" scale="3"/></filter>
 <filter id="paint"><feTurbulence type="fractalNoise" baseFrequency="0.6" numOctaves="2" seed="5" result="n"/>
  <feColorMatrix in="n" type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 -0.9 1.15" result="m"/>
  <feComposite in="SourceGraphic" in2="m" operator="in"/></filter>
 <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="3" seed="11"/>
  <feColorMatrix type="saturate" values="0"/></filter>
</svg>"""


def page(body, css):
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{FACES}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:1080px;height:1920px;overflow:hidden}}
body{{position:relative}}
.abs{{position:absolute}}
.grain{{position:absolute;inset:0;pointer-events:none}}
{css}
</style></head><body>{SVG_DEFS}{body}</body></html>"""


LOGO_IMG = f'<img src="data:image/png;base64,{LOGO}" style="width:100%;height:100%;border-radius:50%;display:block">'

# ------------------------------------------------------------------ 1. Cuaderno
CUADERNO_CSS = """
body{background:#F7F9FB;
 background-image:linear-gradient(#C9DCEE 2px,transparent 2px),linear-gradient(90deg,#C9DCEE 2px,transparent 2px);
 background-size:54px 54px;background-position:0 20px}
.margin{left:150px;top:0;width:3px;height:1920px;background:#E46A6A;box-shadow:8px 0 0 #E46A6A55}
.pen{font-family:Kalam;color:#2342A8}
.pencil{font-family:Caveat;color:#5B6068}
.hl{background:linear-gradient(transparent 18%,#F4EE4A 18%,#F4EE4A 88%,transparent 88%);
 mix-blend-mode:multiply;padding:0 10px}
.postit{background:#FFE36E;box-shadow:0 18px 30px -12px rgba(40,40,20,.35);}
.tape{width:180px;height:56px;background:rgba(255,255,255,.55);border:1px solid rgba(0,0,0,.05)}
.cap{font-family:Archivo;font-weight:800;font-size:54px;color:#111;line-height:1.25}
.cap span{background:linear-gradient(transparent 12%,#B8F28E 12%,#B8F28E 90%,transparent 90%);padding:0 8px}
"""


def cuaderno_hook():
    b = f"""
<div class="abs margin"></div>
<div class="abs" style="left:190px;top:120px;width:120px;height:120px;transform:rotate(-7deg)">{LOGO_IMG}</div>
<div class="abs pencil" style="left:330px;top:150px;font-size:48px">JC Probabilidades</div>
<div class="abs pen" style="left:190px;top:330px;width:820px;font-size:76px;line-height:1.15;font-weight:700">
 Atlético vs Barça:<br><span class="hl">ni el modelo se decide</span></div>
<div class="abs pen" style="left:190px;top:640px;font-size:104px;font-weight:700;transform:rotate(-2deg)">Atlético de Madrid</div>
<svg class="abs" style="left:420px;top:770px" width="210" height="150" filter="url(#rough)">
 <ellipse cx="105" cy="75" rx="92" ry="58" fill="none" stroke="#C83A3A" stroke-width="7"/></svg>
<div class="abs pen" style="left:478px;top:790px;font-size:80px;color:#C83A3A">vs</div>
<div class="abs pen" style="left:300px;top:910px;font-size:120px;font-weight:700;transform:rotate(1.5deg)">Barcelona</div>
<div class="abs postit" style="left:300px;top:1110px;width:560px;height:290px;transform:rotate(3deg);padding:46px 40px">
 <div class="abs tape" style="left:190px;top:-26px;transform:rotate(-4deg)"></div>
 <div class="pen" style="font-size:44px;color:#3A3A3A">el modelo dice:</div>
 <div class="pen" style="font-size:86px;font-weight:700;line-height:1.1">36 / 29 / 35</div>
 <div class="pencil" style="font-size:44px">local, empate, visitante</div></div>
<div class="abs pencil" style="left:190px;top:1450px;font-size:50px;transform:rotate(-1deg)">{M['when']}</div>
"""
    return page(b, CUADERNO_CSS)


def cuaderno_probs():
    rows = [("Atlético", M["h"]), ("Empate", M["d"]), ("Barcelona", M["a"])]
    bars = ""
    for i, (lab, p) in enumerate(rows):
        y = 470 + i * 215
        w = p * 14
        bars += f"""
<div class="abs pen" style="left:190px;top:{y}px;font-size:60px;font-weight:700">{lab}</div>
<div class="abs" style="left:190px;top:{y + 92}px;width:{w}px;height:84px;border:6px solid #2342A8;border-radius:6px 14px 8px 12px;
 background:repeating-linear-gradient(-45deg,#2342A8 0 5px,transparent 5px 18px);filter:url(#rough)"></div>
<div class="abs pen" style="left:{220 + w}px;top:{y + 70}px;font-size:96px;font-weight:700">{p}%</div>"""
    b = f"""
<div class="abs margin"></div>
<div class="abs" style="left:190px;top:120px;width:96px;height:96px;transform:rotate(-7deg)">{LOGO_IMG}</div>
<div class="abs pen" style="left:190px;top:270px;font-size:92px;font-weight:700">¿Quién gana?</div>
<svg class="abs" style="left:186px;top:395px" width="520" height="40" filter="url(#rough)">
 <path d="M5 22 Q 60 2 120 20 T 240 20 T 360 20 T 500 18" fill="none" stroke="#2342A8" stroke-width="6"/></svg>
{bars}

<div class="abs pen" style="left:190px;top:1170px;font-size:60px;color:#C83A3A;transform:rotate(-2deg)">solo 7 puntos de diferencia</div>
<svg class="abs" style="left:186px;top:1250px" width="760" height="30" filter="url(#rough)"><path d="M4 16 C 200 4, 420 26, 740 10" stroke="#C83A3A" stroke-width="5" fill="none"/></svg>
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap">el modelo le da <span>36 por ciento</span> al Atlético</div></div>
"""
    return page(b, CUADERNO_CSS)


# ------------------------------------------------------------------ 2. Pizarra
PIZARRA_CSS = """
body{background:#24352C}
.board{inset:0;background:
 radial-gradient(ellipse 520px 260px at 300px 1500px,rgba(255,255,255,.07),transparent 70%),
 radial-gradient(ellipse 600px 300px at 820px 380px,rgba(255,255,255,.06),transparent 70%),
 radial-gradient(ellipse 900px 700px at 540px 960px,#2C4035,#1D2B24)}
.ch{font-family:Caveat;font-weight:700;color:#EEF1EA;filter:url(#chalk)}
.yel{color:#F3D77F}.red{color:#EE9384}
.magnet{border-radius:50%;box-shadow:0 14px 18px rgba(0,0,0,.45), inset 0 -10px 18px rgba(0,0,0,.25), inset 0 10px 16px rgba(255,255,255,.35)}
.cap{font-family:Archivo;font-weight:800;font-size:54px;color:#fff;line-height:1.25}
.cap span{color:#F3D77F}
"""


def pizarra_hook():
    b = f"""
<div class="abs board"></div>
<svg class="abs" style="left:0;top:0;opacity:.5" width="1080" height="1920" filter="url(#chalk)">
 <g fill="none" stroke="#EEF1EA" stroke-width="7">
  <rect x="90" y="560" width="900" height="1000" rx="4"/>
  <line x1="90" y1="1060" x2="990" y2="1060"/>
  <circle cx="540" cy="1060" r="130"/>
  <rect x="330" y="560" width="420" height="190"/><rect x="330" y="1370" width="420" height="190"/></g></svg>
<div class="abs" style="left:60px;top:110px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs ch" style="left:200px;top:115px;font-size:60px">la pizarra de mañana</div>
<div class="abs ch" style="left:80px;top:250px;font-size:150px;line-height:1">Partido abierto</div>
<svg class="abs" style="left:90px;top:410px" width="760" height="60" filter="url(#chalk)">
 <path d="M8 34 C 180 10, 420 50, 750 22" stroke="#F3D77F" stroke-width="12" fill="none" stroke-linecap="round"/></svg>
<div class="abs magnet" style="left:300px;top:760px;width:190px;height:190px;background:#D2343C"></div>
<div class="abs ch" style="left:510px;top:790px;font-size:96px">Atlético</div>
<div class="abs magnet" style="left:590px;top:1180px;width:190px;height:190px;background:#2C5DB5"></div>
<div class="abs ch" style="left:150px;top:1205px;font-size:96px">Barcelona</div>
<svg class="abs" style="left:0;top:0" width="1080" height="1920" filter="url(#chalk)">
 <path d="M420 960 C 470 1070, 560 1110, 640 1170" stroke="#EEF1EA" stroke-width="8" fill="none" stroke-dasharray="26 18"/>
 <path d="M640 1170 l-44 -6 M640 1170 l-14 -40" stroke="#EEF1EA" stroke-width="8"/>
 <text x="760" y="1080" font-family="Caveat" font-weight="700" font-size="120" fill="#EE9384">X</text>
 <text x="200" y="1010" font-family="Caveat" font-weight="700" font-size="120" fill="#EE9384">O</text></svg>
<div class="abs ch yel" style="left:240px;top:1430px;font-size:76px">{M['when']}</div>
"""
    return page(b, PIZARRA_CSS)


def pizarra_probs():
    rows = [("Atlético", M["h"], "#EEF1EA"), ("Empate", M["d"], "#EEF1EA"), ("Barcelona", M["a"], "#EEF1EA")]
    s = ""
    for i, (lab, p, col) in enumerate(rows):
        y = 430 + i * 250
        w = p * 15
        col = "#F3D77F" if p == max(r[1] for r in rows) else col
        s += f"""
<div class="abs ch" style="left:90px;top:{y}px;font-size:84px">{lab}</div>
<div class="abs" style="left:90px;top:{y + 110}px;width:{w}px;height:80px;filter:url(#chalk);
 background:repeating-linear-gradient(100deg,{col} 0 9px,transparent 9px 15px)"></div>
<div class="abs ch" style="left:{120 + w}px;top:{y + 70}px;font-size:130px;color:{col}">{p}%</div>"""
    b = f"""
<div class="abs board"></div>
<div class="abs" style="left:60px;top:110px;width:96px;height:96px">{LOGO_IMG}</div>
<div class="abs ch" style="left:90px;top:255px;font-size:120px">¿Quién gana?</div>
{s}
<svg class="abs" style="left:560px;top:1120px" width="440" height="230" filter="url(#chalk)">
 <ellipse cx="220" cy="115" rx="200" ry="95" stroke="#EE9384" stroke-width="9" fill="none"/></svg>
<div class="abs ch red" style="left:620px;top:1165px;font-size:70px;width:340px;line-height:1;text-align:center">7 puntos<br>de diferencia</div>
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap">el modelo le da <span>36 por ciento</span> al Atlético</div></div>
"""
    return page(b, PIZARRA_CSS)


# ------------------------------------------------------------------ 3. Marcador LED
LED_CSS = """
body{background:#141516}
.housing{inset:0;background:linear-gradient(90deg,#1b1c1e,#111213 50%,#1b1c1e);}
.panel{left:40px;top:170px;width:1000px;height:1380px;background-color:#070708;border-radius:10px;
 box-shadow:inset 0 0 0 10px #2a2b2d, inset 0 0 40px #000;
 background-image:radial-gradient(circle,#25272a 3.2px,transparent 3.8px);background-size:16px 16px;background-position:4px 4px}
.led{font-family:Doto;font-weight:900;color:#FFB21E;text-shadow:0 0 10px rgba(255,170,20,.75),0 0 26px rgba(255,140,0,.35);letter-spacing:4px}
.w{color:#FFF4DA;text-shadow:0 0 10px rgba(255,240,210,.7),0 0 24px rgba(255,220,170,.3)}
.r{color:#FF4B3E;text-shadow:0 0 10px rgba(255,70,50,.75)}
.dot{width:20px;height:20px;border-radius:50%}
.on{background:#FFB21E;box-shadow:0 0 8px #FFB21E,0 0 18px rgba(255,150,0,.5)}
.onw{background:#FFF4DA;box-shadow:0 0 8px #FFF4DA}
.off{background:#25272a}
.cap{font-family:Archivo;font-weight:800;font-size:54px;color:#fff;line-height:1.25}
.cap span{color:#FFB21E}
"""


def led_hook():
    b = f"""
<div class="abs housing"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs panel"></div>
<div class="abs led r" style="left:110px;top:240px;font-size:58px">PARTIDO ABIERTO</div>
<div class="abs led w" style="left:110px;top:330px;font-size:58px">NADIE PASA DEL 36%</div>
<div class="abs led r" style="left:110px;top:560px;font-size:46px">LOCAL</div>
<div class="abs led" style="left:110px;top:620px;font-size:148px;line-height:1">ATLÉTICO</div>
<div class="abs led w" style="left:470px;top:840px;font-size:110px">VS</div>
<div class="abs led r" style="left:110px;top:1010px;font-size:46px">VISITA</div>
<div class="abs led" style="left:110px;top:1070px;font-size:148px;line-height:1">BARCELONA</div>
<div class="abs led w" style="left:110px;top:1380px;font-size:64px">MIÉ 19  15:00</div>
"""
    return page(b, LED_CSS)


def led_probs():
    cols = [("LOC", M["h"]), ("EMP", M["d"]), ("VIS", M["a"])]
    s = ""
    rows = 26
    for i, (lab, p) in enumerate(cols):
        x0 = 150 + i * 290
        lit = round(p / 50 * rows)
        cls = "onw" if p != max(c[1] for c in cols) else "on"
        for c in range(6):
            for r in range(rows):
                state = cls if r >= rows - lit else "off"
                s += f'<div class="abs dot {state}" style="left:{x0 + c * 30}px;top:{540 + r * 28}px"></div>'
        s += f'<div class="abs led {"w" if cls=="onw" else ""}" style="left:{x0 - 6}px;top:380px;font-size:120px">{p}</div>'
        s += f'<div class="abs led r" style="left:{x0 + 18}px;top:1290px;font-size:54px">{lab}</div>'
    b = f"""
<div class="abs housing"></div>
<div class="abs" style="left:485px;top:40px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs panel"></div>
<div class="abs led w" style="left:110px;top:240px;font-size:58px">¿QUIÉN GANA?</div>
{s}
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap">el modelo le da <span>36 por ciento</span> al Atlético</div></div>
"""
    return page(b, LED_CSS)


# ------------------------------------------------------------------ 4. Cancha
CANCHA_CSS = """
body{background:#2E7A36}
.grass{inset:0;background:repeating-linear-gradient(180deg,#2F8139 0 160px,#2A7432 160px 320px)}
.shade{inset:0;background:radial-gradient(ellipse 900px 1300px at 50% 45%,transparent 55%,rgba(0,0,0,.28))}
.paint{font-family:Oswald;font-weight:700;color:rgba(255,255,255,.92);filter:url(#paint);letter-spacing:2px}
.chip{font-family:'Barlow Condensed';font-weight:800;color:#0F3D17;background:#E9F5E4;border-radius:6px;padding:6px 22px}
.cap{font-family:Archivo;font-weight:800;font-size:54px;color:#fff;line-height:1.25}
.cap span{color:#FFE45C}
"""


def field_lines(op=0.85):
    return f"""<svg class="abs" style="left:0;top:0;opacity:{op}" width="1080" height="1920" filter="url(#paint)">
 <g fill="none" stroke="#fff" stroke-width="9">
  <rect x="70" y="140" width="940" height="1640"/><line x1="70" y1="960" x2="1010" y2="960"/>
  <circle cx="540" cy="960" r="150"/><circle cx="540" cy="960" r="9" fill="#fff"/>
  <rect x="290" y="140" width="500" height="250"/><rect x="290" y="1530" width="500" height="250"/>
  <rect x="420" y="140" width="240" height="90"/><rect x="420" y="1690" width="240" height="90"/></g></svg>"""


def cancha_hook():
    b = f"""
<div class="abs grass"></div><div class="abs" style="inset:0;filter:url(#grain);opacity:.12"></div>
{field_lines()}<div class="abs shade"></div>
<div class="abs" style="left:485px;top:30px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:420px;font-size:150px;line-height:1">ATLÉTICO</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:590px;font-size:56px;opacity:.9">de Madrid</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:905px;font-size:90px">vs</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:1180px;font-size:150px;line-height:1">BARCELONA</div>
<div class="abs" style="left:0;width:1080px;text-align:center;top:1390px"><span class="chip" style="font-size:52px">Nadie pasa del 36 %</span></div>
<div class="abs" style="left:0;width:1080px;text-align:center;top:1480px"><span class="paint" style="font-size:46px">{M['when']}</span></div>
"""
    return page(b, CANCHA_CSS)


def cancha_probs():
    lanes = [("Atlético", M["h"], "#D2343C"), ("Empate", M["d"], "#E9F5E4"), ("Barcelona", M["a"], "#2C5DB5")]
    x, top, h = 70, 300, 1000
    out = ""
    for k, (lab, p, flag) in enumerate(lanes):
        w = round(940 * p / 100)
        fav = p == max(l[1] for l in lanes)
        stripes = "background:repeating-linear-gradient(135deg,rgba(255,255,255,.13) 0 26px,transparent 26px 52px);" if fav else ""
        out += f"""
<div class="abs" style="left:{x}px;top:{top}px;width:{w}px;height:{h}px;{stripes}"></div>
<div class="abs" style="left:{x + 18}px;top:{top + 18}px;width:{w - 36}px;height:14px;background:{flag};border-radius:3px"></div>
<div class="abs paint" style="left:{x}px;width:{w}px;text-align:center;top:{top + 70}px;font-size:{52 if len(lab) < 9 else 46}px">{lab}</div>
<div class="abs paint" style="left:{x}px;width:{w}px;text-align:center;top:{top + 400}px;font-size:140px;line-height:1">{p}<span style="font-size:70px">%</span></div>"""
        x += w
    sep = "".join(
        f'<div class="abs" style="left:{70 + round(940 * c / 100) - 6}px;top:{top}px;width:12px;height:{h}px;'
        f'background:repeating-linear-gradient(180deg,#fff 0 40px,transparent 40px 64px);filter:url(#paint)"></div>'
        for c in (M["h"], M["h"] + M["d"]))
    b = f"""
<div class="abs grass"></div><div class="abs" style="inset:0;filter:url(#grain);opacity:.12"></div>
{field_lines(0.35)}<div class="abs shade"></div>
<div class="abs" style="left:485px;top:30px;width:110px;height:110px">{LOGO_IMG}</div>
<div class="abs paint" style="left:0;width:1080px;text-align:center;top:170px;font-size:84px">¿Quién gana?</div>
{out}{sep}
<div class="abs" style="left:110px;top:1395px;width:860px;text-align:center"><div class="cap" style="text-shadow:0 3px 12px rgba(0,0,0,.6)">el campo, repartido <span>casi en tres</span></div></div>
"""
    return page(b, CANCHA_CSS)


FRAMES = {
    "1_cuaderno_gancho": cuaderno_hook, "1_cuaderno_prob": cuaderno_probs,
    "2_pizarra_gancho": pizarra_hook, "2_pizarra_prob": pizarra_probs,
    "3_marcador_gancho": led_hook, "3_marcador_prob": led_probs,
    "4_cancha_gancho": cancha_hook, "4_cancha_prob": cancha_probs,
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
