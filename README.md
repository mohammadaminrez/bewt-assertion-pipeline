# BEWT Assertion Pipeline

Evaluates LLM-generated Selenium assertions on the BEWT benchmark.

Requires Python 3.9+, Docker, Maven and JDK 11+.

## Quick Start

```bash
git clone https://github.com/mohammadaminrez/bewt-assertion-pipeline.git
git clone https://github.com/OlianasD/BEWT.git
cd bewt-assertion-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env                 # set OPENAI_API_KEY

bewt-pipeline reference-run --app bludit
bewt-pipeline verify-execution --app bludit     # must pass on a new machine
bewt-pipeline run --app bludit --model gpt-4o-mini --execute
bewt-pipeline export-excel                      # only if answers need review
bewt-pipeline import-excel                      #   (after filling the Excel)
bewt-pipeline report
```

**Models:** OpenAI, Anthropic and Gemini. Choose one with `--model`
(`gpt-4o-mini`, `claude-haiku`, `gemini-flash`, …). For example, with
`ANTHROPIC_API_KEY` set in `.env`:

```bash
bewt-pipeline run --app bludit --model claude-haiku --execute
```

## DANTE extension (crawled tests)

Crawljax/DANTE explores the app and generates Selenium tests without
assertions; the LLM then writes them. Needs DANTE next to this repo
(`~/dante`):
https://github.com/matteobiagiola/ICST20-submission-material-DANTE

**Apps:** `bludit`, `kanboard`, `joomla`, `mantisbt`, `claroline` (each
crawled as the logged-in admin).

```bash
# 1. Crawl (10 min) + generate DANTE's test project
bewt-pipeline crawl --app <app>                 # --skip-generate: crawl only

# 2. Optional: the graph page
python3 scripts/make_crawl_graph.py ~/dante/dante/applications/<app>/localhost/crawl-with-inputs
cd ~/dante/dante/applications/<app>/localhost/crawl-with-inputs && python3 -m http.server 8765
# then open http://localhost:8765/graph.html (Ctrl+C stops the server)
```

A new crawl overwrites the previous one in `~/dante/dante/applications/<app>/`.

## Apps and systems

**Apps:** `bludit`, `joomla`, `mantisbt`, `claroline`, `kanboard`.

`expresscart`, `mediawiki` and `prestashop` are not set up (their gold tests
fail on macOS).

**Systems:** gold tests depend on the machine, so `reference-run` and
`verify-execution` must pass on each new machine.

- Windows: activate with `.venv\Scripts\activate`.
