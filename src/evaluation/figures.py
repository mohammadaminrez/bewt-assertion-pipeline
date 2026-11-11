"""Thesis figures, drawn from the results in <output>/results.db.

Written to <output>/reports/figures/ by ``bewt-pipeline report`` (or
``python scripts/make_thesis_charts.py``), each as PNG and PDF plus a CSV
with the numbers it shows (the figure's table view):

  fig1_pass_rate          pass rate per treatment, one bar per model
  fig2_categories         final category of every answer, per model and treatment
  fig3_categories_<app>   the same, for one app

Only benchmark results (source 'bewt') are used. The final category is the
manual review label when there is one, else the category from execution.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

# --- look -------------------------------------------------------------------
# Categorical slots in fixed order (validated for colour-vision deficiency on
# the light surface below); the lighter slots carry dark text when labelled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
DARK_TEXT_ON = {"#1baf7a", "#eda100", "#e87ba4"}
SURFACE = "#fcfcfb"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"

DPI = 200
PX = DPI / 96                 # sizes are given in CSS px
BAR = 22 * PX                 # bar thickness
ROUND = 4 * PX                # rounded data end
GAP = 2 * PX                  # gap between stacked segments
WIDTH_IN = 7.2
HEADER_IN = {1: 1.05, 2: 1.3}  # title, subtitle and legend band, by legend rows

TREATMENTS = {
    "A": "A  test code only",
    "B": "B  + Gherkin specification",
    "C": "C  + page HTML",
    "D": "D  + Page Objects",
}
MODELS = {"gpt-4o-mini": "gpt-4o-mini", "claude-haiku": "Claude Haiku 4.5"}
APPS = {"bludit": "Bludit 3.13.1", "kanboard": "Kanboard 1.2.15", "joomla": "Joomla 3.10.11",
        "mantisbt": "MantisBT 1.2.0", "claroline": "Claroline 1.11.10"}
CATEGORIES = [
    ("correct", "correct"),
    ("over_assertive", "over-assertive"),
    ("under_assertive", "under-assertive"),
    ("wrong_assertion", "wrong assertion"),
    ("not_executable", "not executable"),
]


# --- data -------------------------------------------------------------------

def load(db_path):
    """Benchmark answers: (model, treatment, app, passes, final category)."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT model, treatment, app, passes, "
        "COALESCE(manual_error_category, error_category) "
        "FROM experiments WHERE source = 'bewt'"
    ).fetchall()
    con.close()
    return rows


def models_in(rows):
    """Known models first, in a fixed order, so each keeps its colour."""
    found = {r[0] for r in rows}
    return [m for m in MODELS if m in found] + sorted(found - set(MODELS))


def treatments_in(rows):
    return sorted({r[1] for r in rows})


# --- drawing helpers --------------------------------------------------------

def new_figure(plot_rows_in, legend_rows=1):
    head = HEADER_IN[legend_rows]
    fig = plt.figure(figsize=(WIDTH_IN, head + 0.75 + plot_rows_in), dpi=DPI)
    fig.header_in = head
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_subplot(111)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9, length=0, pad=6)
    ax.tick_params(axis="y", colors=INK_2, labelsize=10, length=0, pad=8)
    ax.grid(axis="x", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    return fig, ax


def finish_layout(fig):
    """Fix the axes position before any pixel-based geometry is measured."""
    fig.tight_layout(rect=(0, 0, 1, 1 - fig.header_in / fig.get_figheight()))


def data_per_px(ax):
    ax.figure.canvas.draw()
    box = ax.get_window_extent()
    (x0, x1), (y0, y1) = ax.get_xlim(), ax.get_ylim()
    return (x1 - x0) / box.width, abs(y1 - y0) / box.height


def hbar(ax, y, left, width, color, rounded, dx, dy):
    """Horizontal bar, square at the baseline, rounded at its data end."""
    h = BAR * dy
    rx, ry = min(ROUND * dx, width / 2), min(ROUND * dy, h / 2)
    x0, x1, y0, y1 = left, left + width, y - h / 2, y + h / 2
    if rounded and width > 0:
        verts = [(x0, y0), (x1 - rx, y0), (x1, y0), (x1, y0 + ry), (x1, y1 - ry),
                 (x1, y1), (x1 - rx, y1), (x0, y1), (x0, y0)]
        codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO,
                 MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    else:
        verts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        codes = [MPath.MOVETO] + [MPath.LINETO] * 3 + [MPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, edgecolor="none"))


