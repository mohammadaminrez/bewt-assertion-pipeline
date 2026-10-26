from __future__ import annotations

"""CLI entry point for the BEWT assertion generation pipeline."""

import json
import os
import shutil
import csv
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import click

from .config import Config
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin
from .models import ExperimentResult, ErrorCategory
from .variants.generator import write_variants
from .variants.html_capture import capture_html_static
from .execution.reference import ReferenceRunError, build_reference
from .execution.controls import run_controls
from .evaluation.reporter import generate_full_report
from .evaluation.excel_export import (
    export_review_to_excel,
    import_classifications_from_excel,
    import_review_labels,
    review_groups,
)
from .evaluation.prompt_export import export_llm_calls
from .evaluation.usage_report import export_treatment_comparison
from .data.store import ResultStore
from .runner import MissingContextError, run_experiment, run_crawled_experiment, count_experiments
from .llm.prompt_builder import TREATMENTS, CRAWLED_TREATMENTS


def _validate_bewt_repo(config: Config) -> None:
    """Check that the BEWT repo exists. Fail loudly if not."""
    if not config.bewt_repo_path.exists():
        raise click.ClickException(
            f"BEWT repo not found at: {config.bewt_repo_path}\n"
            f"  Clone it with: git clone https://github.com/nicorubi/BEWT.git {config.bewt_repo_path}\n"
            f"  Or update bewt_repo_path in config/apps.yaml"
        )


def _validate_api_keys(config: Config, model_names: list[str]) -> None:
    """Check that required API keys are set. Fail loudly if not."""
    missing = []
    for model_name in model_names:
        model_config = config.models[model_name]
        env_var = model_config["api_key_env"]
        if not os.environ.get(env_var):
            missing.append(f"  {model_name} ({model_config['provider']}): set {env_var}")
    if missing:
        raise click.ClickException(
            "Missing API keys:\n" + "\n".join(missing) + "\n\n"
            "Add them to your .env file or export them as environment variables."
        )


def _validate_maven() -> list[str]:
    """Check that Maven is available. Return list of warnings."""
    warnings = []
    if not shutil.which("mvn"):
        warnings.append("Maven (mvn) not found — install with: brew install maven")
    return warnings


def _validate_app_exists(config: Config, app_name: str) -> bool:
    """Check that a specific app's test directory exists."""
    test_dir = config.get_app_test_path(app_name)
    if not test_dir.exists():
        click.echo(
            click.style(f"  Error: Test directory not found for {app_name}", fg="red") + "\n"
            f"  Expected: {test_dir}\n"
            f"  Check that the BEWT repo is cloned at: {config.bewt_repo_path}"
        )
        return False
    return True


class TreatmentType(click.ParamType):
    """Custom Click type that accepts treatments as separate flags or space-separated."""
    name = "treatment"

    def convert(self, value, param, ctx):
        value = value.upper()
        if value not in TREATMENTS:
            self.fail(f"'{value}' is not a valid treatment. Choose from: {', '.join(TREATMENTS)}", param, ctx)
        return value


