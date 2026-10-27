"""Gera o ícone da janela Configurações do RealGuard em vários tamanhos."""
from math import cos, pi, sin
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "web" / "brand"
SCALE = 4


def point(xy):
    return tuple(round(v * SCALE) for v in xy)


def gear(draw, cx, cy, radius, teeth=10, rotation=0.0):
    outer, root = radius, radius * 0.77
    vertices = []
    for index in range(teeth):
        angle = rotation + index * 2 * pi / teeth
        for offset, distance in ((-0.46, root), (-0.29, outer), (0.29, outer), (0.46, root)):
            theta = angle + offset * pi / teeth
            vertices.append(point((cx + distance * cos(theta), cy + distance * sin(theta))))
    draw.polygon(vertices, fill=(224, 183, 95, 255))
    inner = radius * 0.36
    draw.ellipse((*point((cx - inner, cy - inner)), *point((cx + inner, cy + inner))),
                 fill=(11, 26, 45, 255))
    axle = radius * 0.14
    draw.ellipse((*point((cx - axle, cy - axle)), *point((cx + axle, cy + axle))),
                 fill=(240, 207, 136, 255))


def stroke(draw, coords, color, width):
    pts = [point(xy) for xy in coords]
    draw.line(pts, fill=color, width=round(width * SCALE), joint="curve")
    r = width * SCALE / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse((round(x-r), round(y-r), round(x+r), round(y+r)), fill=color)


im = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
draw = ImageDraw.Draw(im)
draw.rounded_rectangle((*point((5, 5)), *point((251, 251))), radius=48*SCALE,
                       fill=(10, 25, 43, 255), outline=(47, 66, 89, 255), width=3*SCALE)

# As engrenagens ficam atrás do escudo para preservar o símbolo original.
gear(draw, 204, 76, 29, teeth=9, rotation=0.08)
gear(draw, 198, 182, 54, teeth=11, rotation=0.14)

silver = (220, 228, 238, 255)
gold = (223, 183, 100, 255)
# Silhueta do escudo do RealGuard, ampliada para manter leitura na barra de tarefas.
stroke(draw, [(31, 110), (31, 61), (34, 51), (41, 44), (93, 25), (100, 23),
              (107, 25), (158, 44), (166, 51), (169, 61), (169, 110)], silver, 10)
stroke(draw, [(31, 131), (31, 166), (36, 182), (47, 197), (63, 210),
              (82, 222), (100, 231), (118, 222), (137, 210), (153, 197),
              (164, 182), (169, 166), (169, 131)], silver, 10)
draw.ellipse((*point((81, 82)), *point((119, 120))), fill=gold)
draw.pieslice((*point((59, 114)), *point((141, 202))), 180, 360, fill=gold)
draw.rounded_rectangle((*point((59, 157)), *point((141, 190))), radius=5*SCALE, fill=gold)

out = BRAND / "realguard-config.ico"
im.save(out, format="ICO", sizes=[(16, 16), (20, 20), (24, 24), (32, 32),
                                  (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
im.resize((256, 256), Image.Resampling.LANCZOS).save(BRAND / "realguard-config.png")
print(out)
