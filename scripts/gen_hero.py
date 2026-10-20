"""Generate assets/readme/hero.svg for pocket-desk.

The visual is the project's own material: a 4x4 folder grid (the 16 folders the
engine actually produces) above a 4-slot dock, on a dark editorial canvas.
"""
import random
from pathlib import Path

W, H = 1200, 360
BG = "#0A0C10"
PANEL = "#11151C"
STROKE = "#242A35"
FG = "#F3F5F8"
MUTED = "#8A93A0"
ACCENT = "#FFB35C"
TILE = "#1B212B"
TILE_STROKE = "#2C3441"
DOT = "#55606F"

rng = random.Random(20261020)

# --- panel geometry -------------------------------------------------------
PX, PY, PW, PH = 700, 16, 450, 330
COLS, ROWS = 4, 4
ICON, PITCH = 50, 62
GRID_X = PX + (PW - (COLS * ICON + (COLS - 1) * (PITCH - ICON))) // 2
GRID_Y = 42

parts = []
add = parts.append

add(f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img" aria-labelledby="ttl dsc">
  <title id="ttl">pocket-desk — organize an iPhone home screen from the iPhone itself</title>
  <desc id="dsc">Dark editorial board. Left: the repository name and a one-line
  description. Right: a home-screen mock showing sixteen folders in a four by four
  grid above a four-slot dock, the layout this tool produces from 166 apps.</desc>

  <rect width="{W}" height="{H}" rx="26" fill="{BG}"/>''')

# --- left block: title ----------------------------------------------------
add(f'''
  <g id="title">
    <text x="56" y="86" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
          font-size="16" letter-spacing="3" fill="{MUTED}">iOS · COREDEVICE RSD · 单机</text>

    <text x="54" y="156" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
          font-size="66" font-weight="700" letter-spacing="-1.5" fill="{FG}">pocket-desk</text>

    <text x="56" y="204" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif"
          font-size="25" fill="{FG}">整理 iPhone 桌面，不用电脑。</text>

    <text x="56" y="238" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
          font-size="19" fill="{MUTED}">Organize a home screen from the phone itself.</text>
  </g>''')

# --- left block: metadata -------------------------------------------------
META_Y = 294
items = [("166", "apps"), ("16", "folders"), ("1", "page")]
x = 56
for value, label in items:
    add(f'''
    <text x="{x}" y="{META_Y}" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
          font-size="26" font-weight="700" fill="{ACCENT}">{value}</text>
    <text x="{x}" y="{META_Y + 22}" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
          font-size="14" letter-spacing="1" fill="{MUTED}">{label}</text>''')
    x += 74 if label != "folders" else 94

add(f'''
    <line x1="322" y1="{META_Y - 24}" x2="322" y2="{META_Y + 22}" stroke="{STROKE}" stroke-width="1"/>
    <text x="346" y="{META_Y - 2}" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
          font-size="14" fill="{MUTED}">no cable</text>
    <text x="346" y="{META_Y + 20}" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"
          font-size="14" fill="{MUTED}">no jailbreak</text>''')

# --- right block: home-screen mock ---------------------------------------
add(f'''
  <g id="proof">
    <rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="30"
          fill="{PANEL}" stroke="{STROKE}" stroke-width="1"/>''')

for row in range(ROWS):
    for col in range(COLS):
        tx = GRID_X + col * PITCH
        ty = GRID_Y + row * PITCH
        add(f'''
    <rect x="{tx}" y="{ty}" width="{ICON}" height="{ICON}" rx="13"
          fill="{TILE}" stroke="{TILE_STROKE}" stroke-width="1"/>''')
        # 3x3 mini-raster hinting at folder contents
        for mr in range(3):
            for mc in range(3):
                filled = rng.random() > 0.18
                if not filled:
                    continue
                dx = tx + 14 + mc * 8
                dy = ty + 14 + mr * 8
                color = ACCENT if rng.random() > 0.88 else DOT
                add(f'    <rect x="{dx}" y="{dy}" width="5.5" height="5.5" rx="1.6" fill="{color}"/>')

# dock
DOCK_W, DOCK_H = 250, 42
DOCK_X = PX + (PW - DOCK_W) // 2
DOCK_Y = 284
add(f'''
    <rect x="{DOCK_X}" y="{DOCK_Y}" width="{DOCK_W}" height="{DOCK_H}" rx="21"
          fill="#161B24" stroke="{TILE_STROKE}" stroke-width="1"/>''')
for i in range(4):
    dx = DOCK_X + 22 + i * 54
    add(f'''
    <rect x="{dx}" y="{DOCK_Y + 7}" width="28" height="28" rx="8"
          fill="{TILE}" stroke="{TILE_STROKE}" stroke-width="1"/>''')

# home indicator
add(f'''
    <rect x="{PX + PW // 2 - 34}" y="{PY + PH - 12}" width="68" height="3" rx="1.5"
          fill="#2E3644"/>''')
add('  </g>')

# baseline accent rule under the title block
add(f'''
  <line x1="56" y1="{META_Y + 46}" x2="640" y2="{META_Y + 46}" stroke="{STROKE}" stroke-width="1"/>''')

add('</svg>')

out = Path(__file__).resolve().parent.parent / "assets" / "readme" / "hero.svg"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size} bytes)")
