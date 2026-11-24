"""Visual style — Sushi's design language, ported to Sushi.

Lifted from `D:\\Ammar\\typing_avo` (the the earlier design / Whisvo desktop app): the
same cream-and-paper palette, the same olive accent, the same generous radii,
the same serif display face over a plain system sans. Where the earlier design's web layer
could use a CSS custom property there is a constant here with the same role.

Three things the earlier design's HTML layer gets for free and Qt does not:

- **Hairlines.** the earlier design draws every border as `box-shadow: inset 0 0 0 .5px`, which
  Qt Style Sheets have no equivalent for. They become 1px borders in the same
  colour; at Windows' usual scaling the difference is not visible.
- **Tracking.** the earlier design tracks almost all type at -.02em. QSS has no
  `letter-spacing`, so tracking is applied through QFont in :func:`display_font`
  and friends, and the helpers in ``components.py`` use them.
- **The display face.** the earlier design sets titles in Fraunces, which is not installed on
  Windows. :func:`ui.fonts.load_fonts` registers it if the file is dropped into
  ``sushi/assets/fonts``; without it the stack falls back to Georgia, which is
  metrically close enough that the layout holds.

Legacy names (``LIME``, ``DEEP``, ``TINT_PINK``…) are kept because pages import
them directly. They now resolve to this palette.
"""
from __future__ import annotations

from PyQt6.QtGui import QFont

# --- palette (from the earlier design) ---------------------------------------------

BG = "#F3F2F2"            # paper — the app background
SURFACE = "#F7F6F6"       # cards
SURFACE_2 = "#EAE9E9"     # sunken fills, segmented tracks, hover
SURFACE_3 = "#E1DFDF"     # deepest neutral fill
LINE = "#EAE7E7"          # hairline
LINE_2 = "#D7D3D3"        # hairline, one step stronger (card edges)
LINE_3 = "#BAB6B6"        # keycaps, pressed edges

INK = "#201F1D"           # body text
MUTED = "#6E6A6A"         # secondary text
FAINT = "#9B9797"         # tertiary text / captions

# The one saturated colour: the coral the mascot is painted in, so the app
# and its mark are the same colour rather than two that happen to share a
# screen. Warmed half a step, towards the orange end of coral.
#
# Worth knowing rather than discovering: ACCENT_INK on this is 3.79:1. That
# clears WCAG AA for large or bold text, which is the only way the accent is
# ever used as a ground — buttons and chips, 600 weight — and not the 4.5:1
# normal-text bar. The coral before it was 4.30:1 and also under, despite a
# comment here claiming otherwise. #B5553A would clear it outright if the
# accent ever carries ordinary text.
ACCENT = "#B68235"        # gold — the wireframe's one accent
ACCENT_HOVER = "#9B7232"
ACCENT_SOFT = "#FFF3E4"   # coral wash behind selected nav, pills
ACCENT_LINE = "#F5CD96"   # the wash's edge
ACCENT_DEEP = "#7D5411"   # text on the wash
ACCENT_INK = "#FFFDF8"    # text on solid accent

# Insane mode's "hot" and every error. Pushed deeper than it was now the
# accent is also warm: at #C6462F the two were a shade apart, and "armed"
# has to be legible as *not* simply the accent again.
REC = "#9B7232"
REC_SOFT = "#FFF3E4"

# The hero band. Coral first, which had the dialogs reading as warnings — a
# red panel is a thing the eye is trained to treat as bad news, and "Apply
# with Sushi?" is not. Then blue, which fixed that and introduced a worse
# problem: a cool, high-chroma colour next to warm earth tones pulls the eye
# like a foreign object, and every other surface in this app is warm.
#
# Olive-charcoal instead. Dark and grounded like the blue, warm like the
# cream it sits on, and a neighbour of the accent on the wheel rather than
# its opposite — so the orange and the band share a screen instead of
# competing for it.
HERO_FROM = "#3A3836"
HERO_MID = "#2D2B2B"
HERO_TO = "#201F1D"

# Ink for anything sitting on that band. The rule has been the same through
# olive, clay and blue: stay in the ground's own family, because the
# alternative is the muddy seam you get where two hues meet. The ground is
# a warm neutral again, so these are warm again — and because the band is
# dark, all three clear 4.5:1 over even its lightest end (6.77, 5.96, 4.70),
# which the set used on clay did not: SKY_INK_SOFT was 3.07:1 there and the
# "Back a step" link paid for it.
SKY_INK = "#FFFDF8"       # titles, the brand, anything that leads
SKY_INK_BODY = "#F1EEEA"  # a paragraph of lead, a link at rest
SKY_INK_SOFT = "#D7D3D3"  # eyebrows, captions, window glyphs

# Card / icon-tile tints. The earlier design gives each mode a hue and keeps the card white.
TINT_GREEN = "#FFF3E4"    # the accent tint; the name is load-bearing
TINT_CLAY = "#F1EFEE"
TINT_BLUE = "#EEEDEC"
TINT_SAND = "#F6F0E4"
TINT_ROSE = "#F3EEEA"
# ink that reads on each tint
TINT_GREEN_INK = ACCENT   # it *is* the accent; spelled out, it drifted
TINT_CLAY_INK = "#5E5A5A"
TINT_BLUE_INK = "#5E5A5A"
TINT_SAND_INK = "#7D5411"
TINT_ROSE_INK = "#6E5A50"

# Sidebar group grounds — the earlier design tints each nav group a different warm neutral.
GROUP_OLIVE = "#F7F5F2"
GROUP_OLIVE_LINE = "#E6E2DC"
GROUP_SAND = "#F7F5F2"
GROUP_SAND_LINE = "#E6E2DC"
GROUP_CLAY = "#F7F5F2"
GROUP_CLAY_LINE = "#E6E2DC"

