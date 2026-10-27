#!/usr/bin/env python3
"""Render the Proteus system-overview figure (figs/overview_proteus.png).

Stylistically mirrors the 2025 code-similarity figure: a wide, left-to-right
pastel block pipeline with a bold title, soft-colored rounded boxes, dashed
groupings, arrows, and small document glyphs. Regenerate with:
    python3 figs/make_overview.py

Corrected against the implementation (smart_loader/core/graph.py):

  * The VCA has exactly TWO re-entry targets -- the LSA and the GSA.
    ``verifier_edges = {"axiom": "axiom", "end": END}`` plus ``"connector"``,
    and ``_after_verifier()`` returns only those. The previous figure drew a
    feedback arrow into the LMA as well, which the graph has no edge for.
  * The LMA sits BETWEEN the GSA and the VCA (``verifier_entry =
    "logic_substitution"``), not in parallel with them, and its output is
    gated by the equivalence oracle before the VCA ever sees it -- so a
    rejected mutation is discarded, never repaired.
  * The global oracle is SOURCE-LEVEL and has five layers. The previous
    figure labelled it "binary clone detector, prior work" and described
    execution traces; no binary detector is used anywhere in this work.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- fonts
CAND = {
    "reg":  ["/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-regular.otf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/System/Library/Fonts/Helvetica.ttc",
             "/System/Library/Fonts/Supplemental/Arial.ttf"],
    "bold": ["/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-bold.otf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/System/Library/Fonts/Helvetica.ttc"],
    "ital": ["/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreheros-italic.otf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
             "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
             "/System/Library/Fonts/Helvetica.ttc"],
}


def _find(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


F = {k: _find(v) for k, v in CAND.items()}
_missing = [k for k, v in F.items() if v is None]
if _missing:
    raise SystemExit(
        f"no font found for {_missing}; add a path to CAND for this machine")


def font(kind, size):
    return ImageFont.truetype(F[kind], size)


# ---------------------------------------------------------------- palette
INK      = (35, 35, 35)
ARROW    = (70, 70, 70)
BLUE_F   = (214, 228, 240); BLUE_B   = (86, 122, 160)
GREEN_F  = (176, 204, 84);  GREEN_B  = (120, 150, 52)   # LLM agent boxes
LGREEN_F = (233, 249, 233); LGREEN_B = (140, 185, 140)  # oracle
CREAM_F  = (255, 244, 222); CREAM_B  = (208, 176, 112)  # verification band
WHITE    = (255, 255, 255)

# ---------------------------------------------------------------- canvas
W, H = 3060, 1000
img = Image.new("RGB", (W, H), WHITE)
d = ImageDraw.Draw(img)


def rrect(box, r, fill, outline, width=3, dash=False):
    if not dash:
        d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)
    else:
        if fill:
            d.rounded_rectangle(box, radius=r, fill=fill)
        dash_rrect(box, r, outline, width)


def dash_line(p0, p1, color, width=3, dash=18, gap=12):
    x0, y0 = p0; x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist == 0:
        return
    ux, uy = dx / dist, dy / dist
    n = 0.0
    while n < dist:
        a = (x0 + ux * n, y0 + uy * n)
        b = (x0 + ux * min(n + dash, dist), y0 + uy * min(n + dash, dist))
        d.line([a, b], fill=color, width=width)
        n += dash + gap


def dash_rrect(box, r, color, width=3):
    x0, y0, x1, y1 = box
    dash_line((x0 + r, y0), (x1 - r, y0), color, width)
    dash_line((x0 + r, y1), (x1 - r, y1), color, width)
    dash_line((x0, y0 + r), (x0, y1 - r), color, width)
    dash_line((x1, y0 + r), (x1, y1 - r), color, width)
    d.arc([x0, y0, x0 + 2*r, y0 + 2*r], 180, 270, fill=color, width=width)
    d.arc([x1 - 2*r, y0, x1, y0 + 2*r], 270, 360, fill=color, width=width)
    d.arc([x0, y1 - 2*r, x0 + 2*r, y1], 90, 180, fill=color, width=width)
    d.arc([x1 - 2*r, y1 - 2*r, x1, y1], 0, 90, fill=color, width=width)


def arrow(p0, p1, color=ARROW, width=6, head=20, dash=False):
    if dash:
        dash_line(p0, p1, color, width)
    else:
        d.line([p0, p1], fill=color, width=width)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    for s in (0.5, -0.5):
        d.line([p1, (p1[0] - head*math.cos(ang - s), p1[1] - head*math.sin(ang - s))],
               fill=color, width=width)


def ctext(xy, s, fnt, fill=INK, anchor="mm", align="center"):
    d.multiline_text(xy, s, font=fnt, fill=fill, anchor=anchor, align=align, spacing=8)


def doc(cx, cy, w, h, label, sub=None):
    """A document glyph with a folded corner."""
    x0, y0 = cx - w/2, cy - h/2
    x1, y1 = cx + w/2, cy + h/2
    fold = 26
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)],
              fill=WHITE, outline=(90, 90, 90), width=3)
    d.line([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)],
           fill=(90, 90, 90), width=3)
    for i in range(3):
        yy = y0 + h*0.42 + i*16
        d.line([(x0 + 16, yy), (x1 - 16, yy)], fill=(170, 170, 170), width=3)
    ctext((cx, y1 + 34), label, font("bold", 27))
    if sub:
        ctext((cx, y1 + 70), sub, font("ital", 23), fill=(90, 90, 90))


def repair_glyph(box):
    """A small 'execute & repair' circular arrow in a box's top-right corner."""
    x1, y0 = box[2], box[1]
    cx, cy, r = x1 - 36, y0 + 34, 17
    d.arc([cx-r, cy-r, cx+r, cy+r], 290, 200, fill=WHITE, width=4)
    ang = math.radians(290)
    ex, ey = cx + r*math.cos(ang), cy + r*math.sin(ang)
    d.polygon([(ex, ey), (ex-13, ey-3), (ex-3, ey+12)], fill=WHITE)


