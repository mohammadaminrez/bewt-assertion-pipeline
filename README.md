# BEWT Assertion Pipeline

Evaluates how well LLMs generate Selenium test assertions under four context levels:
- **Treatment A** -- test code only (assertion removed)
- **Treatment B** -- test code + Gherkin comments
- **Treatment C** -- test code + Gherkin comments + HTML page source
- **Treatment D** -- test code + Gherkin comments + HTML page source + project code (Page Objects, Strings.java)

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e .
cp .env.example .env          # add your OPENAI_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY
```

## Running Experiments

### Treatments A & B (no app deployment needed)

```bash
bewt-pipeline run --app mantisbt --model gpt-4o-mini --treatment A --treatment B
```

### Treatments C & D (requires Maven + running app)

```bash
brew install maven                                    # if not installed
bewt-pipeline setup-app --app mantisbt                # deploy + configure via Docker
bewt-pipeline capture-html --app mantisbt             # capture HTML pages
bewt-pipeline run --app mantisbt --model gpt-4o-mini \
  --treatment A --treatment B --treatment C --treatment D
```

### With live test execution

```bash
bewt-pipeline run --app mantisbt --model gpt-4o-mini \
  --treatment A --treatment B --treatment C --treatment D --execute
```

## Manual Evaluation Workflow

Auto-classification of assertion quality (over/under-assertive) is undecidable in general. The pipeline uses an Excel-based manual evaluation workflow with LLM pre-classification as suggestions:

```bash
# 1. Export results to Excel with LLM pre-filled suggestions
bewt-pipeline export-excel --pre-classify

# 2. Open the Excel, review/correct the "Manual Classification" column
#    Classifications: correct, over_assertive, under_assertive, wrong_assertion, not_executable

# 3. Import your manual annotations back into the database
bewt-pipeline import-excel

# 4. Generate final reports from manual classifications
bewt-pipeline report
```

The Excel includes a "Classification Guide" sheet with definitions and examples for each category.

## Prerequisites

- Python 3.9+
- [BEWT repo](https://github.com/nicorubi/BEWT) cloned at `../bewt`
- For Treatment C/D and `--execute`: Maven, Chrome, and the web app running at its configured URL

## CLI Commands

| Command | Description |
|---|---|
| `bewt-pipeline info` | Show configured apps and test counts |
| `bewt-pipeline parse` | Parse tests, extract assertions |
| `bewt-pipeline generate-variants` | Write A/B/C/D variant files to disk |
| `bewt-pipeline setup-app --app <name>` | Deploy app + run installer to configure it |
| `bewt-pipeline capture-html --app <name>` | Capture HTML pages for Treatment C/D |
| `bewt-pipeline run` | Run experiment: prompt LLM, evaluate, report |
| `bewt-pipeline export-excel` | Export results to Excel for manual evaluation |
| `bewt-pipeline import-excel` | Import manual classifications from annotated Excel |
| `bewt-pipeline report` | Regenerate reports from stored results |

## Testing

```bash
pip install -e ".[dev]" && pytest tests/ -v
```

## Apps

Bludit, Claroline, ExpressCart, Joomla, Kanboard, MantisBT, MediaWiki, PrestaShop.