# Sage: the quiet half of the pairing. Green sits beside orange on the
# wheel, so it supports the accent instead of arguing with it — success
# dots, counters, the badges that are not asking for anything. It is a
# *fill*, not an ink: on cream it is 2.57:1, which is a shape you can see
# and not a word you can read. GOOD is the same family taken down until it
# can be read (4.66:1), so a tick and the word beside it match.
SAGE = "#87A878"          # dots and bars; the in-page pill mirrors it
GOOD = "#5C7A4F"          # success *text*
WARN = "#A8761A"
BAD = "#A62A1A"

# --- geometry --------------------------------------------------------------

R_CARD = 4       # the big white cards
R_PANEL = 4      # panels nested inside a card
R_TILE = 4       # icon tiles, group grounds, inputs
R_BTN = 3        # buttons
R_CHIP = 3        # chips, small actions, keycaps
# A pill is "as round as the box is tall", which on the web is `border-radius:
# 999px` and in Qt is nothing at all: a radius of half the height or more is
# dropped on the floor, and the widget is painted square. That is why every
# pill in this app — the hero's pair, the credit badge, the door's Open Sushi
# button — has been a rectangle. So the radius is stated per control height,
# just under half, and anything new that wants a pill picks the one matching
# its own height rather than reaching for a big number.
R_PILL = 4       # a standard H_CONTROL (40px) button or frame
R_PILL_SM = 3    # a label-sized badge, ~26px tall
R_PILL_LG = 4    # the door's button, 54px tall

# --- the spacing scale ------------------------------------------------------
#
# One scale, four steps, used everywhere a gap is chosen. Before this each
# page picked its own numbers — 9, 11, 12, 14, 16, 18, 20, 22, 24, 28 all
# appeared as "a gap between two things" — and the result is what reads as
# asymmetry: nothing lines up across pages because nothing was measured
# against the same ruler.
GAP_XS = 6        # between a label and the control it names
GAP_SM = 10       # between siblings in a row
GAP_MD = 16       # between blocks inside a card
GAP_LG = 24       # between sections of a page
GAP_XL = 32       # between a page's major bands

PAGE_PAD = 28     # a page's own margin, all four sides
CARD_PAD = 22     # inside a card, all four sides
#: The gutter inside a table cell — and, necessarily, inside the header
#: section above it, or the column heading and its column do not start at the
#: same x. 18 was generous enough that a six-column table spent 216px of a
#: 788px viewport on gutters, which is where the width for the role column
#: went.
CELL_PAD = 14

# --- control metrics --------------------------------------------------------
#
# Every control that can sit beside another control is the same height. This
# is the whole of the "symmetry" problem in one constant: a 13px button next
# to a 16px input next to a 13px chip came out 37, 42 and 33 pixels tall, so
# any row holding more than one kind of control stepped up and down across
# the screen. The padding below is chosen to land on these.
H_CONTROL = 40    # buttons, inputs, combos, the search field
H_CHIP = 32       # filter chips and segmented options — one step down, on purpose
H_TILE = 24       # the icon tile on a nav row

#: Vertical padding that puts a SIZE_SMALL control at H_CONTROL, allowing
#: 1px of border on each side. Line box for 13px Segoe is ~18px.
_PAD_CONTROL = 9
_PAD_CHIP = 7

# --- type ------------------------------------------------------------------

# Qt Style Sheets take a *single* family for `font-family`. A CSS-style stack
# is not merely ignored — Qt fails to resolve the whole declaration and falls
# back to a face with no glyphs, which renders the entire app as tofu boxes.
# So the fallback chain lives in these lists, which QFont.setFamilies does
# honour, and `resolve_families` picks the one real name the stylesheet gets.
DISPLAY_FAMILIES = ["Cormorant Garamond", "Fraunces", "Source Serif 4", "Georgia", "Times New Roman"]
# Deliberately *not* "Segoe UI Variable Text". Windows lists it, and asking
# for it by name resolves fine — but routed through a stylesheet Qt maps it to
# the internal face "Segoe UI Variable 65" and renders Tahoma, which is wider
# and heavier and was quietly the body face of the whole app. Plain Segoe UI
# ships with every Windows since Vista and is what the earlier design's own stack falls back
# to anyway.
BODY_FAMILIES = ["Lora", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]
# Metadata — "via Greenhouse", a salary band, "autonomous v3.4", the model
# line under the chat — is set in mono. It is the design's way of marking the
# difference between prose the app wrote and facts it is reporting.
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono",
                 "DejaVu Sans Mono", "Courier New"]

#: The single family name interpolated into the stylesheet. Replaced at
#: startup by whichever of the lists above is actually installed.
DISPLAY_FONT = "Georgia"
BODY_FONT = "Segoe UI"
MONO_FONT = "Consolas"

SIZE_MICRO = 11
SIZE_SMALL = 13
SIZE_BODY = 16      # The earlier design's body size — larger than a typical desktop app
SIZE_H3 = 16
SIZE_H2 = 20
SIZE_H1 = 28        # the Fraunces title size used on every screen
SIZE_HERO = 25

# The earlier design tracks everything at -.02em; Qt wants a percentage of the font size.
_TRACK_TIGHT = 97
_TRACK_DISPLAY = 98


def display_font(size: int = SIZE_H1, weight: int = 400) -> QFont:
    """The Fraunces title face. Light weight, tight tracking — never bold."""
    f = QFont()
    f.setFamilies(DISPLAY_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, _TRACK_DISPLAY)
    return f


def body_font(size: int = SIZE_BODY, weight: int = 400) -> QFont:
    f = QFont()
    f.setFamilies(BODY_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, _TRACK_TIGHT)
    return f


def _first_installed(families: list[str], fallback: str) -> str:
    """The first family on the list the system can actually *render*.

    Membership in ``QFontDatabase.families()`` is not enough. Windows lists
    "Segoe UI Variable Text" there, but asking for it hands back Tahoma — so
    the app spent its first build rendering in a face nobody chose, wider and
    heavier than intended, which is most of what "the proportions feel off"
    turned out to be. QFontInfo reports what Qt would really use, so a family
    only counts if it resolves to itself.
    """
    try:
        from PyQt6.QtGui import QFont, QFontDatabase, QFontInfo

        installed = {f.lower() for f in QFontDatabase.families()}
        for name in families:
            if name.lower() not in installed:
                continue
            if QFontInfo(QFont(name)).family().lower() == name.lower():
                return name
    except Exception:
        pass
    return fallback


