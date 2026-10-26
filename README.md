# BEWT Assertion Pipeline

Evaluates how well LLMs generate Selenium test assertions under varying amounts
of context. Context is added step by step — each treatment contains everything
the previous one had, plus one more source:
- **Treatment A** -- test code only (assertion removed)
- **Treatment B** -- A + Gherkin comments
- **Treatment C** -- B + HTML page source
- **Treatment D** -- C + project code (Page Objects, Strings.java) — the full condition

Answers *"does each extra layer help, added in this order?"*

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e .
cp .env.example .env          # add your OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY
```

## Running Experiments

Validated end to end on **Bludit** (other apps need their own `docker`
settings in `config/apps.yaml` and a passing reference run first).

```bash
# 1. Reference run: the gold suite in TestSuite order on a fresh app container.
#    Saves the app state after every test and the page HTML at each assertion
#    point (needed for treatments C/D and for --execute).
bewt-pipeline reference-run --app bludit

# 2. Check the execution harness: every gold assertion must pass and its
#    logical negation must fail when run through the same path.
bewt-pipeline verify-execution --app bludit

# 3. Generate and execute: each generated assertion runs alone, on the app
#    state saved just before its test. --runs repeats every LLM call.
bewt-pipeline run --app bludit --model gpt-4o-mini --execute --runs 5
```

How each part is built:

- **A** – the test with its assertion(s) replaced by a placeholder. Assertions
  separated by other code get numbered placeholders, answered under `// (n)`.
- **B** – A + the Gherkin Then-clauses as a comment (none if the benchmark has
  no spec for the test; the prompt then mentions no comment).
- **C** – B + the whole page at the assertion point, deterministically cleaned
  (no head/scripts/styles/SVG/comments), never cut.
- **D** – C + the project's Page Objects (and `Strings.java`, if present).

The model's code is injected as written. Categories are decided by execution
where that is certain:

| Outcome | Category |
|---|---|
| does not compile, or throws a non-assertion exception | `not_executable` |
| runs, an assertion fails | `wrong_assertion` |
| passes, identical to the gold (whitespace-insensitive) | `correct` |
| passes, differs from the gold | `needs_review` → manual label |

## Manual Review

Only passing-but-different answers need a human, and each unique answer is
reviewed once (the label applies to every run/treatment where it occurred):

```bash
bewt-pipeline export-excel --pre-classify   # optional LLM suggestions
# fill "Manual Classification": correct | over_assertive | under_assertive | wrong_assertion
bewt-pipeline import-excel
bewt-pipeline report
```

## Reports

`bewt-pipeline report` writes to `output/reports/`:

- `by_treatment.csv` – per model and treatment: pass rate (mean, SD, min, max
  across runs), compile and exact-match rates, final categories, and how many
  answers are still unreviewed.
- `statistical_tests.json` – unit = test. Primary: per-test pass rate across
  runs, Friedman + pairwise Wilcoxon signed-rank (rank-biserial effect size,
  Holm-adjusted). Robustness: majority vote, Cochran's Q + exact McNemar
  (Holm). Model comparison per treatment when several models ran.
- `latex_tables.tex`, `raw_results.csv`, `by_app.csv`, `by_model.csv`.

Textual similarity to the gold is reported for description only: a
non-compiling answer can be textually very close to the gold.

## Observability

Every real LLM call is stored in `output/results.db` (prompt, reply, tokens,
cost, latency, served model snapshot) and mirrored to PostHog LLM analytics
when `POSTHOG_PROJECT_TOKEN`/`POSTHOG_HOST` are set. Replies replayed from the
local cache are flagged `from_cache`, cost nothing in usage summaries, and are
not sent to PostHog.

## Prerequisites

- Python 3.9+
- [BEWT repo](https://github.com/nicorubi/BEWT) cloned at `../BEWT`
- Docker, Maven and a JDK for the reference run and `--execute` (Selenium
  downloads the Chrome version the BEWT suites pin)

## CLI Commands

| Command | Description |
|---|---|
| `bewt-pipeline info` | Show configured apps and test counts |
| `bewt-pipeline parse` | Parse tests, extract assertions |
| `bewt-pipeline generate-variants` | Write A/B variant files to disk |
| `bewt-pipeline reference-run --app <name>` | Gold suite on a fresh app: state snapshots + HTML |
| `bewt-pipeline verify-execution --app <name>` | Positive/negative controls of the execution harness |
| `bewt-pipeline run` | Prompt the LLM, execute, classify (`--runs`, `--execute`) |
| `bewt-pipeline export-excel` | Export answers needing manual review |
| `bewt-pipeline import-excel` | Import manual review labels |
| `bewt-pipeline report` | Regenerate reports from stored results |
| `bewt-pipeline llm-usage` | Real API usage (tokens, cost, latency) |

## Testing

```bash
pip install -e ".[dev]" && pytest tests/ -v
```

## Apps

Bludit, Claroline, ExpressCart, Joomla, Kanboard, MantisBT, MediaWiki, PrestaShop.
