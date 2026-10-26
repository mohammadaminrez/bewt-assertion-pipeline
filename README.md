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
bewt-pipeline verify-execution --app bludit     # recommended
bewt-pipeline run --app bludit --model gpt-4o-mini --execute
bewt-pipeline export-excel                      # only if answers need review
bewt-pipeline import-excel                      #   (after filling the Excel)
bewt-pipeline report
```