def resolve_families(display_override: str = "") -> tuple[str, str]:
    """Settle the two family names the stylesheet will use."""
    global DISPLAY_FONT, BODY_FONT
    pool = ([display_override] + DISPLAY_FAMILIES) if display_override else DISPLAY_FAMILIES
    global MONO_FONT
    DISPLAY_FONT = _first_installed(pool, "Georgia")
    BODY_FONT = _first_installed(BODY_FAMILIES, "Segoe UI")
    MONO_FONT = _first_installed(MONO_FAMILIES, "Consolas")
    return DISPLAY_FONT, BODY_FONT


def eyebrow_font(size: int = SIZE_SMALL) -> QFont:
    """The small 590-weight label the earlier design puts above every title."""
    return body_font(size, 600)


def mono_font(size: int = SIZE_SMALL, weight: int = 400) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    return f


def on_sky(size: int, colour: str, weight: int = 400, display: bool = False,
           extra: str = "") -> str:
    """A stylesheet for one label sitting on a starfield.

    Not a convenience — a correction. The moment a widget gets a stylesheet
    of its own, Qt resolves its font through the stylesheet cascade and
    discards whatever ``setFont`` put there. Every label on a sky needs a
    stylesheet (for the transparent background and the cream ink), so every
    label on a sky silently loses its size and its face: a 42px Fraunces
    title renders as 13px Segoe. Setting the font *in the same rule* is the
    only thing that holds.
    """
    family = DISPLAY_FONT if display else BODY_FONT
    return (f"background:transparent;color:{colour};"
            f"font-family:'{family}';font-size:{size}px;font-weight:{weight};"
            f"{extra}")


# --- generated subcontrol arrows -------------------------------------------
#
# Qt draws QComboBox::down-arrow and the QSpinBox buttons from an `image:`.
# Style the sub-control without supplying one and the native arrow disappears
# — a combo becomes indistinguishable from a text field, which is how the
# provider picker shipped looking like a read-only label. The CSS
# border-triangle trick does not work here either: Qt renders the zero-sized
# box as a filled square.
#
# So the three arrows are painted once at startup into the app's data dir and
# referenced by path. Generated, not shipped: nothing to keep in the repo, and
# they follow the palette if it ever changes.

_ARROW_DIR = None


def _arrow_paths() -> dict[str, str]:
    """Write the arrow PNGs if they are not already there; return their paths."""
    global _ARROW_DIR
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPolygonF

    from ..config import DATA_DIR

    out = DATA_DIR / "ui"
    paths = {}
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}

    # The checkbox tick, same story: style the indicator and Qt stops drawing
    # its own, leaving a filled olive square with nothing in it.
    tick = out / "tick.png"
    tick_img = QImage(16, 16, QImage.Format.Format_ARGB32)
    tick_img.fill(Qt.GlobalColor.transparent)
    tp = QPainter(tick_img)
    tp.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = tp.pen()
    pen.setColor(QColor(ACCENT_INK))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    tp.setPen(pen)
    tp.drawPolyline(QPolygonF([QPointF(4, 8.4), QPointF(6.9, 11.2), QPointF(12, 5.2)]))
    tp.end()
    tick_img.save(str(tick))
    paths["tick"] = tick.as_posix()

    size, w, h = 14, 7.0, 4.0
    for name, flip, colour in (("down", False, MUTED), ("up", True, MUTED),
                               ("down_dim", False, LINE_3)):
        path = out / f"arrow_{name}.png"
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(colour))
        cx, cy = size / 2, size / 2
        tip = cy + h / 2 if not flip else cy - h / 2
        base = cy - h / 2 if not flip else cy + h / 2
        p.drawPolygon(QPolygonF([
            QPointF(cx - w / 2, base), QPointF(cx + w / 2, base), QPointF(cx, tip)]))
        p.end()
        img.save(str(path))
        paths[name] = path.as_posix()
    _ARROW_DIR = paths
    return paths


def arrow_qss() -> str:
    """Stylesheet fragment wiring the generated arrows to the controls."""
    a = _arrow_paths()
    if not a:
        return ""
    return f"""
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({a['down']}); width: 14px; height: 14px; margin-right: 8px; }}
QComboBox::down-arrow:disabled {{ image: url({a['down_dim']}); }}

QSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 5px 5px 0 0; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0 5px 5px 0; }}
QSpinBox::up-arrow {{ image: url({a['up']}); width: 14px; height: 14px; }}
QSpinBox::down-arrow {{ image: url({a['down']}); width: 14px; height: 14px; }}

QCheckBox::indicator:checked {{ image: url({a['tick']}); }}
"""


# --- stylesheet -------------------------------------------------------------

