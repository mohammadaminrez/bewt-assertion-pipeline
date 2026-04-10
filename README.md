# BEWT Assertion Pipeline

Evaluates how well LLMs generate Selenium test assertions under three context levels: (A) code only, (B) code + Gherkin comment, (C) code + comment + HTML page.

## Prerequisites

- Python 3.9+
- The [BEWT benchmark repo](https://github.com/nicorubi/BEWT) cloned at `../BEWT` (sibling directory)
- For full execution mode (optional): Maven and Docker

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp .env.example .env          # add your OPENAI_API_KEY / ANTHROPIC_API_KEY
bewt-pipeline run --app expresscart --model gpt-4o --treatment A --treatment B --skip-execution
bewt-pipeline report
```

`--skip-execution` computes similarity and exact match without compiling or running tests. To also measure functional pass rate, omit the flag (requires Maven + Docker + the web app running).

## How It Works

1. **Parse** -- Reads Java/Selenium test files from the BEWT benchmark (8 web apps, 362 assertions) and extracts gold-standard assertions.
2. **Strip** -- Removes assertions from each test, replacing them with a `// TODO` placeholder.
3. **Generate variants** -- Creates three versions per test: A (no hint), B (descriptive comment from Gherkin `Then` clauses), C (comment + captured HTML page source).
4. **Prompt LLM** -- Sends each variant to a configured model (GPT-4o, GPT-4o-mini, Claude Sonnet, Claude Haiku) with a system prompt requesting only assertion code.
5. **Extract** -- Parses the LLM response to isolate the generated assertion statement(s).
6. **Evaluate** -- Computes exact match, weighted semantic similarity (type + expected values + Levenshtein), and classifies errors (correct, over/under-assertive, wrong, not executable).
7. **Execute** (optional) -- Injects the generated assertion into a Maven project copy, compiles, and runs against a Dockerized app to measure functional pass rate.
8. **Report** -- Outputs per-treatment, per-app, and per-model CSVs, LaTeX tables, and Friedman/Wilcoxon/Cliff's delta statistical tests.
9. **Store** -- All results are saved to SQLite with a unique key per (app, test, treatment, model), enabling resumable runs.
10. **Verify** -- Run `python tests/verify_pipeline.py` to trace individual test cases through every stage.

## CLI Commands

| Command | Description |
|---|---|
| `bewt-pipeline parse` | Parse tests, extract assertions |
| `bewt-pipeline generate-variants` | Write A/B/C variant files to disk |
| `bewt-pipeline capture-html` | Deploy apps, capture HTML pages |
| `bewt-pipeline run` | Full experiment: parse → prompt LLM → evaluate → report |
| `bewt-pipeline report` | Generate reports from stored results |
| `bewt-pipeline info` | Show configured apps and test counts |

## 8 Web Apps Under Test

Bludit, Claroline, ExpressCart, Joomla, Kanboard, MantisBT, MediaWiki, PrestaShop -- all with Docker-based deployment.