def header(fig, ax, title, subtitle, legend_labels, ncol):
    h = fig.get_figheight()
    fig.text(0.02, 1 - 0.18 / h, title, ha="left", va="top", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.02, 1 - 0.46 / h, subtitle, ha="left", va="top", fontsize=9, color=INK_2)
    handles = [plt.Rectangle((0, 0), 1, 1, color=SERIES[i]) for i in range(len(legend_labels))]
    ax.legend(handles, legend_labels, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=ncol,
              frameon=False, fontsize=9, labelcolor=INK_2, handlelength=1, handleheight=1,
              columnspacing=1.4, borderaxespad=0.4)


def save(fig, out_dir, name, table_header, table_rows, log):
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{name}.{ext}", facecolor=SURFACE)
    plt.close(fig)
    with open(out_dir / f"{name}.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(table_header)
        writer.writerows(table_rows)
    log(f"  {name}.png / .pdf / .csv")


# --- figures ----------------------------------------------------------------

def fig_pass_rate(rows, out_dir, log=print):
    """Pass rate per treatment: one bar per model, grouped by treatment."""
    models, treatments = models_in(rows), treatments_in(rows)
    count = {}
    for model, t, _, passes, _ in rows:
        n, p = count.get((model, t), (0, 0))
        count[(model, t)] = (n + 1, p + passes)

    gap = 0.9                                   # extra space between groups
    ys, centers, y = {}, [], 0.0
    for t in treatments:
        for i, m in enumerate(models):
            ys[(m, t)] = y + i
        centers.append(y + (len(models) - 1) / 2)
        y += len(models) + gap
    bottom = y - gap - 1

    fig, ax = new_figure(0.3 * len(models) * len(treatments) + 0.25 * len(treatments))
    ax.set_xlim(0, 1.24)                        # room for the label after a full bar
    ax.set_ylim(bottom + 0.7, -0.7)             # first treatment at the top
    ax.set_yticks(centers, [TREATMENTS.get(t, t) for t in treatments])
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("generated assertions that pass on the live app", color=INK_2, fontsize=9, labelpad=8)
    finish_layout(fig)
    dx, dy = data_per_px(ax)

    table = []
    for (m, t), yb in ys.items():
        n, p = count.get((m, t), (0, 0))
        rate = p / n if n else 0.0
        hbar(ax, yb, 0, rate, SERIES[models.index(m)], True, dx, dy)
        ax.text(rate + 6 * PX * dx, yb, f"{rate:.0%}  ({p}/{n})", va="center", ha="left",
                fontsize=8.5, color=INK)
        table.append([MODELS.get(m, m), t, n, p, f"{rate:.4f}"])

    tests = max(n for n, _ in count.values())
    apps = len({r[2] for r in rows})
    header(fig, ax, "Generated assertions that pass, by context given to the model",
           f"{tests} BEWT tests in {apps} apps, 1 run per model; context is cumulative "
           "(each treatment adds one source)",
           [MODELS.get(m, m) for m in models], ncol=len(models))
    save(fig, out_dir, "fig1_pass_rate",
         ["model", "treatment", "answers", "passed", "pass_rate"], table, log)


def fig_categories(rows, out_dir, log=print, name="fig2_categories", title=None):
    """Final category of every answer: one stacked bar per treatment, the
    treatments of each model grouped under the model's name. ``rows`` may be
    one app's answers only (see write_figures)."""
    models, treatments = models_in(rows), treatments_in(rows)
    keys = [k for k, _ in CATEGORIES]
    counts = {}
    for model, t, _, _, category in rows:
        c = counts.setdefault((model, t), dict.fromkeys(keys, 0))
        c[category if category in c else "not_executable"] += 1
    total = max(sum(c.values()) for c in counts.values())

    bars, heads, y = [], [], 0.0
    for m in models:
        heads.append((y, MODELS.get(m, m)))
        y += 0.9
        for t in treatments:
            bars.append((y, m, t))
            y += 1.0
        y += 0.4
    bottom = bars[-1][0]

    fig, ax = new_figure(0.52 * len(bars) + 0.3 * len(models), legend_rows=2)
    ax.set_xlim(0, total)
    ax.set_ylim(bottom + 0.7, -0.4)
    ax.set_yticks([b[0] for b in bars], [TREATMENTS.get(t, t) for _, _, t in bars])
    tick = 25 if total > 100 else 10 if total > 30 else 5
    ax.set_xticks(list(range(0, total + 1, tick)))
    ax.set_xlabel(f"answers (of {total} tests)", color=INK_2, fontsize=9, labelpad=8)
    finish_layout(fig)
    dx, dy = data_per_px(ax)

    for yh, heading in heads:
        ax.text(-8 * PX * dx, yh, heading, ha="right", va="center", fontsize=10, color=INK,
                fontweight="bold", clip_on=False)
    table = []
    for yb, m, t in bars:
        c = counts.get((m, t), dict.fromkeys(keys, 0))
        present = [k for k in keys if c[k]]
        left, prev_x, level = 0.0, None, 0
        for i, k in enumerate(present):
            n = c[k]
            color = SERIES[keys.index(k)]
            last = i == len(present) - 1
            width = n - (0 if last else GAP * dx)
            hbar(ax, yb, left, width, color, last, dx, dy)
            x = left + width / 2
            if width / dx >= 18 * PX:           # fits inside the segment
                ax.text(x, yb, str(n), va="center", ha="center", fontsize=8, fontweight="bold",
                        color=INK if color in DARK_TEXT_ON else "#ffffff")
                prev_x, level = None, 0
            else:                               # above it; close neighbours alternate height
                level = 1 - level if prev_x is not None and (x - prev_x) / dx < 12 * PX else 0
                ax.text(x, yb - (BAR / 2 + 3 * PX + level * 11 * PX) * dy, str(n),
                        va="bottom", ha="center", fontsize=7.5, color=INK)
                prev_x = x
            left += n
        table.append([MODELS.get(m, m), t] + [c[k] for k in keys])

    header(fig, ax, title or "Final category of each generated assertion",
           "decided by execution; answers that pass but differ from the gold were reviewed manually",
           [label for _, label in CATEGORIES], ncol=3)
    save(fig, out_dir, name, ["model", "treatment"] + keys, table, log)


def write_figures(output_dir: Path, log=print) -> Path | None:
    """Draw every figure from <output_dir>/results.db; returns the figures
    folder, or None when there are no benchmark results yet."""
    output_dir = Path(output_dir)
    db_path = output_dir / "results.db"
    rows = load(db_path) if db_path.exists() else []
    if not rows:
        return None
    out_dir = output_dir / "reports" / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"font.family": "DejaVu Sans"}):
        fig_pass_rate(rows, out_dir, log)
        fig_categories(rows, out_dir, log)
        found = {r[2] for r in rows}
        for app in [a for a in APPS if a in found] + sorted(found - set(APPS)):
            app_rows = [r for r in rows if r[2] == app]
            tests = max(sum(1 for r in app_rows if (r[0], r[1]) == key)
                        for key in {(r[0], r[1]) for r in app_rows})
            fig_categories(app_rows, out_dir, log, name=f"fig3_categories_{app}",
                           title=f"Final category of each generated assertion: {APPS.get(app, app)} "
                                 f"({tests} tests)")
    return out_dir
