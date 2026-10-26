"""Thesis figures from the pipeline's reports (static PNG + PDF, light theme).

Reads output/reports/by_treatment.csv, output/reports/crawled_comparison.csv
and, when present, output/generated-suite/<app>/manifest.json; writes the
figures to output/reports/figures/. The CSVs are the figures' table view.

    python scripts/make_thesis_charts.py [--app bludit]
"""

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

# Validated categorical slots (light mode), chart chrome and ink.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
SURFACE = "#fcfcfb"
INK, INK_2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"
# Label ink inside a fill: white, except dark ink on yellow.
INSIDE_INK = {"#eda100": INK}

DPI = 200
_PX = DPI / 96       # sizes below are CSS px; the figure renders at DPI
BAR_PX = 22 * _PX    # bar thickness (<= 24px)
ROUND_PX = 4 * _PX   # rounded data-end
GAP_PX = 2 * _PX     # surface gap between adjacent fills

TREATMENT_LABELS = {
    "A": "A  test code only",
    "B": "B  + Gherkin comments",
    "C": "C  + page HTML",
    "D": "D  + Page Objects",
}


def _style(ax, xlabel):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9, length=0, pad=6)
    ax.tick_params(axis="y", colors=INK_2, labelsize=10, length=0, pad=8)
    ax.grid(axis="x", color=GRID, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9, labelpad=8)


def _figure(rows, width_in=7.2, header_in=0.75):
    height_in = header_in + 0.75 + 0.5 * rows
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    return fig, ax


def _px_to_data(ax):
    """Data units per pixel along x and y, for the current limits."""
    fig = ax.figure
    fig.canvas.draw()
    bbox = ax.get_window_extent()
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    return (x1 - x0) / bbox.width, abs(y1 - y0) / bbox.height


def _hbar(ax, y, left, width, color, round_end, dx, dy):
    """Horizontal bar, square at the baseline, rounded at its data-end."""
    h = BAR_PX * dy
    r_x, r_y = min(ROUND_PX * dx, width / 2), min(ROUND_PX * dy, h / 2)
    x0, x1, y0, y1 = left, left + width, y - h / 2, y + h / 2
    if not round_end or width <= 0:
        verts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        codes = [MPath.MOVETO] + [MPath.LINETO] * 3 + [MPath.CLOSEPOLY]
    else:
        verts = [(x0, y0), (x1 - r_x, y0), (x1, y0), (x1, y0 + r_y), (x1, y1 - r_y),
                 (x1, y1), (x1 - r_x, y1), (x0, y1), (x0, y0)]
        codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO,
                 MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, edgecolor="none"))


def _layout(fig, header_in):
    """Final axes position (before any pixel-based geometry is measured),
    leaving a fixed-height band at the top for the title, subtitle and legend."""
    fig.tight_layout(rect=(0, 0, 1, 1 - header_in / fig.get_figheight()))


def _save(fig, out_dir, name):
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{name}.{ext}", facecolor=SURFACE)
    plt.close(fig)
    print(f"  {name}.png / .pdf")


def _title(fig, title, subtitle):
    h = fig.get_figheight()
    fig.text(0.02, 1 - 0.18 / h, title, ha="left", va="top", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.02, 1 - 0.46 / h, subtitle, ha="left", va="top", fontsize=9, color=INK_2)


def _single_series_bars(rows, xlabel, title, subtitle, out_dir, name, xmax=1.0, pct=True):
    """rows: [(label, value, value_label)] drawn top to bottom in one color."""
    fig, ax = _figure(len(rows))
    labels = [r[0] for r in rows]
    ys = list(range(len(rows)))[::-1]
    ax.set_xlim(0, xmax)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticks(ys, labels)
    if pct:
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0], ["0%", "25%", "50%", "75%", "100%"])
    _style(ax, xlabel)
    _layout(fig, 0.75)
    dx, dy = _px_to_data(ax)
    for y, (_, value, text) in zip(ys, rows):
        _hbar(ax, y, 0, value, SERIES[0], True, dx, dy)
        if value > 0.8 * xmax:   # no room after the bar: label inside its end
            ax.text(value - 8 * _PX * dx, y, text, va="center", ha="right", fontsize=9,
                    color="#ffffff", fontweight="bold")
        else:
            ax.text(value + 6 * _PX * dx, y, text, va="center", ha="left", fontsize=9, color=INK)
    _title(fig, title, subtitle)
    _save(fig, out_dir, name)


