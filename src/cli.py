from __future__ import annotations

"""CLI entry point for the BEWT assertion generation pipeline."""

import json
import os
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import click

from .config import Config
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin, match_gherkin_to_test
from .parsing.assertion_model import ExperimentResult, ErrorCategory
from .variants.generator import generate_variant_a, generate_variant_b, generate_variant_c, write_variants
from .variants.html_capture import capture_html_for_app, capture_html_static
from .llm.client import create_client
from .llm.prompt_builder import (
    build_prompt_a, build_prompt_b, build_prompt_c, build_prompt_with_page_objects,
)
from .llm.response_parser import extract_assertion_from_response, validate_assertion
from .execution.docker_manager import DockerManager
from .execution.java_injector import prepare_project_copy
from .execution.test_runner import compile_project, run_suite_and_get_result
from .evaluation.comparator import classify_error, compute_semantic_similarity, check_exact_match
from .evaluation.reporter import generate_full_report
from .data.store import ResultStore


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


def _validate_execution_tools() -> list[str]:
    """Check that Maven and Docker are available. Return list of warnings."""
    warnings = []
    if not shutil.which("mvn"):
        warnings.append("Maven (mvn) not found — test compilation and execution will fail")
    if not shutil.which("docker"):
        warnings.append("Docker not found — app deployment will fail")
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
        if value not in ("A", "B", "C"):
            self.fail(f"'{value}' is not a valid treatment. Choose from: A, B, C", param, ctx)
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

        # Load Strings constants if available
        strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
        constants = resolve_strings_constants(strings_path)

        records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)
        n_assertions = sum(r.assertion_count for r in records)
        click.echo(f"  Found {len(records)} test files with {n_assertions} assertions")
        total_tests += len(records)
        total_assertions += n_assertions

        # Save parsed data
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

        # Parse tests
        strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
        constants = resolve_strings_constants(strings_path)
        records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)

        # Parse gherkin
        gherkin_dir = config.get_gherkin_path(app_name)
        gherkin_map = parse_all_gherkin(gherkin_dir)

        # Load captured HTML (if available)
        html_map = {}
        for r in records:
            html = capture_html_static(config, app_name, r.class_name)
            if html:
                html_map[r.class_name] = html

        # Generate variants
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

    for app_name in apps:
        click.echo(f"\n=== Capturing HTML for {app_name} ===")

        # Ensure app is running
        if not docker.deploy_app(app_name):
            click.echo(f"  Failed to deploy {app_name}, skipping")
            continue

        html_map = capture_html_for_app(config, app_name)
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
    apps = app if app else list(config.apps.keys())
    models = model if model else [config.default_model]
    treatments = treatment if treatment else ("A", "B", "C")

    # Validate API keys before starting
    _validate_api_keys(config, list(models))

    # Warn about execution requirements
    if execute:
        warnings = _validate_execution_tools()
        for w in warnings:
            click.echo(click.style(f"Warning: {w}", fg="yellow"))
        if warnings:
            if not click.confirm("Continue anyway?"):
                return

    # Count total experiments for progress
    total_experiments = 0
    for model_name in models:
        for app_name in apps:
            test_dir = config.get_app_test_path(app_name)
            if test_dir.exists():
                n_tests = len([f for f in test_dir.glob("*.java") if f.stem not in ("BaseTest", "TestSuite", "Installer")])
                total_experiments += n_tests * len(treatments)

    store = ResultStore(config.output_dir / "results.db")
    all_results = []
    completed = 0

    for model_name in models:
        click.echo(f"\n{'='*60}")
        click.echo(f"Model: {model_name}")
        click.echo(f"{'='*60}")

        llm = create_client(config, model_name)

        for app_name in apps:
            click.echo(f"\n--- {app_name} ---")
            if not _validate_app_exists(config, app_name):
                continue

            app_config = config.apps[app_name]
            version = app_config["versions"][0]
            test_dir = config.get_app_test_path(app_name)

            # Parse tests
            strings_path = (
                config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
            )
            constants = resolve_strings_constants(strings_path)
            records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)

            # Parse gherkin
            gherkin_dir = config.get_gherkin_path(app_name)
            gherkin_map = parse_all_gherkin(gherkin_dir)

            # Load page object sources for prompt enhancement
            po_dir = config.get_app_po_path(app_name)
            po_sources = {}
            if po_dir.exists():
                for po_file in po_dir.glob("*.java"):
                    po_sources[po_file.stem] = po_file.read_text()

            for record in records:
                record.page_object_sources = po_sources

                for t in treatments:
                    completed += 1
                    progress = f"[{completed}/{total_experiments}]"

                    # Check if already done (resumability)
                    if store.has_result(app_name, version, record.class_name, t, model_name):
                        click.echo(f"  {progress} [{t}] {record.class_name}: already done, skipping")
                        continue

                    click.echo(f"  {progress} [{t}] {record.class_name}: ", nl=False)

                    # Build variant source and prompt
                    if t == "A":
                        variant_source = generate_variant_a(record)
                        system, user = build_prompt_a(record)
                    elif t == "B":
                        variant_source = generate_variant_b(record, gherkin_map)
                        system, user = build_prompt_b(record, variant_source)
                    elif t == "C":
                        variant_source = generate_variant_b(record, gherkin_map)
                        html = capture_html_static(config, app_name, record.class_name)
                        if html:
                            system, user = build_prompt_c(record, variant_source, html)
                        else:
                            # Fallback to B if no HTML available
                            system, user = build_prompt_b(record, variant_source)
                            click.echo("(no HTML, falling back to B) ", nl=False)
                    else:
                        click.echo(f"Unknown treatment: {t}")
                        continue

                    # Add page object context
                    system, user = build_prompt_with_page_objects(
                        (system, user), record.page_object_sources
                    )

                    # Call LLM
                    try:
                        raw_response = llm.generate(system, user)
                    except Exception as e:
                        click.echo(click.style(f"LLM error: {e}", fg="red"))
                        result = ExperimentResult(
                            test_record=record, treatment=t, model=model_name,
                            prompt=user, raw_response=str(e),
                            generated_assertion="",
                            error_category=ErrorCategory.NOT_EXECUTABLE,
                            notes=f"LLM error: {e}",
                        )
                        store.save_result(result)
                        all_results.append(result)
                        continue

                    # Extract assertion
                    generated = extract_assertion_from_response(raw_response)
                    valid = validate_assertion(generated)

                    # Initialize result
                    result = ExperimentResult(
                        test_record=record,
                        treatment=t,
                        model=model_name,
                        prompt=user,
                        raw_response=raw_response,
                        generated_assertion=generated,
                    )

                    if not valid:
                        result.error_category = ErrorCategory.NOT_EXECUTABLE
                        result.notes = "Generated assertion failed validation"
                        click.echo(click.style("invalid assertion", fg="red"))
                    elif not execute:
                        # Just compute similarity without running
                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.notes = "execution skipped"
                        sim = result.semantic_similarity
                        color = "green" if sim >= 0.8 else "yellow" if sim >= 0.5 else "red"
                        click.echo(click.style(f"sim={sim:.2f}", fg=color))
                    else:
                        # Compile and run
                        work_dir = prepare_project_copy(
                            config, app_name, record, generated,
                            variant_source, t, model_name,
                        )

                        compile_res = compile_project(work_dir)
                        result.compiles = compile_res.success

                        if compile_res.success:
                            test_res = run_suite_and_get_result(
                                work_dir, record.class_name
                            )
                            result.passes = test_res.passed
                            if test_res.error_message:
                                result.notes = test_res.error_message[:500]
                        else:
                            result.notes = "; ".join(compile_res.errors[:3])

                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.error_category = classify_error(
                            generated, record.assertions,
                            result.compiles, result.passes,
                        )

                        if result.passes:
                            click.echo(click.style(
                                f"PASS (sim={result.semantic_similarity:.2f}, "
                                f"cat={result.error_category.value})", fg="green"
                            ))
                        else:
                            click.echo(click.style(
                                f"FAIL (sim={result.semantic_similarity:.2f}, "
                                f"cat={result.error_category.value})", fg="red"
                            ))

                    store.save_result(result)
                    all_results.append(result)

    # Generate reports
    if all_results:
        click.echo(f"\n{'='*60}")
        click.echo("Generating reports...")
        generate_full_report(all_results, config.output_dir / "reports")

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
        raise click.ClickException("No results database found. Run the experiment first:\n  bewt-pipeline run --app expresscart --model gpt-4o --treatment A --treatment B")

    store = ResultStore(db_path)

    rows = store.get_all_results()
    if not rows:
        raise click.ClickException("No results found in database. Run the experiment first.")

    click.echo(f"Found {len(rows)} results")
    summary = store.get_summary()
    click.echo(json.dumps(summary, indent=2))

    store.close()


@main.command()
@click.pass_context
def info(ctx):
    """Show information about configured apps and available tests."""
    config = ctx.obj["config"]

    if not config.bewt_repo_path.exists():
        raise click.ClickException(
            f"BEWT repo not found at: {config.bewt_repo_path}\n"
            f"  Clone it with: git clone https://github.com/nicorubi/BEWT.git {config.bewt_repo_path}\n"
            f"  Or update bewt_repo_path in config/apps.yaml"
        )

    for app_name, app_config in config.apps.items():
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)
        gherkin_dir = config.get_gherkin_path(app_name)

        test_count = len(list(test_dir.glob("*.java"))) if test_dir.exists() else 0
        gherkin_count = len(list(gherkin_dir.glob("*.feature"))) if gherkin_dir.exists() else 0

        click.echo(f"{app_name} ({version}): {test_count} test files, {gherkin_count} feature files")


if __name__ == "__main__":
    main()