@click.group()
@click.option("--config-dir", type=click.Path(exists=True), default=None, help="Config directory")
@click.pass_context
def main(ctx, config_dir):
    """BEWT Assertion Generation Pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config(Path(config_dir) if config_dir else None)


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to process (default: all)")
@click.pass_context
def parse(ctx, app):
    """Parse test files and extract assertions."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)
    apps = app if app else list(config.apps.keys())

    total_tests = 0
    total_assertions = 0

    for app_name in apps:
        click.echo(f"\n=== Parsing {app_name} ===")
        if not _validate_app_exists(config, app_name):
            continue

        app_config = config.apps[app_name]
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)

        strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
        constants = resolve_strings_constants(strings_path)

        records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)
        n_assertions = sum(r.assertion_count for r in records)
        click.echo(f"  Found {len(records)} test files with {n_assertions} assertions")
        total_tests += len(records)
        total_assertions += n_assertions

        out = config.output_dir / "parsed" / app_name
        out.mkdir(parents=True, exist_ok=True)
        for r in records:
            data = r.to_dict()
            data["stripped_source"] = r.stripped_source
            (out / f"{r.class_name}.json").write_text(json.dumps(data, indent=2))

    click.echo(f"\nParsing complete: {total_tests} tests, {total_assertions} assertions across {len(apps)} apps.")


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to process")
@click.pass_context
def generate_variants(ctx, app):
    """Generate test variants A, B, C."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)
    apps = app if app else list(config.apps.keys())

    for app_name in apps:
        click.echo(f"\n=== Generating variants for {app_name} ===")
        if not _validate_app_exists(config, app_name):
            continue

        app_config = config.apps[app_name]
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)

        strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
        constants = resolve_strings_constants(strings_path)
        records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)

        gherkin_dir = config.get_gherkin_path(app_name)
        gherkin_map = parse_all_gherkin(gherkin_dir)

        html_map = {}
        for r in records:
            html = capture_html_static(config, app_name, r.class_name)
            if html:
                html_map[r.class_name] = html

        output_dir = config.output_dir / "variants"
        for r in records:
            html = html_map.get(r.class_name)
            paths = write_variants(r, gherkin_map, output_dir, html)
            click.echo(f"  {r.class_name}: {len(paths)} files written")

    click.echo("\nVariant generation complete.")


@main.command(name="reference-run")
@click.option("--app", "-a", required=True, help="App to run the gold suite for")
@click.option("--source", type=click.Choice(["bewt", "crawled"]), default="bewt",
              help="bewt: the benchmark's gold suite; crawled: the DANTE-generated replays")
@click.pass_context
def reference_run(ctx, app, source):
    """Run the tests once on a fresh app, in order: save per-test state
    snapshots and the HTML at each assertion point (needed for C/D and --execute)."""
    config = ctx.obj["config"]
    click.echo(f"\n=== {source} reference run for {app} ===")
    try:
        if source == "crawled":
            from .execution.reference import build_crawled_reference
            reference = build_crawled_reference(config, app, log=click.echo)
        else:
            _validate_bewt_repo(config)
            if not _validate_app_exists(config, app):
                return
            reference = build_reference(config, app, log=click.echo)
    except ReferenceRunError as e:
        raise click.ClickException(str(e))
    tests = len(reference["order"]) - 1
    click.echo(click.style(
        f"\nAll {tests} tests passed (after setup); {len(reference['snapshots'])} snapshots "
        f"and the HTML at every assertion point saved.", fg="green"))


@main.command(name="rescore")
@click.option("--app", "-a", required=True, help="App whose stored answers to re-execute")
@click.pass_context
def rescore(ctx, app):
    """Re-execute and re-classify every stored answer with the current harness
    (no LLM calls; the model's answers are not changed)."""
    from .runner import rescore_experiment

    config = ctx.obj["config"]
    try:
        summary = rescore_experiment(config, app, on_progress=click.echo)
    except ReferenceRunError as e:
        raise click.ClickException(str(e))
    click.echo(f"\nRe-executed {summary['rescored']} answers; {len(summary['changed'])} outcome(s) changed:")
    for c in summary["changed"]:
        click.echo(f"  {c['class']} [{c['treatment']}] run{c['run']}: "
                   f"(compiles, passes, category) {c['old']} -> {c['new']}")


@main.command(name="verify-execution")
@click.option("--app", "-a", required=True, help="App whose execution harness to verify")
@click.option("--source", type=click.Choice(["bewt", "crawled"]), default="bewt",
              help="bewt: gold assertions; crawled: the captured page title (no gold exists)")
@click.pass_context
def verify_execution(ctx, app, source):
    """Positive/negative controls: a known-true assertion must pass and its
    negation must fail when run through the isolated execution path."""
    config = ctx.obj["config"]
    click.echo(f"\n=== Execution controls for {app} ({source}) ===")
    try:
        if source == "crawled":
            from .execution.controls import run_crawled_controls
            rows = run_crawled_controls(config, app, log=click.echo)
        else:
            rows = run_controls(config, app, log=click.echo)
    except ReferenceRunError as e:
        raise click.ClickException(str(e))
    bad = [r for r in rows if not (r["positive"]["ok"] and r["negative"]["ok"])]
    for r in bad:
        for kind in ("positive", "negative"):
            if not r[kind]["ok"]:
                click.echo(f"  {r['class']} {kind}: compiles={r[kind]['compiles']} "
                           f"passed={r[kind]['passed']} {r[kind]['note']} {r[kind]['message']}")
    color = "green" if not bad else "red"
    click.echo(click.style(f"\n{len(rows) - len(bad)}/{len(rows)} tests behave correctly "
                           "(known-true assertion passes, negation fails).", fg=color))
    if bad:
        raise SystemExit(1)


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to run experiments on")
@click.option("--model", "-m", multiple=True, help="Model(s) to use")
@click.option("--treatment", "-t", multiple=True, type=TreatmentType(), help="Treatment(s): A, B, C, D")
@click.option("--execute", is_flag=True, default=False, help="Compile and run assertions against live apps (requires Maven + Docker)")
@click.option("--limit", "-n", type=int, default=None, help="Limit number of tests per app (default: all)")
@click.option("--runs", type=click.IntRange(min=1), default=1,
              help="Repetitions: each test/treatment is generated this many times (independent LLM calls)")
@click.option("--source", type=click.Choice(["bewt", "crawled"]), default="bewt",
              help="Test provenance: bewt (benchmark tests with gold assertions) or "
                   "crawled (DANTE/Crawljax-generated tests; treatments A/B/C only)")
@click.pass_context
def run(ctx, app, model, treatment, execute, limit, runs, source):
    """Run the full experiment pipeline."""
    config = ctx.obj["config"]
    models = list(model) if model else [config.default_model]

    if source == "crawled":
        if runs > 1:
            raise click.ClickException("--runs is only supported for --source bewt.")
        if not app:
            raise click.ClickException(
                "--source crawled requires an explicit --app (one crawled app per run)."
            )
        if len(app) > 1:
            raise click.ClickException("--source crawled supports a single --app per run.")
        treatments = tuple(treatment) if treatment else CRAWLED_TREATMENTS
        invalid = [t for t in treatments if t not in CRAWLED_TREATMENTS]
        if invalid:
            raise click.ClickException(
                f"Treatment(s) {', '.join(invalid)} not valid for crawled tests. "
                f"Valid: {', '.join(CRAWLED_TREATMENTS)}."
            )
        _validate_api_keys(config, models)
        _run_crawled(config, app[0], models, treatments, execute, limit)
        return

    _validate_bewt_repo(config)
    apps = list(app) if app else list(config.apps.keys())
    treatments = tuple(treatment) if treatment else TREATMENTS

    _validate_api_keys(config, models)

    if execute:
        warnings = _validate_maven()
        for w in warnings:
            click.echo(click.style(f"Warning: {w}", fg="yellow"))
        if warnings and not click.confirm("Continue anyway?"):
            return

    total = count_experiments(config, apps, models, treatments, limit=limit, runs=runs)

    def on_progress(completed, total_exp, t, class_name, message):
        progress = f"[{completed}/{total_exp}]"
        if message.startswith("model:"):
            click.echo(f"\n{'='*60}\nModel: {message[6:]}\n{'='*60}")
        elif message.startswith("app:"):
            click.echo(f"\n--- {message[4:]} ---")
        elif message.startswith("skip:"):
            click.echo(click.style(f"  {message[5:]}: test dir not found, skipping", fg="red"))
        elif message == "skipped":
            click.echo(f"  {progress} [{t}] {class_name}: already done, skipping")
        elif message == "invalid":
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style("invalid assertion", fg="red"))
        elif message.startswith("error:"):
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style(f"LLM error: {message[6:]}", fg="red"))
        elif message.startswith("sim="):
            sim = float(message[4:])
            color = "green" if sim >= 0.8 else "yellow" if sim >= 0.5 else "red"
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style(message, fg=color))
        elif message.startswith("PASS:") or message.startswith("FAIL:"):
            status = message[:4]
            color = "green" if status == "PASS" else "red"
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style(message, fg=color))

    try:
        results = run_experiment(
            config, apps=apps, models=models, treatments=treatments,
            execute=execute, on_progress=on_progress, limit=limit, runs=runs,
        )
    except MissingContextError as e:
        raise click.ClickException(str(e))

    store = ResultStore(config.output_dir / "results.db")
    summary = store.get_summary()
    click.echo(f"\nTotal experiments: {summary['total']}")
    for t, s in summary.get("by_treatment", {}).items():
        click.echo(f"  Treatment {t}: {s['count']} tests, {s['passes']} pass, {s['exact']} exact")
    store.close()