def _stacked_bars(groups, categories, xlabel, title, subtitle, out_dir, name, total):
    """groups: [(label, {category: count})]; categories in fixed slot order."""
    fig, ax = _figure(len(groups), header_in=1.05)
    ys = list(range(len(groups)))[::-1]
    ax.set_xlim(0, total)
    ax.set_ylim(-0.6, len(groups) - 0.4)
    ax.set_yticks(ys, [g[0] for g in groups])
    step = 5 if total <= 30 else 10
    ax.set_xticks(list(range(0, total + 1, step)))
    _style(ax, xlabel)
    _layout(fig, 1.05)
    dx, dy = _px_to_data(ax)
    for y, (_, counts) in zip(ys, groups):
        present = [c for c in categories if counts.get(c, 0)]
        left = 0.0
        for i, cat in enumerate(present):
            n = counts[cat]
            color = SERIES[categories.index(cat)]
            last = i == len(present) - 1
            width = n - (0 if last else GAP_PX * dx)
            _hbar(ax, y, left, width, color, last, dx, dy)
            label = str(n)
            if width / dx >= 18 * _PX:   # fits inside with padding
                ax.text(left + width / 2, y, label, va="center", ha="center", fontsize=8.5,
                        color=INSIDE_INK.get(color, "#ffffff"), fontweight="bold")
            else:                  # too narrow: label above the segment
                ax.text(left + width / 2, y + (BAR_PX / 2 + 7 * _PX) * dy, label, va="bottom",
                        ha="center", fontsize=8, color=INK)
            left += n
    handles = [plt.Rectangle((0, 0), 1, 1, color=SERIES[i]) for i in range(len(categories))]
    ax.legend(handles, categories, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=len(categories),
              frameon=False, fontsize=9, labelcolor=INK_2, handlelength=1, handleheight=1,
              columnspacing=1.4, borderaxespad=0.4)
    _title(fig, title, subtitle)
    _save(fig, out_dir, name)


def _crawl_graph(crawl_dir, out_dir, name):
    """The crawl's state-flow graph: states left to right by click depth from
    the start page; the three most frequent pages in colour, the rest gray
    with their page named on the node."""
    from collections import Counter, deque
    from matplotlib.patches import FancyArrowPatch
    from urllib.parse import urlparse

    result = json.loads((crawl_dir / "result.json").read_text())
    states = result["states"]
    edges = [(e["from"], e["to"]) for e in result["edges"]]
    page = {n: {"": "login", "admin": "login"}.get(urlparse(st["url"]).path.rstrip("/").split("/")[-1],
                                                  urlparse(st["url"]).path.rstrip("/").split("/")[-1])
            for n, st in states.items()}
    start = next(n for n in states if n == "index")
    depth, queue = {start: 0}, deque([start])
    out = {}
    for a, b in edges:
        out.setdefault(a, []).append(b)
    while queue:
        n = queue.popleft()
        for m in out.get(n, []):
            if m not in depth:
                depth[m] = depth[n] + 1
                queue.append(m)
    top = [pg for pg, _ in Counter(page.values()).most_common(3)]
    color = {pg: SERIES[i] for i, pg in enumerate(top)}
    other = "#b4b2ab"

    columns = {}
    for n in sorted(states, key=lambda n: (top.index(page[n]) if page[n] in top else 9, page[n],
                                           int(n.replace("state", "") or 0) if n != "index" else -1)):
        columns.setdefault(depth.get(n, max(depth.values()) + 1), []).append(n)
    tallest = max(len(c) for c in columns.values())
    pos = {}
    for d, nodes in columns.items():
        for i, n in enumerate(nodes):
            pos[n] = (d, (tallest - 1) / 2 - i + (len(nodes) - 1) / 2 - (tallest - 1) / 2)

    fig, ax = plt.subplots(figsize=(7.2, 1.05 + 0.32 * tallest), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axis("off")
    for a, b in edges:
        (x0, y0), (x1, y1) = pos[a], pos[b]
        back = x1 <= x0
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=6,
                                     connectionstyle=f"arc3,rad={0.35 if back else 0.08}",
                                     color=BASELINE if not back else GRID, linewidth=0.8,
                                     shrinkA=5, shrinkB=5, zorder=1))
    for n, (x, y) in pos.items():
        c = color.get(page[n], other)
        ax.scatter([x], [y], s=46, color=c, edgecolors=SURFACE, linewidths=1.5, zorder=3)
        if page[n] not in color:
            ax.text(x, y - 0.42, page[n], ha="center", va="top", fontsize=5.5, color=INK_2, zorder=4)
    ax.set_xlim(-0.5, max(x for x, _ in pos.values()) + 0.5)
    ax.set_ylim(min(y for _, y in pos.values()) - 0.9, max(y for _, y in pos.values()) + 0.3)
    handles = [plt.Line2D([], [], marker="o", linestyle="", markersize=6, color=color[pg]) for pg in top]
    handles.append(plt.Line2D([], [], marker="o", linestyle="", markersize=6, color=other))
    fig.legend(handles, [f"/admin/{pg}" for pg in top] + ["other pages (named)"], loc="upper left",
               bbox_to_anchor=(0.012, 1 - 0.68 / fig.get_figheight()), ncol=4, frameon=False, fontsize=8,
               labelcolor=INK_2, handletextpad=0.3, columnspacing=1.2, borderaxespad=0)
    _title(fig, "Crawl: state-flow graph of Bludit",
           f"{len(states)} DOM states · {len(edges)} transitions · columns = clicks from login · "
           "pale arrows lead back")
    _layout(fig, 1.05)
    _save(fig, out_dir, name)


