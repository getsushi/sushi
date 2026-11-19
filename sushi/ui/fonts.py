"""Bundled fonts.

The earlier design sets every title in **Fraunces** (SIL OFL) over the platform's own
sans — no bundled body face at all, because Segoe UI Variable is already the
right thing on Windows and substituting a webfont for it only makes the app
look slightly foreign.

So there is exactly one font to ship, and it is optional: drop
``Fraunces.ttf`` (or any ``Fraunces*.ttf/otf`` — the variable version works)
into ``sushi/assets/fonts`` and titles upgrade with no code change. Without
it the stack in ``theme.DISPLAY_FONT`` falls through to Georgia, which is
close enough in colour and width that nothing reflows.

Never raises: a missing folder or an unreadable file is a cosmetic difference,
not a reason to fail startup.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFontDatabase

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

#: What the stylesheet asks for when nothing is bundled.
DISPLAY = "Cormorant Garamond"


def load_fonts() -> dict[str, str]:
    """Register every face we ship; return {role: family actually loaded}.

    The wireframe sets titles in Cormorant Garamond and body text in Lora,
    both SIL OFL and bundled here.
    """
    loaded: dict[str, str] = {}
    try:
        if not _ASSETS.is_dir():
            return loaded
        for path in sorted(_ASSETS.iterdir()):
            if path.suffix.lower() not in (".ttf", ".otf"):
                continue
            fid = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            for fam in families:
                if fam.startswith("Cormorant"):
                    loaded["display"] = fam
                elif fam == "Lora":
                    loaded["body"] = fam
    except OSError:
        return loaded
    return loaded
