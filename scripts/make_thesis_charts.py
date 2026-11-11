"""Draw the thesis figures from output/results.db (also done by `bewt-pipeline report`).

    python scripts/make_thesis_charts.py [--output output]
"""

import argparse

from src.evaluation.figures import write_figures

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("--output", default="output", help="pipeline output folder")
folder = write_figures(parser.parse_args().output)
if folder is None:
    raise SystemExit("No benchmark results yet")
print(f"Figures written to {folder}")