def _read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default="bludit")
    parser.add_argument("--output", default="output")
    parser.add_argument("--crawl-dir", default=None,
                        help="Crawljax crawl folder (default: ~/dante/dante/applications/<app>/localhost/crawl-with-inputs)")
    args = parser.parse_args()
    out = Path(args.output)
    reports = out / "reports"
    figures = reports / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams["font.family"] = "DejaVu Sans"

    # 1-2. benchmark (BEWT, gold assertions)
    bench = [r for r in _read_csv(reports / "by_treatment.csv")]
    model = bench[0]["model"]
    tests = int(bench[0]["tests"])
    _single_series_bars(
        [(TREATMENT_LABELS[r["treatment"]], float(r["pass_rate"]),
          f"{float(r['pass_rate']):.0%}  ({round(float(r['pass_rate']) * int(r['answers']))}/{r['answers']})")
         for r in bench],
        "generated assertions that pass on the live app",
        "Benchmark: assertions that pass, by context given to the model",
        f"Bludit, {tests} BEWT tests, {model}, 1 run each; context is cumulative (each treatment adds one source)",
        figures, "fig1_benchmark_pass_rate",
    )
    categories = ["correct", "under-assertive", "wrong assertion", "not executable"]
    _stacked_bars(
        [(TREATMENT_LABELS[r["treatment"]], {
            "correct": int(r["correct"]), "under-assertive": int(r["under_assertive"]),
            "wrong assertion": int(r["wrong_assertion"]), "not executable": int(r["not_executable"]),
        }) for r in bench],
        categories, f"answers (of {tests})",
        "Benchmark: final category of each generated assertion",
        "decided by execution; answers that pass but differ from the gold were reviewed manually",
        figures, "fig2_benchmark_categories", total=tests,
    )

    # 3-4. crawled (DANTE/Crawljax) tests
    crawled = _read_csv(reports / "crawled_comparison.csv")
    llm = next(r for r in crawled if r["oracle"] == "LLM assertions")
    oracle = next((r for r in crawled if r["oracle"].startswith("Crawljax")), None)
    manifest_path = out / "generated-suite" / args.app / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None

    rows = []
    if oracle:
        o_n, o_ok = int(oracle["total"]), int(oracle["holds_on_live_app"])
        rows.append(("Crawljax oracle\n(whole-page equality, per path)", o_ok / o_n, f"{o_ok / o_n:.0%}  ({o_ok}/{o_n} paths)"))
    l_n, l_ok = int(llm["total"]), int(llm["holds_on_live_app"])
    rows.append(("LLM, whole answer\n(per test)", l_ok / l_n, f"{l_ok / l_n:.0%}  ({l_ok}/{l_n} tests)"))
    if manifest:
        a_n, a_ok = manifest["assertions_generated"], manifest["assertions_kept"]
        rows.append(("LLM, single assertions\n(per statement)", a_ok / a_n, f"{a_ok / a_n:.0%}  ({a_ok}/{a_n} assertions)"))
    _single_series_bars(
        rows, "checks that hold on the correct app (higher = fewer false alarms)",
        "Crawled tests: which oracle holds on a correct application",
        "Bludit crawled by Crawljax/DANTE; every check run on the app state it was generated for",
        figures, "fig3_crawled_oracles_hold",
    )
    crawled_categories = ["semantic", "structural", "wrong assertion", "not executable"]
    _stacked_bars(
        [("LLM answers", {"semantic": int(llm["semantic"]), "structural": int(llm["structural"]),
                          "wrong assertion": int(llm["wrong_assertion"]),
                          "not executable": int(llm["not_executable"])})],
        crawled_categories, f"crawled tests (of {l_n})",
        "Crawled tests: category of each LLM answer",
        "semantic / structural = holds on the app, judged on meaningfulness; others decided by execution",
        figures, "fig4_crawled_categories", total=l_n,
    )
    crawl_dir = Path(args.crawl_dir or Path.home() / "dante" / "dante" / "applications" / args.app
                     / "localhost" / "crawl-with-inputs").expanduser()
    if (crawl_dir / "result.json").exists():
        _crawl_graph(crawl_dir, figures, "fig5_crawl_state_flow_graph")
    print(f"Figures written to {figures}")


if __name__ == "__main__":
    main()
