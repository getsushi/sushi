"""Figures for the Sushi feature document.

Every number here is measured, not estimated: the corpus figures come from
``jobs.db``, the check counts from the suites themselves. Colours are the
validated categorical palette (slots 1-4) plus the reserved status pair;
the two slots that sit under 3:1 on a light surface carry direct labels,
which is the relief rule.
"""
from __future__ import annotations

import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                              # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = pathlib.Path(__file__).parent / "figures"
OUT.mkdir(parents=True, exist_ok=True)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8984"
GRID = "#e8e7e3"

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": INK,
    "axes.labelcolor": INK_2,
    "xtick.color": INK_3,
    "ytick.color": INK_2,
    "axes.edgecolor": GRID,
})


def _bare(ax, x_grid: bool = True) -> None:
    """Recessive axes: no box, no tick marks, a faint grid behind."""
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    if x_grid:
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)


# --- 1. the pipeline, as a diagram ------------------------------------------

def pipeline() -> None:
    steps = [
        ("Read the CV", "one file in"),
        ("Find the work", "296,968 jobs on file"),
        ("Score the match", "title, seniority,\nskills, place"),
        ("Choose", "tick what you want"),
        ("Fill the form", "in a real browser"),
        ("Track it", "applied → offer"),
    ]
    fig, ax = plt.subplots(figsize=(9.2, 2.05), dpi=200)
    ax.set_xlim(0, len(steps) * 10)
    ax.set_ylim(0, 10)
    ax.axis("off")

    for i, (title, sub) in enumerate(steps):
        x = i * 10 + 0.4
        filled = i == 4            # the one step the browser does
        face = "#eef4fc" if not filled else "#fdeee7"
        edge = BLUE if not filled else ORANGE
        ax.add_patch(FancyBboxPatch(
            (x, 3.1), 8.4, 4.4, boxstyle="round,pad=0.25,rounding_size=0.8",
            linewidth=1.2, edgecolor=edge, facecolor=face))
        ax.text(x + 4.2, 6.0, title, ha="center", va="center",
                fontsize=9.6, color=INK, fontweight="600")
        ax.text(x + 4.2, 4.3, sub, ha="center", va="center",
                fontsize=7.6, color=INK_2, linespacing=1.35)
        if i:
            ax.add_patch(FancyArrowPatch(
                (x - 1.3, 5.3), (x - 0.25, 5.3),
                arrowstyle="-|>", mutation_scale=11,
                linewidth=1.1, color=INK_3))

    ax.text(0.4, 1.3, "Local — your CV, your key and your data stay on the "
                      "machine", fontsize=7.6, color=INK_3)
    ax.text(len(steps) * 10 - 0.4, 1.3, "orange = a real browser",
            fontsize=7.6, color=ORANGE, ha="right")
    fig.tight_layout(pad=0.2)
    fig.savefig(OUT / "pipeline.png", bbox_inches="tight")
    plt.close(fig)


# --- 2. where the jobs come from, and which Sushi can fill ------------------

def coverage() -> None:
    # Exact, from jobs.db on 2026-10-27.
    rows = [
        ("Greenhouse", 161_003, "verified"),
        ("Lever", 60_215, "verified"),
        ("Ashby", 38_707, "verified"),
        ("SmartRecruiters", 35_257, "blocked"),
        ("Recruitee", 1_014, "untested"),
        ("Other boards", 500, "untested"),
        ("Workable", 272, "untested"),
    ]
    colour = {"verified": GOOD, "blocked": CRITICAL, "untested": WARNING}
    rows = sorted(rows, key=lambda r: r[1])
    names = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    cols = [colour[r[2]] for r in rows]

    fig, ax = plt.subplots(figsize=(9.2, 3.5), dpi=200)
    ax.barh(names, vals, height=0.62, color=cols, edgecolor=SURFACE,
            linewidth=2)
    _bare(ax)
    ax.set_xlim(0, max(vals) * 1.16)
    ax.xaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    for name, v in zip(names, vals):
        ax.text(v + max(vals) * 0.012, name, f"{v:,}", va="center",
                fontsize=8.6, color=INK)
    ax.set_xlabel("vacancies on file", fontsize=8.4)
    ax.tick_params(axis="y", labelsize=9)

    handles = [plt.Line2D([0], [0], marker="s", linestyle="",
                          markersize=7, color=colour[k],
                          label={"verified": "Fills end to end (tested live)",
                                 "blocked": "Reader sees no fields",
                                 "untested": "Not yet tested"}[k])
               for k in ("verified", "blocked", "untested")]
    ax.legend(handles=handles, frameon=False, fontsize=8,
              loc="lower right", bbox_to_anchor=(1.0, -0.02),
              labelcolor=INK_2)
    fig.tight_layout(pad=0.3)
    fig.savefig(OUT / "coverage.png", bbox_inches="tight")
    plt.close(fig)


# --- 3. what is actually checked --------------------------------------------

def verification() -> None:
    rows = [
        ("qa_fields", 50, "every kind of field, in a real browser"),
        ("qa_features", 45, "the engine: scoring, storage, coercion"),
        ("qa_audit", 44, "does each control do its work"),
        ("qa_ui", 38, "the widgets, driven as a user drives them"),
        ("qa_browser", 20, "the injected JavaScript, live"),
        ("qa_journey", 15, "CV → matches → applied → restart"),
    ]
    rows = sorted(rows, key=lambda r: r[1])
    names = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    notes = [r[2] for r in rows]

    fig, ax = plt.subplots(figsize=(9.2, 2.9), dpi=200)
    ax.barh(names, vals, height=0.58, color=BLUE, edgecolor=SURFACE,
            linewidth=2)
    # Direct labels carry the values, so the axis would only add an empty
    # stretch of scale to the right of the longest bar. Drop it.
    _bare(ax, x_grid=False)
    ax.set_xlim(0, max(vals) * 2.35)
    ax.set_xticks([])
    for name, v, note in zip(names, vals, notes):
        ax.text(v + 1.2, name, f"{v}", va="center", fontsize=8.8,
                color=INK, fontweight="600")
        ax.text(v + 5.2, name, note, va="center", fontsize=7.8, color=INK_3)
    ax.tick_params(axis="y", labelsize=9)
    fig.tight_layout(pad=0.3)
    fig.savefig(OUT / "verification.png", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    pipeline()
    coverage()
    verification()
    print("wrote:", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))
