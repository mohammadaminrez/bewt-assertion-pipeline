from __future__ import annotations

"""CLI entry point for the BEWT assertion generation pipeline."""

import json
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import click

from .config import Config
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin
from .models import ExperimentResult, ErrorCategory
from .variants.generator import write_variants
from .variants.html_capture import capture_html_for_app, capture_html_static
from .execution.docker_manager import DockerManager
from .execution.app_setup import run_installer
from .evaluation.reporter import generate_full_report
from .evaluation.excel_export import export_results_to_excel, import_classifications_from_excel
from .evaluation.prompt_export import export_llm_calls
from .data.store import ResultStore
from .runner import run_experiment, count_experiments


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
        if value not in ("A", "B", "C", "D"):
            self.fail(f"'{value}' is not a valid treatment. Choose from: A, B, C, D", param, ctx)
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


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to capture HTML for")
@click.pass_context
def capture_html(ctx, app):
    """Capture HTML pages from running apps for variant C."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)
    apps = app if app else list(config.apps.keys())

    docker = DockerManager(config)

    # Check requirements upfront
    warnings = _validate_maven()
    for w in warnings:
        click.echo(click.style(f"Warning: {w}", fg="yellow"))
    if warnings and not click.confirm("Continue anyway?"):
        return

    for app_name in apps:
        click.echo(f"\n=== Capturing HTML for {app_name} ===")
        if not docker.deploy_app(app_name):
            click.echo(f"  Failed to deploy {app_name}, skipping")
            continue
        html_map = capture_html_for_app(config, app_name, on_progress=click.echo)
        click.echo(f"  Captured {len(html_map)} pages")


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to run experiments on")
@click.option("--model", "-m", multiple=True, help="Model(s) to use")
@click.option("--treatment", "-t", multiple=True, type=TreatmentType(), help="Treatment(s): A, B, C")
@click.option("--execute", is_flag=True, default=False, help="Compile and run assertions against live apps (requires Maven + Docker)")
@click.pass_context
def run(ctx, app, model, treatment, execute):
    """Run the full experiment pipeline."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)
    apps = list(app) if app else list(config.apps.keys())
    models = list(model) if model else [config.default_model]
    treatments = tuple(treatment) if treatment else ("A", "B", "C", "D")

    _validate_api_keys(config, models)

    if execute:
        warnings = _validate_maven()
        for w in warnings:
            click.echo(click.style(f"Warning: {w}", fg="yellow"))
        if warnings and not click.confirm("Continue anyway?"):
            return

    total = count_experiments(config, apps, models, treatments)

    def on_progress(completed, total_exp, t, class_name, message):
        progress = f"[{completed}/{total_exp}]"
        if message.startswith("model:"):
            click.echo(f"\n{'='*60}\nModel: {message[6:]}\n{'='*60}")
        elif message.startswith("app:"):
            click.echo(f"\n--- {message[4:]} ---")
        elif message.startswith("skip:"):
            click.echo(click.style(f"  {message[5:]}: test dir not found, skipping", fg="red"))
        elif message == "no_html":
            click.echo(f"  {progress} [{t}] {class_name}: " + click.style("no HTML captured, falling back to B", fg="yellow"))
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

    results = run_experiment(
        config, apps=apps, models=models, treatments=treatments,
        execute=execute, on_progress=on_progress,
    )

    store = ResultStore(config.output_dir / "results.db")
    summary = store.get_summary()
    click.echo(f"\nTotal experiments: {summary['total']}")
    for t, s in summary.get("by_treatment", {}).items():
        click.echo(f"  Treatment {t}: {s['count']} tests, {s['passes']} pass, {s['exact']} exact")
    store.close()


@main.command()
@click.pass_context
def report(ctx):
    """Generate reports from stored results."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException(
            "No results database found. Run the experiment first:\n"
            "  bewt-pipeline run --app expresscart --model gpt-4o --treatment A --treatment B"
        )

    store = ResultStore(db_path)
    results = store.load_experiment_results()
    if not results:
        raise click.ClickException("No results found in database. Run the experiment first.")

    click.echo(f"Found {len(results)} results")
    generate_full_report(results, config.output_dir / "reports")
    click.echo(f"Reports written to {config.output_dir / 'reports'}")

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


@main.command()
@click.option("--app", "-a", required=True, help="App to set up")
@click.pass_context
def setup_app(ctx, app):
    """Deploy and configure a web app (Docker + Installer)."""
    config = ctx.obj["config"]
    _validate_bewt_repo(config)

    if app not in config.apps:
        raise click.ClickException(f"Unknown app: {app}. Available: {', '.join(config.apps.keys())}")

    warnings = _validate_maven()
    for w in warnings:
        click.echo(click.style(f"Warning: {w}", fg="yellow"))
    if warnings and not click.confirm("Continue anyway?"):
        return

    docker = DockerManager(config)

    click.echo(f"\n=== Setting up {app} ===")

    # Step 1: Deploy
    click.echo("Step 1: Deploy app...")
    if not docker.deploy_app(app):
        raise click.ClickException(f"Failed to deploy {app}. Make sure Docker is running or start the app manually.")

    # Step 2: Run installer
    click.echo("Step 2: Run installer...")
    success = run_installer(config, app, on_progress=click.echo)

    if success:
        click.echo(click.style(f"\n{app} is ready.", fg="green"))
        click.echo(f"You can now run: bewt-pipeline capture-html --app {app}")
    else:
        click.echo(click.style(f"\n{app} setup failed. Check the errors above.", fg="red"))


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
@click.pass_context
def export_excel(ctx, output, pre_classify, model):
    """Export results to Excel for manual annotation."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found. Run the experiment first.")

    store = ResultStore(db_path)
    results = store.load_experiment_results()
    if not results:
        raise click.ClickException("No results found in database.")

    pre_classifications = None
    if pre_classify:
        from .llm.client import create_client
        from .evaluation.llm_classifier import pre_classify_results

        model_name = model or config.default_model
        _validate_api_keys(config, [model_name])
        llm = create_client(config, model_name)

        click.echo(f"Pre-classifying {len(results)} results with {model_name}...")
        def on_progress(completed, total, message):
            click.echo(f"  [{completed}/{total}] {message}")

        pre_classifications = pre_classify_results(
            results, llm, on_progress=on_progress, store=store, classifier_model=model_name
        )
        click.echo(f"Pre-classification complete.")

    output_path = Path(output) if output else config.output_dir / "reports" / "manual_evaluation.xlsx"
    export_results_to_excel(results, output_path, pre_classifications=pre_classifications)
    click.echo(f"Exported {len(results)} results to {output_path}")
    click.echo("Fill in the 'Manual Classification' column, then run: bewt-pipeline import-excel")
    store.close()


@main.command(name="import-excel")
@click.option("--input", "-i", "input_path", type=click.Path(exists=True), default=None, help="Annotated .xlsx path")
@click.pass_context
def import_excel(ctx, input_path):
    """Import manual classifications from an annotated Excel file back into the database."""
    config = ctx.obj["config"]
    db_path = config.output_dir / "results.db"
    if not db_path.exists():
        raise click.ClickException("No results database found.")

    excel_path = Path(input_path) if input_path else config.output_dir / "reports" / "manual_evaluation.xlsx"
    if not excel_path.exists():
        raise click.ClickException(f"Excel file not found: {excel_path}")

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
        ):
            updated += 1

    click.echo(f"Updated {updated}/{len(annotations)} classifications in database.")
    click.echo("Run 'bewt-pipeline report' to regenerate reports with manual classifications.")
    store.close()


if __name__ == "__main__":
    main()