# ---------------------------------------------------------------- title
ctext((40, 40), "Proteus: Agentic Construction of Equivalent Program Clones",
      font("bold", 46), anchor="la")

YC = 540  # main pipeline centre line

# ---------------------------------------------------------------- input
doc(345, 375, 150, 96, "")
rrect((70, 450, 620, 640), 18, BLUE_F, BLUE_B, 3)
ctext((345, 500), "Source Program", font("bold", 30))
ctext((345, 540), "+  Requirements", font("bold", 30))
ctext((345, 597), "(safer / faster libraries,\ndiversify logic)",
      font("ital", 22), fill=(80, 80, 80))

# ---------------------------------------------------------------- agents group
grp = (720, 400, 2010, 665)
dash_rrect(grp, 22, (120, 120, 120), 3)
ctext((735, 368), "Transformation Agents (LLM-driven)", font("bold", 28), anchor="la")

agents = [
    (905,  "LSA", "Library-\nSubstitution", "swap deps →\nsafer / faster"),
    (1365, "GSA", "Glue-\nSynthesis",       "adapt program to\nnew interfaces"),
    (1825, "LMA", "Logic-\nMutation",       "rewrite first-party\nlogic (Type-IV)"),
]
BW, BH = 300, 200
for cx, tag, title, sub in agents:
    box = (cx-BW/2, YC-BH/2, cx+BW/2, YC+BH/2)
    rrect(box, 16, GREEN_F, GREEN_B, 3)
    ctext((cx, YC-64), tag, font("bold", 40), fill=(40, 50, 15))
    ctext((cx, YC-8), title, font("bold", 27))
    ctext((cx, YC+58), sub, font("ital", 23), fill=(50, 55, 20))
    repair_glyph(box)
ctext((905, YC-BH/2-24), "execute & repair", font("ital", 21), fill=GREEN_B)

# The LMA's variants are gated by the oracle BEFORE the VCA sees them, which is
# why no repair edge returns to it: a rejected mutation is discarded.
ctext((1825, 706), "variants gated by the oracle \u2014\nrejected, not repaired",
      font("ital", 21), fill=(60, 120, 60))

# ---------------------------------------------------------------- oracle
rrect((2120, 415, 2620, 665), 18, LGREEN_F, LGREEN_B, 3)
ctext((2370, 462), "Equivalence Oracle", font("bold", 32), fill=(30, 90, 30))
ctext((2370, 502), "(five source-level layers)", font("ital", 22), fill=(70, 110, 70))
ctext((2370, 580),
      "imports resolve  •  golden vectors\ninvariants  •  test suite\nfactory execution",
      font("reg", 23))

# ---------------------------------------------------------------- output
doc(2870, YC, 165, 200, "Clone", "+ Provenance Report")

# ---------------------------------------------------------------- VCA band
rrect((720, 745, 2010, 895), 16, CREAM_F, CREAM_B, 3)
ctext((1365, 792), "Verification & Consistency Agent",
      font("bold", 30), fill=(120, 85, 20))
ctext((1365, 845),
      "runs available test cases • confirms equivalence • attributes each regression to the responsible agent",
      font("ital", 24), fill=(110, 85, 40))

# Exactly two re-entry targets, matching _after_verifier() in graph.py.
for cx, label in ((905, "re-select target"), (1365, "repair adapter")):
    arrow((cx, 745), (cx, YC+BH/2+6), color=CREAM_B, width=4, head=16, dash=True)
    ctext((cx, 706), label, font("ital", 21), fill=(140, 100, 30))

# ---------------------------------------------------------------- main flow
arrow((620, YC), (agents[0][0]-BW/2, YC))
arrow((agents[0][0]+BW/2, YC), (agents[1][0]-BW/2, YC))
arrow((agents[1][0]+BW/2, YC), (agents[2][0]-BW/2, YC))
arrow((agents[2][0]+BW/2, YC), (2120, YC))
arrow((2620, YC), (2870-82, YC))

# ---------------------------------------------------------------- tier badges
def badge(x, y, text, col):
    w = d.textlength(text, font=font("bold", 24)) + 28
    rrect((x, y, x+w, y+42), 12, col, None, 0)
    ctext((x+w/2, y+21), text, font("bold", 24), fill=WHITE)


badge(740, 900, "1  Local verification", GREEN_B)
badge(1080, 900, "2  Global verification  (source-level, five layers)", CREAM_B)

os.makedirs("figs", exist_ok=True)
img.save("figs/overview_proteus.png", dpi=(300, 300))
print("wrote figs/overview_proteus.png", img.size)
