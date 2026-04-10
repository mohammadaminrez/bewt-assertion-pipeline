# BEWT Assertion Pipeline

Evaluates how well LLMs generate Selenium test assertions under three context levels:
- **A** -- test code only
- **B** -- test code + Gherkin comment
- **C** -- test code + comment + HTML page

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e .
cp .env.example .env          # add your API keys
bewt-pipeline run --app expresscart --model gpt-4o --treatment A --treatment B
bewt-pipeline report
```

By default the pipeline computes similarity and exact match only. Add `--execute` to compile and run assertions against live apps (requires Maven + Docker).

## Prerequisites

- Python 3.9+
- [BEWT repo](https://github.com/nicorubi/BEWT) cloned at `../BEWT`
- For `--execute` mode: Maven + Docker

## CLI Commands

| Command | Description |
|---|---|
| `bewt-pipeline info` | Show configured apps and test counts |
| `bewt-pipeline parse` | Parse tests, extract assertions |
| `bewt-pipeline generate-variants` | Write A/B/C variant files to disk |
| `bewt-pipeline run` | Run experiment: prompt LLM, evaluate, report |
| `bewt-pipeline report` | Regenerate reports from stored results |
| `bewt-pipeline capture-html` | Capture HTML pages for treatment C |

## Testing

```bash
pip install -e ".[dev]" && pytest tests/ -v
```

## Apps

Bludit, Claroline, ExpressCart, Joomla, Kanboard, MantisBT, MediaWiki, PrestaShop.
