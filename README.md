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

## Apps and systems

Apps: `bludit`, `joomla`, `mantisbt`, `claroline`, `kanboard`. Not set up:
`expresscart`, `mediawiki`, `prestashop` (their gold tests fail on macOS).

Verified on macOS (Apple Silicon, Docker Desktop). Intel Macs, Linux and
Windows should work but are untested. Gold tests depend on the machine
(window size, keyboard, speed), so on every new machine `reference-run` and
`verify-execution` must pass for an app before its results count.

- Windows: set `PYTHONUTF8=1`; activate with `.venv\Scripts\activate`.
- Apps sharing a port run one at a time: joomla/claroline (3000),
  bludit/kanboard (8080). The harness stops the other app's container.