def _run_crawled(config, app, models, treatments, execute, limit):
    """Drive run_crawled_experiment with CLI progress output."""
    suite_path = config.get_dante_suite_path(app)
    if not suite_path.exists():
        raise click.ClickException(
            f"DANTE-generated suite not found for {app}:\n  {suite_path}\n"
            f"Run 'bewt-pipeline crawl --app {app}' first (see docs/dante-setup.md)."
        )

    def on_progress(completed, total_exp, t, class_name, message):
        progress = f"[{completed}/{total_exp}]"
        if message.startswith("model:"):
            click.echo(f"\n{'='*60}\nModel: {message[6:]} (crawled: {app})\n{'='*60}")
        elif message == "skipped":
            click.echo(f"  {progress} [{t}] {class_name}: already done, skipping")
        elif message == "invalid":
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style("invalid assertion", fg="red"))
        elif message == "generated":
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style("assertion generated", fg="green"))
        elif message.startswith("error:"):
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style(f"LLM error: {message[6:]}", fg="red"))
        elif message.startswith("PASS:") or message.startswith("FAIL:"):
            color = "green" if message.startswith("PASS") else "red"
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style(message, fg=color))

    try:
        results = run_crawled_experiment(
            config, app, models=models, treatments=treatments,
            execute=execute, on_progress=on_progress, limit=limit,
        )
    except MissingContextError as e:
        raise click.ClickException(str(e))
    click.echo(f"\nCrawled experiments completed this run: {len(results)}")