def build_stylesheet() -> str:
    return f"""
QWidget {{
    background: {BG};
    color: {INK};
    font-family: {BODY_FONT};
    font-size: {SIZE_BODY}px;
}}

QLabel {{ background: transparent; }}

/* typography — serif display over plain sans body */
QLabel#H1 {{ font-family: {DISPLAY_FONT}; font-size: {SIZE_H1}px; font-weight: 400; color: #000000; }}
QLabel#H2 {{ font-family: {DISPLAY_FONT}; font-size: {SIZE_H2}px; font-weight: 400; color: #000000; }}
QLabel#H3 {{ font-size: {SIZE_H3}px; font-weight: 600; color: {INK}; }}
QLabel#Hero {{ font-size: {SIZE_HERO}px; font-weight: 600; color: {SKY_INK}; }}
QLabel#HeroBody {{ font-size: 15px; color: {SKY_INK_BODY}; }}
QLabel#HeroFaint {{ font-size: 15px; color: {SKY_INK_SOFT}; }}
QLabel#Muted {{ color: {MUTED}; font-size: {SIZE_BODY}px; }}
QLabel#Faint {{ color: {FAINT}; font-size: {SIZE_SMALL}px; }}
QLabel#Eyebrow {{ color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QLabel#SectionLabel {{ color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QLabel#Brand {{ font-family: {DISPLAY_FONT}; font-size: 15px; font-weight: 500; color: {INK}; }}

QLabel#Pill {{ background: {SURFACE_2}; color: {MUTED}; border: 1px solid {LINE}; border-radius: {R_CHIP}px; padding: 4px 11px; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QLabel#CreditPill {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; border: 1px solid {ACCENT_LINE}; border-radius: {R_PILL_SM}px; padding: 5px 14px; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QLabel#TagGreen {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: {R_CHIP}px; padding: 3px 9px; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QLabel#TagHot {{ background: {REC_SOFT}; color: {REC}; border-radius: {R_CHIP}px; padding: 3px 9px; font-size: {SIZE_SMALL}px; font-weight: 600; }}
/* keycap — white, a 1px edge and a 1.5px bottom lip, as in the design */
QLabel#Kbd {{ background: {SURFACE}; color: {INK}; border: 1px solid {LINE_3}; border-bottom: 2px solid {LINE_3}; border-radius: 6px; padding: 4px 10px; font-size: {SIZE_SMALL}px; font-weight: 500; }}

/* cards — white, one hairline, a generous radius */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CARD}px; }}
/* The wireframe's Insane Mode card when it is on: a gold edge. */
QFrame#CardOn {{ background: {SURFACE}; border: 1px solid {ACCENT}; border-radius: {R_CARD}px; }}
QFrame#FlatCard {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_PANEL}px; }}
/* A card confirming it just saved. Held for a moment, then dropped back to
   a plain card: a permanent green panel reads as a state rather than as an
   acknowledgement. */
QFrame#CardSaved {{
    background: {ACCENT_SOFT}; border: 1px solid {ACCENT_LINE};
    border-radius: {R_CARD}px;
}}
/* A card holding answers Sushi guessed. The amber is the same #C58A00 the
   fill script outlines those fields with in the browser, so the list in the
   app and the fields in the form are visibly the same set of things. */
QFrame#CardGuess {{
    background: #FFFBEF; border: 1px solid #C58A00;
    border-radius: {R_CARD}px;
}}
QFrame#Sunken {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {R_PANEL}px; }}
QFrame#TintGreen {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CARD}px; }}
QFrame#TintYellow {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CARD}px; }}
QFrame#TintPink {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CARD}px; }}
QFrame#TintBlue {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CARD}px; }}
QFrame#Selected {{ background: {SURFACE}; border: 2px solid {ACCENT}; border-radius: {R_CARD}px; }}
QFrame#Rail {{ background: {SURFACE}; border-right: 1px solid {LINE_2}; }}
QFrame#RailRight {{ background: {SURFACE}; border-left: 1px solid {LINE_2}; }}
QFrame#Sidebar {{ background: {SURFACE}; border-right: 1px solid {LINE_2}; }}
QFrame#TopBar {{ background: {BG}; border: none; }}
QFrame#Divider {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}
/* a nav group's tinted ground */
QFrame#GroupOlive {{ background: {GROUP_OLIVE}; border: 1px solid {GROUP_OLIVE_LINE}; border-radius: {R_TILE}px; }}
QFrame#GroupSand {{ background: {GROUP_SAND}; border: 1px solid {GROUP_SAND_LINE}; border-radius: {R_TILE}px; }}
QFrame#GroupClay {{ background: {GROUP_CLAY}; border: 1px solid {GROUP_CLAY_LINE}; border-radius: {R_TILE}px; }}

/* buttons. The design keeps exactly one saturated button on screen: the
   primary action is near-black ink, and olive is reserved for state. */
/* ``min-height`` is what makes a row of mixed controls line up. Padding
   alone cannot: it is added to a line box whose height comes from the font,
   so a 13px button and a 16px combo with identical padding are five pixels
   apart. Pinning the content box instead puts every control on H_CONTROL
   whatever is inside it. */
QPushButton {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE_3};
    border-radius: {R_BTN}px; padding: {_PAD_CONTROL}px 16px;
    min-height: {H_CONTROL - 2 * _PAD_CONTROL - 2}px;
    font-size: {SIZE_SMALL}px; font-weight: 500;
}}
QPushButton:hover {{ background: {SURFACE_2}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE_2}; border-color: {LINE}; }}

QPushButton#Primary {{ background: transparent; color: {ACCENT_DEEP}; border: 1px solid {ACCENT}; font-weight: 500; }}
QPushButton#Primary:hover {{ background: {ACCENT_SOFT}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_SOFT}; border-color: {ACCENT_DEEP}; padding-top: 1px; }}
QPushButton#Primary:disabled {{ background: transparent; border-color: {LINE_2}; color: {FAINT}; }}

QPushButton#Accent {{ background: {ACCENT}; color: {ACCENT_INK}; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Accent:disabled {{ background: {SURFACE_3}; border-color: {LINE}; color: {FAINT}; }}

/* kept under its historical name because pages import it; it is the accent
   button, and the accent is now the earlier design's olive rather than Clay's lime. */
QPushButton#Lime {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; border: 1px solid {ACCENT_LINE}; border-radius: {R_PILL}px; padding: {_PAD_CONTROL}px 18px; font-weight: 600; }}
QPushButton#Lime:hover {{ background: #E4E9D2; }}

QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 8px 12px; }}
QPushButton#Ghost:hover {{ color: {INK}; background: {SURFACE_2}; border-radius: {R_BTN}px; }}
QPushButton#Ghost:pressed {{ color: {INK}; background: {SURFACE_3}; border-radius: {R_BTN}px; }}
QPushButton#Ghost:disabled {{ color: {LINE_3}; background: transparent; }}
/* The confirm on a dialog that cannot be undone — submitting an application,
   emptying the answer bank. Solid rather than the outlined Danger above,
   because as the primary of a pair it has to carry the same weight as the
   Primary it replaces. */
QPushButton#DangerSolid {{ background: {REC}; color: {ACCENT_INK}; border: 1px solid {REC}; font-weight: 600; }}
QPushButton#DangerSolid:hover {{ background: #8E2416; border-color: #8E2416; }}

QPushButton#Danger {{ background: transparent; color: {REC}; border: 1px solid {LINE_3}; }}
QPushButton#Danger:hover {{ border-color: {REC}; background: {REC_SOFT}; }}
QPushButton#Danger:disabled {{ color: {LINE_3}; border-color: {LINE}; background: {SURFACE_2}; }}

QPushButton#Chip {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_CHIP}px; padding: {_PAD_CHIP}px 14px; min-height: {H_CHIP - 2 * _PAD_CHIP - 2}px; color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QPushButton#Chip:hover {{ background: {SURFACE_2}; color: {INK}; }}
/* An ID selector outranks the plain ``QPushButton:disabled`` rule, so a
   disabled Chip kept its full-contrast ink and read as pressable. Both of
   Manual apply's "Open…" buttons shipped looking live with nothing to
   open. Same for Link, for the same reason. */
QPushButton#Chip:pressed {{ background: {SURFACE_3}; color: {INK}; }}
QPushButton#Chip:disabled {{ color: {LINE_3}; background: {SURFACE_2}; border-color: {LINE}; }}
QPushButton#ChipActive:disabled {{ color: {LINE_3}; background: {SURFACE_2}; border-color: {LINE}; }}
QPushButton#ChipActive {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_LINE}; border-radius: {R_CHIP}px; padding: {_PAD_CHIP}px 14px; min-height: {H_CHIP - 2 * _PAD_CHIP - 2}px; color: {ACCENT_DEEP}; font-size: {SIZE_SMALL}px; font-weight: 600; }}

/* A Primary at chip height, for a row of actions in a section header. A
   40px black button beside a 32px chip is the pair looking like a mistake —
   same row, same job, two sizes. */
QPushButton#ChipPrimary {{ background: {INK}; color: {ACCENT_INK}; border: 1px solid {INK}; border-radius: {R_CHIP}px; padding: {_PAD_CHIP}px 16px; min-height: {H_CHIP - 2 * _PAD_CHIP - 2}px; font-size: {SIZE_SMALL}px; font-weight: 600; }}
QPushButton#ChipPrimary:hover {{ background: #3A3A36; border-color: #3A3A36; }}
QPushButton#ChipPrimary:disabled {{ background: {SURFACE_3}; border-color: {LINE}; color: {FAINT}; }}

QPushButton#Link {{ background: transparent; border: none; color: {MUTED}; padding: 2px 6px; font-size: {SIZE_SMALL}px; font-weight: 500; }}
QPushButton#Link:hover {{ color: {INK}; }}
QPushButton#Link:disabled {{ color: {LINE_3}; }}

/* top-bar square affordances (sidebar toggle, window buttons) */
QPushButton#IconBtn {{ background: transparent; border: none; border-radius: {R_CHIP}px; padding: 0; color: {MUTED}; }}
QPushButton#IconBtn:hover {{ background: {SURFACE_2}; }}
QPushButton#IconBtnOn {{ background: {SURFACE_2}; border: none; border-radius: {R_CHIP}px; padding: 0; color: {INK}; }}

/* sidebar rows. A row is a frame rather than a button because it carries an
   icon tile, a label and a count; QPushButton cannot host child widgets. */
QFrame#NavRow {{ background: transparent; border: none; border-radius: {R_BTN}px; }}
QFrame#NavRow:hover {{ background: {SURFACE_2}; }}
/* Selected is the solid accent, with the row's type and glyph inverted to
   cream. A wash reads as a hover state on a white group — you have to look
   twice to find where you are — and an outline reads as neither. */
QFrame#NavRowActive {{ background: {SURFACE_2}; border: 1px solid {SURFACE_2}; border-radius: {R_BTN}px; }}
QFrame#NavRow QLabel, QFrame#NavRowActive QLabel {{ background: transparent; }}
QLabel#NavLabel {{ font-size: {SIZE_BODY}px; color: {INK}; font-weight: 400; }}
QLabel#NavLabelActive {{ font-size: {SIZE_BODY}px; color: {INK}; font-weight: 600; }}
QLabel#NavCount {{ font-size: {SIZE_SMALL}px; color: {FAINT}; font-weight: 500; }}
/* The count keeps its place on a selected row, one step down from the label
   so it still reads as metadata rather than a second heading. */
QLabel#NavCountActive {{ font-size: {SIZE_SMALL}px; color: {ACCENT}; font-weight: 600; }}
QLabel#GroupTitle {{ font-size: {SIZE_SMALL}px; color: {MUTED}; font-weight: 600; }}

/* sidebar nav rows — a soft wash when selected, never a solid block */
QPushButton#NavItem {{ background: transparent; border: none; color: {INK}; padding: 8px 12px; border-radius: {R_BTN}px; font-size: {SIZE_BODY}px; font-weight: 400; text-align: left; }}
QPushButton#NavItem:hover {{ background: {SURFACE_2}; }}
QPushButton#NavItemActive {{ background: {SURFACE}; border: 1px solid {LINE_2}; color: {INK}; padding: 8px 12px; border-radius: {R_BTN}px; font-size: {SIZE_BODY}px; font-weight: 600; text-align: left; }}

/* inputs */
/* ``min-height`` is not decoration. Without it the box is sized from the
   font's *ascent* and the descenders go over the edge — "Interviewing"
   lost the tail of its g, "Applied" the tail of its p, on every card in
   the Tracker. */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {{
    background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_TILE}px;
    padding: {_PAD_CONTROL}px 14px; color: {INK}; font-size: {SIZE_BODY}px;
    min-height: {H_CONTROL - 2 * _PAD_CONTROL - 2}px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DEEP};
}}
/* Focus is a ring, as in the design — a 2px olive edge rather than a recolour
   of the hairline, which at 1px is almost invisible on a cream ground. The
   padding drops by the pixel the border gains so the field does not jolt and
   the text does not shift when it takes focus. */
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border: 2px solid {ACCENT};
    padding: {_PAD_CONTROL - 1}px 13px;
}}
QComboBox:focus, QComboBox:on {{
    border: 2px solid {ACCENT};
    padding: {_PAD_CONTROL - 1}px 13px;
}}
QSpinBox:focus {{ padding-right: 23px; }}
/* Hover is a quieter version of the same signal, so a field looks reachable
   before it is clicked. */
QLineEdit:hover:!focus, QTextEdit:hover:!focus, QPlainTextEdit:hover:!focus,
QComboBox:hover:!focus, QSpinBox:hover:!focus {{ border-color: {LINE_3}; }}
QLineEdit:disabled, QTextEdit:disabled {{ color: {FAINT}; background: {SURFACE_2}; }}
/* The popup is a separate top-level window with its own palette, and a rule
   that sets only its background leaves the *text* on the system default —
   which on Windows' dark-ish popup palette came out as a black slab with
   barely-visible grey items. Every colour it uses has to be named, and the
   items have to be styled in their own right. */
QComboBox QAbstractItemView {{
    background: {SURFACE}; color: {INK};
    border: 1px solid {LINE_2}; border-radius: {R_BTN}px;
    padding: 4px; outline: none;
    selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT_DEEP};
}}
QComboBox QAbstractItemView::item {{
    background: transparent; color: {INK};
    padding: 7px 10px; min-height: 20px; border-radius: {R_CHIP}px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {SURFACE_2}; color: {INK};
}}
QComboBox QAbstractItemView::item:selected {{
    background: {ACCENT_SOFT}; color: {ACCENT_DEEP};
}}

/* tables */
QTableWidget, QTableView {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_PANEL}px; gridline-color: transparent; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {LINE}; padding: 11px 8px; font-weight: 600; font-size: {SIZE_SMALL}px; }}

/* --- the job details drawer -------------------------------------------- */
QFrame#DetailsPanel {{
    background: {SURFACE}; border: none;
    border-left: 1px solid {LINE_2};
}}
QWidget#DetailsFoot {{
    background: {BG}; border-top: 1px solid {LINE};
}}

/* --- the applications table -------------------------------------------
   A banded table under one solid header, in the manner of a printed
   schedule: the header is the only saturated thing, the rows alternate so
   the eye can track across six columns without rules, and there are no
   gridlines at all. Vertical rules would be doing the banding's job twice.

   The top corners are rounded on the header's first and last sections
   rather than the widget, because a QTableWidget's viewport paints square
   over its own frame and the olive band would show two hard corners. */
QTableWidget#AppTable {{
    background: {SURFACE}; border: 1px solid {LINE_2};
    border-radius: {R_CARD}px; gridline-color: transparent;
    selection-background-color: {ACCENT_SOFT}; selection-color: {INK};
}}
/* No horizontal padding here. Every cell in this table holds a widget, and
   item padding is subtracted from the rect a cell *widget* is given — so an
   18px gutter set in both places was charged twice, handing each widget 36px
   less than its column and truncating "Submitted" to "Submitte". The gutter
   lives in the cell widgets, which are the things that draw. */
QTableWidget#AppTable::item {{
    border: none; border-bottom: 1px solid {LINE}; color: {INK};
}}
QTableWidget#AppTable::item:selected {{
    background: {ACCENT_SOFT}; color: {ACCENT_DEEP};
}}
QTableWidget#AppTable QHeaderView::section {{
    background: {SURFACE}; color: {MUTED}; border: none;
    border-bottom: 1px solid {LINE_2};
    padding: 15px {CELL_PAD}px; font-weight: 600; font-size: {SIZE_SMALL}px;
}}
QTableWidget#AppTable QHeaderView::section:first {{
    border-top-left-radius: {R_CARD}px;
}}
QTableWidget#AppTable QHeaderView::section:last {{
    border-top-right-radius: {R_CARD}px; border-right: none;
}}

/* Status cells. A tick and a cross carry the result at a glance, exactly
   as the reference does; the wash behind them keeps them from reading as
   decoration on a white row. */
QLabel#StatusGood {{ color: {GOOD}; font-size: {SIZE_SMALL}px; font-weight: 600; background: transparent; }}
QLabel#StatusWarn {{ color: {WARN}; font-size: {SIZE_SMALL}px; font-weight: 600; background: transparent; }}
QLabel#StatusBad {{ color: {BAD}; font-size: {SIZE_SMALL}px; font-weight: 600; background: transparent; }}
QLabel#StatusIdle {{ color: {FAINT}; font-size: {SIZE_SMALL}px; font-weight: 600; background: transparent; }}
QTableView::item {{ padding: 9px; border-bottom: 1px solid {LINE}; }}
/* A row answers the pointer, so a long list reads as something to act on. */
QTableView::item:hover {{ background: {SURFACE_2}; }}
QTableWidget#AppTable::item:hover {{ background: {SURFACE_2}; }}

/* QTextBrowser inherits QTextEdit's box but renders its *document* in the
   document's own default font, which is not the stylesheet's — so a job
   description came out in a serif face nothing else on screen uses. */
QTextBrowser {{
    background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_PANEL}px;
    padding: 16px 18px; color: {INK}; font-family: {BODY_FONT}; font-size: {SIZE_BODY}px;
}}

QListWidget {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_PANEL}px; }}
QListWidget::item {{ padding: 11px; border-bottom: 1px solid {LINE}; color: {INK}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; }}
QScrollBar::handle:horizontal {{ background: {LINE_3}; border-radius: 4px; min-width: 30px; }}

/* A table's scrollbar is pushed below its header in code, not here — a
   stylesheet margin is ignored for it, because QAbstractScrollArea lays
   its own scrollbars out and overwrites whatever the style asked for. The
   same selector will happily change the width, which is what makes that
   worth writing down. See ``components.scroll_below_header``. */

QProgressBar {{ background: {SURFACE_3}; border: none; border-radius: 3px; height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

/* Transparent, or the universal QWidget rule paints a cream strip across
   whatever white card the checkbox is sitting on. */
QCheckBox {{ background: transparent; font-size: {SIZE_BODY}px; spacing: 10px; }}
QRadioButton {{ background: transparent; font-size: {SIZE_BODY}px; spacing: 10px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {LINE_3}; border-radius: 5px; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: none; }}

QToolTip {{ background: {INK}; color: {BG}; border: none; padding: 6px 9px; border-radius: 6px; }}

/* --- metadata, set in mono ------------------------------------------- */
QLabel#Mono {{ font-family: {MONO_FONT}; font-size: 12px; color: {FAINT}; }}
QLabel#MonoDim {{ font-family: {MONO_FONT}; font-size: 11px; color: {LINE_3}; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; color: {INK}; }}
QLabel#Match {{ font-size: {SIZE_SMALL}px; font-weight: 600; color: {ACCENT}; }}

/* --- badges ------------------------------------------------------------ */
QLabel#Badge {{
    background: {SURFACE_2}; color: {MUTED}; border-radius: 7px;
    padding: 1px 7px; font-size: 11px; font-weight: 600;
}}
QLabel#BadgeNew {{
    background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: 7px;
    padding: 1px 7px; font-size: 11px; font-weight: 600;
}}
QLabel#BadgeAgent {{
    background: {ACCENT_SOFT}; color: {ACCENT}; border: 1px solid {ACCENT_LINE};
    border-radius: 5px; padding: 1px 6px; font-size: 10px; font-weight: 700;
}}

/* --- sidebar: a bordered group with its label outside it --------------- */
QLabel#GroupLabel {{ color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 500; }}
QFrame#NavGroup {{ background: transparent; border: none; }}

/* --- the search field, with its icon inside ---------------------------- */
QLineEdit#Search {{
    background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_TILE}px;
    padding: {_PAD_CONTROL}px 16px {_PAD_CONTROL}px 44px; font-size: {SIZE_BODY}px;
}}
QLineEdit#Search:focus {{ border: 2px solid {ACCENT}; padding: {_PAD_CONTROL - 1}px 15px {_PAD_CONTROL - 1}px 43px; }}
QLineEdit#Search:hover:!focus {{ border-color: {LINE_3}; }}

/* --- pills: the hero's pair, and the model switcher -------------------- */
/* The two hero buttons sit side by side, so they are the same box: same
   padding, same radius, same height. They were 20px and 18px of horizontal
   padding, which is not a difference you can name looking at it and is
   exactly why the pair looked off. */
QPushButton#Pill {{
    background: {SURFACE}; color: {INK}; border: 1px solid {SURFACE};
    border-radius: {R_PILL}px; padding: {_PAD_CONTROL}px 20px;
    min-height: {H_CONTROL - 2 * _PAD_CONTROL - 2}px;
    font-size: {SIZE_SMALL}px; font-weight: 600;
}}
QPushButton#Pill:hover {{ background: #FFFFFF; border-color: #FFFFFF; }}
QPushButton#PillGhost {{
    background: transparent; color: {SKY_INK}; border: 1px solid rgba(255,248,244,0.38);
    border-radius: {R_PILL}px; padding: {_PAD_CONTROL}px 20px;
    min-height: {H_CONTROL - 2 * _PAD_CONTROL - 2}px;
    font-size: {SIZE_SMALL}px; font-weight: 600;
}}
QPushButton#PillGhost:hover {{ background: rgba(255,248,244,0.16); }}
/* The door's one button. Bigger than a hero pill, in the warm off-white the
   sky's ink uses rather than a flat #FFF, and with the coral ink the mascot
   is painted in — on a clay ground a white slab with near-black text reads
   as a dialog button, which is the one thing this screen is not. It is
   given a fixed height in code and a radius just under half of it, because
   anything at or over half is painted square. */
QPushButton#DoorPill {{
    background: {SKY_INK}; color: {ACCENT_DEEP}; border: 1px solid {SKY_INK};
    border-radius: {R_PILL_LG}px; padding: 0 34px; min-height: 52px;
    font-size: {SIZE_BODY}px; font-weight: 600;
}}
QPushButton#DoorPill:hover {{ background: #FFFFFF; border-color: #FFFFFF; }}
QPushButton#DoorPill:pressed {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT}; }}
QFrame#ModelPillFrame {{
    background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: {R_PILL}px;
}}
QFrame#ModelPillFrame:hover {{ border-color: {LINE_3}; }}
QFrame#ModelPillFrame QLabel {{ background: transparent; }}
QPushButton#ModelPill {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE_2};
    border-radius: {R_PILL_SM}px; padding: 5px 12px;
    font-size: {SIZE_SMALL}px; font-weight: 600; text-align: left;
}}
QPushButton#ModelPill:hover {{ border-color: {LINE_3}; background: #FFFFFF; }}

/* --- segmented controls ------------------------------------------------
   Two sizes of the same idea: a sunken track with the current option lifted
   out of it on a white card. ``Segment`` is the 32px icon pair in the title
   bar; ``SegTrack`` is the full-height text version that replaced the loose
   chip rows in Settings and on Insane mode. */
QFrame#Segment {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {R_BTN}px;
}}
QFrame#SegTrack {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {R_BTN}px;
}}
QPushButton#SegOption {{
    background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 0 16px; min-height: {H_CONTROL - 8}px;
    color: {MUTED}; font-size: {SIZE_SMALL}px; font-weight: 600;
}}
QPushButton#SegOption:hover {{ color: {INK}; background: {SURFACE_3}; }}
QPushButton#SegOptionOn {{
    background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: 7px;
    padding: 0 16px; min-height: {H_CONTROL - 8}px;
    color: {INK}; font-size: {SIZE_SMALL}px; font-weight: 600;
}}
QPushButton#SegBtn {{ background: transparent; border: none; border-radius: 7px; padding: 0; }}
QPushButton#SegBtn:hover {{ background: {SURFACE_3}; }}
QPushButton#SegBtnOn {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: 7px; padding: 0; }}

/* --- assistant: full-width suggestions, quieter than a chip ------------ */
QPushButton#Suggest {{
    background: {SURFACE}; color: {INK}; border: 1px solid {LINE_2};
    border-radius: {R_TILE}px; padding: 12px 14px;
    font-size: {SIZE_SMALL}px; font-weight: 500;
}}
QPushButton#Suggest:hover {{ background: {SURFACE_2}; border-color: {LINE_3}; }}

/* --- the console strip along the bottom -------------------------------- */
QFrame#ConsoleBar {{ background: {BG}; border-top: 1px solid {LINE}; }}

/* --- job card ----------------------------------------------------------- */
QFrame#JobCard {{ background: {SURFACE}; border: 1px solid {LINE_2}; border-radius: 14px; }}
QFrame#JobCard:hover {{ border-color: {LINE_3}; }}
QPushButton#InstantApply {{
    background: transparent; border: none; color: {ACCENT};
    font-size: {SIZE_SMALL}px; font-weight: 600; padding: 2px 0; text-align: left;
}}
QPushButton#InstantApply:hover {{ color: {ACCENT_HOVER}; }}
""" + arrow_qss()


