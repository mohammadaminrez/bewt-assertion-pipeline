# BEWT Assertion Pipeline

Evaluates how well LLMs generate Selenium test assertions under three context levels:
- **A** -- test code only
- **B** -- test code + Gherkin comment
- **C** -- test code + comment + HTML page

## Quick Start (Treatments A & B)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e .
cp .env.example .env          # add your API keys
bewt-pipeline run --app mantisbt --model gpt-4o-mini --treatment A --treatment B
bewt-pipeline report
```

## Treatment C (requires Maven + running app)

```bash
brew install maven                                    # if not installed
bewt-pipeline setup-app --app mantisbt                # deploy + configure via Docker
bewt-pipeline capture-html --app mantisbt             # capture HTML pages
bewt-pipeline run --app mantisbt --model gpt-4o-mini --treatment A --treatment B --treatment C
```

## Prerequisites

- Python 3.9+
- [BEWT repo](https://github.com/nicorubi/BEWT) cloned at `../BEWT`
- For Treatment C and `--execute`: Maven, Chrome, and the web app running at its configured URL

## CLI Commands

| Command | Description |
|---|---|
| `bewt-pipeline info` | Show configured apps and test counts |
| `bewt-pipeline parse` | Parse tests, extract assertions |
| `bewt-pipeline generate-variants` | Write A/B/C variant files to disk |
| `bewt-pipeline setup-app --app <name>` | Deploy app + run installer to configure it |
| `bewt-pipeline capture-html --app <name>` | Capture HTML pages for Treatment C |
| `bewt-pipeline run` | Run experiment: prompt LLM, evaluate, report |
| `bewt-pipeline report` | Regenerate reports from stored results |

## Testing

```bash
pip install -e ".[dev]" && pytest tests/ -v
```

## Apps

Bludit, Claroline, ExpressCart, Joomla, Kanboard, MantisBT, MediaWiki, PrestaShop.