@main.command(name="ingest-crawl")
@click.option("--app", "-a", required=True, help="App whose DANTE crawl to ingest")
@click.pass_context
def ingest_crawl(ctx, app):
    """Ingest a DANTE-generated test suite: split tests, add placeholders, render action traces."""
    from .ingest.dante import ingest_dante_suite

    config = ctx.obj["config"]
    suite_path = config.get_dante_suite_path(app)
    if not suite_path.exists():
        raise click.ClickException(
            f"DANTE-generated suite not found for {app}:\n  {suite_path}\n"
            f"Run 'bewt-pipeline crawl --app {app}' first."
        )

    output_dir = config.output_dir / "crawled" / app
    tests = ingest_dante_suite(app, suite_path, output_dir)

    click.echo(f"Ingested {len(tests)} crawled tests from {suite_path.name}:")
    for t in tests:
        click.echo(f"  {t.class_name}  ({t.method_name})")
    click.echo(f"\nPlaceholdered classes written to {output_dir / 'tests'}")
    click.echo(f"Action traces written to {output_dir / 'state_map.json'}")
    click.echo(f"Next: bewt-pipeline reference-run --app {app} --source crawled")


@main.command()
@click.option("--app", "-a", required=True, help="App to crawl with DANTE")
@click.option("--headless/--no-headless", default=True, help="Run Chrome headless")
@click.option("--minutes", "-t", type=int, default=10, help="Crawl time budget (minutes)")
@click.option("--skip-generate", is_flag=True, default=False,
              help="Only crawl; skip generating + running the Java test project")
@click.pass_context
def crawl(ctx, app, headless, minutes, skip_generate):
    """Crawl an app with DANTE and generate its Selenium test suite."""
    import subprocess

    config = ctx.obj["config"]
    dante_dir = config.dante_repo_path / "dante"
    if not (dante_dir / "run-crawling.sh").exists():
        raise click.ClickException(
            f"DANTE not found at {config.dante_repo_path}.\n"
            f"See docs/dante-setup.md for the toolchain bring-up."
        )

    from .execution.dante_runner import _toolchain_env, DEFAULT_JAVA_HOME
    env = _toolchain_env()
    if not Path(env["JAVA_HOME"], "bin", "java").exists():
        raise click.ClickException(
            f"Pinned JDK 8 not found at {env['JAVA_HOME']} "
            f"(default: {DEFAULT_JAVA_HOME}).\nSee docs/dante-setup.md, or set DANTE_JAVA_HOME."
        )

    headless_flag = "true" if headless else "false"

    click.echo(f"=== Crawling {app} with DANTE ({minutes} min, headless={headless_flag}) ===")
    result = subprocess.run(
        ["./run-crawling.sh", app, headless_flag, str(minutes)],
        cwd=str(dante_dir), env=env,
    )
    if result.returncode != 0:
        raise click.ClickException(
            f"Crawl failed (exit {result.returncode}). "
            f"See ~/Desktop/errors_CrawlApp_{app}.txt"
        )

    if skip_generate:
        click.echo("Crawl complete (generation skipped).")
        return

    click.echo(f"\n=== Generating Java test project for {app} ===")
    result = subprocess.run(
        ["./generate-java-project-from-crawling.sh", app, headless_flag],
        cwd=str(dante_dir), env=env,
    )
    if result.returncode != 0:
        raise click.ClickException(f"Test project generation failed (exit {result.returncode}).")

    suite = config.get_dante_suite_path(app)
    if suite.exists():
        click.echo(click.style(f"\nGenerated suite: {suite}", fg="green"))
        click.echo(f"Next: bewt-pipeline ingest-crawl --app {app}")
    else:
        click.echo(click.style("\nGeneration finished but suite file not found.", fg="red"))