# built once fonts are registered; main.py calls refresh_stylesheet()
STYLESHEET = build_stylesheet()


def refresh_stylesheet(display: str | None = None, body: str | None = None) -> str:
    """Rebuild the stylesheet against the fonts this machine actually has.

    ``display`` is the family a bundled font file registered, if any; it goes
    to the front of the queue but still has to exist to be used.
    """
    global DISPLAY_FAMILIES, BODY_FAMILIES, STYLESHEET
    if display and display not in DISPLAY_FAMILIES:
        DISPLAY_FAMILIES = [display] + DISPLAY_FAMILIES
    if body and body not in BODY_FAMILIES:
        BODY_FAMILIES = [body] + BODY_FAMILIES
    resolve_families(display or "")
    STYLESHEET = build_stylesheet()
    return STYLESHEET


#: The tooltip, as a stylesheet of its own.
#:
#: It is in the main stylesheet too, and that is not where it takes effect.
#: The main stylesheet is set on the *window*; a tooltip is a separate
#: top-level window and inherits nothing from it, so that rule never
#: reached one — the tooltip fell through to the palette, got
#: ``ToolTipText`` (the body ink) on a background Windows paints dark in
#: dark mode, and a role title on All jobs came out charcoal on black.
#:
#: Applied to the QApplication, which is the only thing a top-level popup
#: does inherit from. Scoped to ``QToolTip`` alone on purpose: an app-level
#: stylesheet cascades into every widget in the program, and the window's
#: own sheet is the one that should be doing that work.
TOOLTIP_QSS = f"""
QToolTip {{
    background: {INK};
    color: {BG};
    border: 1px solid {INK};
    padding: 6px 9px;
    border-radius: 6px;
    font-size: {SIZE_SMALL}px;
}}
"""


