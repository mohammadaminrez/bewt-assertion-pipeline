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
(`gpt-4o-mini`, `claude-haiku`, `gemini-flash`, …); only its provider's key
is needed. All models are in `config/models.yaml`.

## Apps and systems

**Apps:** `bludit`, `joomla`, `mantisbt`, `claroline`, `kanboard`.
`expresscart`, `mediawiki` and `prestashop` are not set up (their gold tests
fail on macOS).

**Systems:** tested on macOS (Apple Silicon, Docker Desktop); Linux and
Windows should work but are untested. Gold tests depend on the machine, so
`reference-run` and `verify-execution` must pass on each new machine.

- Windows: activate with `.venv\Scripts\activate`.
- joomla/claroline (port 3000) and bludit/kanboard (8080) share ports and run
  one at a time; the harness stops the other app.