@main.command(name="run-oracle-suite")
@click.option("--app", "-a", required=True, help="App whose Crawljax oracle suite to run")
@click.pass_context
def run_oracle_suite_cmd(ctx, app):
    """Run the Crawljax oracle suite (baseline for the LLM-vs-oracle comparison)."""
    from .execution.dante_runner import reset_crawled_app, run_oracle_suite

    config = ctx.obj["config"]
    click.echo(f"Resetting {app} to a fresh installed state...")
    reset_crawled_app(config, app)

    click.echo("Running Crawljax oracle suite (invariants + state-equivalence)...")
    results = run_oracle_suite(config, app)
    if not results:
        raise click.ClickException(
            f"No oracle suite found for {app} (expected GeneratedTests.java in the crawl output)."
        )

    outcome = {r.test_method: ("PASS" if r.passed else "SKIP" if r.skipped else "FAIL")
               for r in results}
    out_path = config.output_dir / "crawled" / app / "oracle_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(outcome, indent=2))

    passed = sum(1 for s in outcome.values() if s == "PASS")
    for method, status in outcome.items():
        color = "green" if status == "PASS" else "red"
        click.echo(f"  {method}: " + click.style(status, fg=color))
    click.echo(f"\nOracle suite: {passed}/{len(outcome)} methods pass")
    click.echo(f"Results saved to {out_path}")


def _load_crawled_state_maps(config, apps: set[str]) -> dict[str, dict]:
    """Merge state_map.json files for the given crawled apps."""
    merged: dict[str, dict] = {}
    for app in apps:
        path = config.output_dir / "crawled" / app / "state_map.json"
        if path.exists():
            merged.update(json.loads(path.read_text()))
    return merged