def apply_palette(app) -> None:
    """Pin the light palette on the application itself.

    The stylesheet is set on the main window, which covers everything drawn
    inside it. A combo box's popup is not drawn inside it: it is a *top-level
    window*, and a top-level window takes its palette from the application.
    On a machine running Windows in dark mode that palette's Base is
    near-black, which is exactly how the country list came out — a black slab
    with barely-readable items — while the QSS item rules that *did* reach it
    painted the selection pink and the hover cream, as written.

    So the palette is named here rather than fought in QSS, once, for every
    popup, menu, tooltip and scroll-area viewport the app will ever open.
    """
    from PyQt6.QtGui import QColor, QPalette

    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    pal = QPalette()
    for g in (group.Active, group.Inactive, group.Disabled):
        pal.setColor(g, role.Window, QColor(BG))
        pal.setColor(g, role.Base, QColor(SURFACE))
        pal.setColor(g, role.AlternateBase, QColor(SURFACE_2))
        pal.setColor(g, role.Button, QColor(SURFACE))
        # The tooltip is the one surface in the app that is dark on purpose,
        # so it is the one place the ink/paper pair is the wrong way round.
        # Both halves are named here: leaving the text to the loop below
        # gave it the body ink, which against a dark ground is the
        # unreadable tooltip this pair exists to prevent.
        pal.setColor(g, role.ToolTipBase, QColor(INK))
        pal.setColor(g, role.ToolTipText, QColor(BG))
        pal.setColor(g, role.Highlight, QColor(ACCENT_SOFT))
        pal.setColor(g, role.HighlightedText, QColor(ACCENT_DEEP))
        pal.setColor(g, role.PlaceholderText, QColor(FAINT))
        # Text that is meant to be read, and text that is meant to look
        # unavailable. Disabled gets the faint grey rather than the ink, so
        # a greyed-out item still reads as greyed out on a white ground.
        ink = QColor(FAINT if g == group.Disabled else INK)
        for text_role in (role.WindowText, role.Text, role.ButtonText):
            pal.setColor(g, text_role, ink)
    app.setPalette(pal)
    # And the stylesheet the palette cannot carry. See TOOLTIP_QSS: a
    # tooltip is a top-level window, so the window's own sheet never
    # reaches it and this is the only level that does.
    app.setStyleSheet(TOOLTIP_QSS)


def score_colour(score: int) -> str:
    if score >= 14:
        return GOOD
    if score >= 10:
        return WARN
    return MUTED


def score_tint(score: int) -> str:
    """The icon-tile tint for a score. Cards stay white; only the tile moves."""
    if score >= 14:
        return TINT_GREEN
    if score >= 11:
        return TINT_BLUE
    if score >= 8:
        return TINT_SAND
    return TINT_CLAY


# --- legacy aliases ---------------------------------------------------------
# Kept so pages that already import them keep working; they now resolve to the
# The earlier design palette rather than Clay's.

DEEP = ACCENT_DEEP
LIME = ACCENT_SOFT
LIME_HOVER = "#E4E9D2"
LIME_INK = ACCENT_DEEP
CREDIT = INK
TINT_YELLOW = TINT_SAND
TINT_PINK = TINT_ROSE