def _load_oracle_results(config, apps: set[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for app in apps:
        path = config.output_dir / "crawled" / app / "oracle_results.json"
        if path.exists():
            merged.update(json.loads(path.read_text()))
    return merged


@main.command()
@click.pass_context
def report(ctx):
    """Generate reports from stored results."""
    from .evaluation.reporter import write_crawled_comparison

    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException(
            "No results database found. Run the experiment first:\n"
            "  bewt-pipeline run --app expresscart --model gpt-4o --treatment A --treatment B"
        )

    store = ResultStore(db_path)
    all_results = store.load_experiment_results()
    if not all_results:
        raise click.ClickException("No results found in database. Run the experiment first.")

    bewt_results = [r for r in all_results if r.source == "bewt"]
    crawled_results = [r for r in all_results if r.source == "crawled"]
    click.echo(f"Found {len(bewt_results)} benchmark results, {len(crawled_results)} crawled results")

    if bewt_results:
        generate_full_report(bewt_results, config.output_dir / "reports")
        click.echo(f"Benchmark reports written to {config.output_dir / 'reports'}")

    if crawled_results:
        crawled_apps = {r.test_record.app for r in crawled_results}
        manual = {
            f"{row['app']}|{row['class_name']}|{row['treatment']}|{row['model']}":
                row["manual_error_category"] or ""
            for row in store.get_all_results()
            if row.get("source") == "crawled"
        }
        write_crawled_comparison(
            crawled_results,
            config.output_dir / "reports",
            oracle_results=_load_oracle_results(config, crawled_apps) or None,
            manual_categories=manual,
        )
        click.echo(f"Crawled comparison written to {config.output_dir / 'reports' / 'crawled_comparison.csv'}")

    summary = store.get_summary()
    click.echo(json.dumps(summary, indent=2))
    store.close()


def _format_optional(value, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{value}{suffix}"


@main.command(name="show-prompt")
@click.option("--app", "-a", default=None, help="Filter by app")
@click.option("--class", "class_name", default=None, help="Filter by test class")
@click.option("--treatment", "-t", type=TreatmentType(), default=None, help="Filter by treatment")
@click.option("--model", "-m", default=None, help="Filter by model")
@click.option("--call-type", default="generation", help="LLM call type (default: generation)")
@click.option("--latest", is_flag=True, default=False, help="Show the latest matching call")
@click.pass_context
def show_prompt(ctx, app, class_name, treatment, model, call_type, latest):
    """Show the exact prompt and response for one logged LLM call."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    store = ResultStore(db_path)
    calls = store.get_llm_calls(
        call_type=call_type,
        app=app,
        class_name=class_name,
        treatment=treatment,
        model=model,
    )
    store.close()

    if not calls:
        raise click.ClickException("No matching LLM calls found.")
    if len(calls) > 1 and not latest:
        raise click.ClickException(
            f"Found {len(calls)} matching calls. Add more filters or pass --latest."
        )

    call = calls[-1] if latest else calls[0]
    click.echo(f"Call ID: {call['id']}")
    click.echo(f"Type: {call['call_type']}")
    click.echo(f"App: {call['app']}")
    click.echo(f"Class: {call['class_name']}")
    click.echo(f"Method: {call['method_name']}")
    click.echo(f"Treatment: {call['treatment']}")
    click.echo(f"Model: {call['model']}")
    click.echo(f"Provider: {call['provider'] or 'n/a'}")
    click.echo(f"Prompt hash: {call['prompt_hash']}")
    click.echo(f"Input tokens: {_format_optional(call['input_tokens'])}")
    click.echo(f"Output tokens: {_format_optional(call['output_tokens'])}")
    click.echo(f"Total tokens: {_format_optional(call['total_tokens'])}")
    click.echo(f"Cached input tokens: {_format_optional(call['cached_input_tokens'])}")
    click.echo(f"Cache creation input tokens: {_format_optional(call['cache_creation_input_tokens'])}")
    click.echo(f"Cache read input tokens: {_format_optional(call['cache_read_input_tokens'])}")
    click.echo(f"Reasoning tokens: {_format_optional(call['reasoning_tokens'])}")
    click.echo(f"Cost USD: {_format_optional(call['cost_usd'])}")
    click.echo(f"Latency: {_format_optional(call['latency_ms'], ' ms')}")
    click.echo("\n--- SYSTEM PROMPT ---")
    click.echo(call["system_prompt"] or "")
    click.echo("\n--- USER PROMPT ---")
    click.echo(call["user_prompt"] or "")
    click.echo("\n--- RAW RESPONSE ---")
    click.echo(call["raw_response"] or "")


@main.command(name="export-prompts")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output .csv or .xlsx path")
@click.option("--app", "-a", default=None, help="Filter by app")
@click.option("--class", "class_name", default=None, help="Filter by test class")
@click.option("--treatment", "-t", type=TreatmentType(), default=None, help="Filter by treatment")
@click.option("--model", "-m", default=None, help="Filter by model")
@click.option("--call-type", default=None, help="Filter by LLM call type")
@click.pass_context
def export_prompts(ctx, output, app, class_name, treatment, model, call_type):
    """Export logged LLM prompts and responses to CSV or Excel."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    store = ResultStore(db_path)
    calls = store.get_llm_calls(
        call_type=call_type,
        app=app,
        class_name=class_name,
        treatment=treatment,
        model=model,
    )
    store.close()

    if not calls:
        raise click.ClickException("No matching LLM calls found.")

    output_path = Path(output) if output else config.output_dir / "reports" / "prompts.xlsx"
    try:
        export_llm_calls(calls, output_path)
    except ValueError as e:
        raise click.ClickException(str(e))

    click.echo(f"Exported {len(calls)} LLM calls to {output_path}")


@main.command(name="llm-usage")
@click.option(
    "--by",
    "group_by",
    type=click.Choice(["none", "treatment", "model", "app", "call_type", "source"]),
    default="none",
    help="Group usage summary",
)
@click.option("--output", "-o", type=click.Path(), default=None, help="Optional CSV output path")
@click.pass_context
def llm_usage(ctx, group_by, output):
    """Show real API usage (tokens, cost, latency); cache hits are counted separately."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    summary_group = None if group_by == "none" else group_by
    store = ResultStore(db_path)
    rows = store.get_llm_usage_summary(summary_group)
    store.close()

    if not rows:
        raise click.ClickException("No LLM calls found.")

    headers = [
        "group",
        "calls",
        "cache_hits",
        "input",
        "output",
        "total",
        "cached",
        "cache_write",
        "cache_read",
        "reasoning",
        "cost_usd",
        "avg_latency_ms",
    ]
    table_rows = []
    for row in rows:
        table_rows.append({
            "group": row["group_key"],
            "calls": row["calls"],
            "cache_hits": row["cache_hits"],
            "input": row["input_tokens"],
            "output": row["output_tokens"],
            "total": row["total_tokens"],
            "cached": row["cached_input_tokens"],
            "cache_write": row["cache_creation_input_tokens"],
            "cache_read": row["cache_read_input_tokens"],
            "reasoning": row["reasoning_tokens"],
            "cost_usd": row["cost_usd"] or 0,
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
        })

    widths = {
        header: max(len(header), *(len(str(row[header])) for row in table_rows))
        for header in headers
    }
    click.echo("  ".join(header.ljust(widths[header]) for header in headers))
    click.echo("  ".join("-" * widths[header] for header in headers))
    for row in table_rows:
        click.echo("  ".join(str(row[header]).ljust(widths[header]) for header in headers))

    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(table_rows)
        click.echo(f"Usage summary written to {output_path}")


@main.command(name="export-treatment-comparison")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output .csv or .xlsx path")
@click.pass_context
def export_treatment_comparison_cmd(ctx, output):
    """Export A/B/C/D usage and quality comparison per test/model."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    store = ResultStore(db_path)
    records = store.get_treatment_comparison_records()
    store.close()

    if not records:
        raise click.ClickException("No experiment results found.")

    output_path = Path(output) if output else config.output_dir / "reports" / "treatment_comparison.xlsx"
    try:
        export_treatment_comparison(records, output_path)
    except ValueError as e:
        raise click.ClickException(str(e))

    click.echo(f"Exported treatment comparison for {len(records)} treatment rows to {output_path}")


@main.command()
@click.pass_context
def info(ctx):
    """Show information about configured apps and available tests."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)

    for app_name, app_config in config.apps.items():
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)
        gherkin_dir = config.get_gherkin_path(app_name)

        test_count = len(list(test_dir.glob("*.java"))) if test_dir.exists() else 0
        gherkin_count = len(list(gherkin_dir.glob("*.feature"))) if gherkin_dir.exists() else 0

        click.echo(f"{app_name} ({version}): {test_count} test files, {gherkin_count} feature files")


@main.command(name="export-excel")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output .xlsx path")
@click.option("--pre-classify", is_flag=True, default=False, help="Use LLM to pre-fill classification suggestions")
@click.option("--model", "-m", default=None, help="Model for pre-classification (default: config default)")
@click.option("--source", type=click.Choice(["bewt", "crawled"]), default="bewt",
              help="Which results to export: bewt (gold-standard rubric) or crawled (meaningfulness rubric)")
@click.pass_context
def export_excel(ctx, output, pre_classify, model, source):
    """Export results to Excel for manual annotation."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    store = ResultStore(db_path)
    results = [r for r in store.load_experiment_results() if r.source == source]
    if not results:
        raise click.ClickException(f"No {source} results found in database.")

    state_map: dict[str, dict] = {}
    if source == "crawled":
        crawled_apps = {r.test_record.app for r in results}
        state_map = _load_crawled_state_maps(config, crawled_apps)

    groups = review_groups(results) if source == "bewt" else []
    if source == "bewt":
        click.echo(f"{len(groups)} unique passing-but-different answers need review "
                   f"({sum(len(g['occurrences']) for g in groups)} results).")
        if not groups:
            click.echo("Nothing to review: every result is decided by execution.")
            store.close()
            return

    pre_classifications = None
    if pre_classify:
        from .llm.client import create_client
        from .evaluation.llm_classifier import (
            pre_classify_results,
            pre_classify_crawled_results,
        )

        model_name = model or config.default_model
        _validate_api_keys(config, [model_name])
        llm = create_client(config, model_name)

        def on_progress(completed, total, message):
            click.echo(f"  [{completed}/{total}] {message}")

        if source == "crawled":
            click.echo(f"Pre-classifying {len(results)} results with {model_name}...")
            pre_classifications = pre_classify_crawled_results(
                results, llm, state_map, on_progress=on_progress,
                store=store, classifier_model=model_name,
            )
            for key, classification in pre_classifications.items():
                if not classification:
                    continue
                app_name, class_name, treatment_name, result_model = key.split("|", 3)
                store.update_llm_preclassification(
                    app=app_name, class_name=class_name, treatment=treatment_name,
                    model=result_model, llm_preclassification=classification, source=source,
                )
        else:
            click.echo(f"Pre-classifying {len(groups)} unique answers with {model_name}...")
            pre_classifications = pre_classify_results(
                groups, llm, on_progress=on_progress, store=store, classifier_model=model_name
            )
            for key, classification in pre_classifications.items():
                if classification:
                    app_name, class_name, answer = key.split("|", 2)
                    store.set_preclassification_for_answer(app_name, class_name, answer, classification)
        click.echo("Pre-classification complete (suggestions only; final labels are yours).")

    default_name = "manual_evaluation_crawled.xlsx" if source == "crawled" else "manual_evaluation.xlsx"
    output_path = Path(output) if output else config.output_dir / "reports" / default_name

    if source == "crawled":
        from .evaluation.excel_export import export_crawled_results_to_excel

        crawled_apps = {r.test_record.app for r in results}
        oracle_results = _load_oracle_results(config, crawled_apps) or None
        export_crawled_results_to_excel(
            results, output_path, state_map,
            pre_classifications=pre_classifications,
            oracle_results=oracle_results,
        )
    else:
        export_review_to_excel(groups, output_path, pre_classifications=pre_classifications)

    click.echo(f"Exported to {output_path}")
    click.echo(f"Fill in the 'Manual Classification' column, then run: bewt-pipeline import-excel --source {source}")
    store.close()


@main.command(name="import-excel")
@click.option("--input", "-i", "input_path", type=click.Path(exists=True), default=None, help="Annotated .xlsx path")
@click.option("--source", type=click.Choice(["bewt", "crawled"]), default="bewt",
              help="Which results the annotations belong to")
@click.pass_context
def import_excel(ctx, input_path, source):
    """Import manual classifications from an annotated Excel file back into the database."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found.")

    default_name = "manual_evaluation_crawled.xlsx" if source == "crawled" else "manual_evaluation.xlsx"
    excel_path = Path(input_path) if input_path else config.output_dir / "reports" / default_name
    if not excel_path.exists():
        raise click.ClickException(f"Excel file not found: {excel_path}")

    if source == "bewt":
        try:
            labels = import_review_labels(excel_path)
        except ValueError as e:
            raise click.ClickException(str(e))
        if not labels:
            raise click.ClickException("No manual classifications found in the Excel file.")
        store = ResultStore(db_path)
        updated = sum(
            store.apply_review_label(l["app"], l["class_name"], l["answer_key"], l["label"], l["notes"])
            for l in labels
        )
        remaining = len(review_groups(store.load_experiment_results()))
        store.close()
        click.echo(f"Applied {len(labels)} labels to {updated} results; "
                   f"{remaining} unique answers still need review.")
        click.echo("Run 'bewt-pipeline report' to regenerate reports with the final categories.")
        return

    annotations = import_classifications_from_excel(excel_path)
    if not annotations:
        raise click.ClickException("No manual classifications found in the Excel file.")

    store = ResultStore(db_path)
    updated = 0
    for a in annotations:
        if store.update_classification(
            app=a["app"], class_name=a["class_name"],
            treatment=a["treatment"], model=a["model"],
            error_category=a["manual_classification"],
            notes=a["manual_notes"],
            source=source,
        ):
            updated += 1

    click.echo(f"Updated {updated}/{len(annotations)} classifications in database.")
    click.echo("Run 'bewt-pipeline report' to regenerate reports with manual classifications.")
    store.close()


if __name__ == "__main__":
    main()
