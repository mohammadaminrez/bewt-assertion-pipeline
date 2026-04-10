from __future__ import annotations

"""CLI entry point for the BEWT assertion generation pipeline."""

import json
import sys
from pathlib import Path

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
    apps = app if app else list(config.apps.keys())

    for app_name in apps:
        click.echo(f"\n=== Parsing {app_name} ===")
        app_config = config.apps[app_name]
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)

        if not test_dir.exists():
            click.echo(f"  Test dir not found: {test_dir}")
            continue

        # Load Strings constants if available
        strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
        constants = resolve_strings_constants(strings_path)

        records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)
        click.echo(f"  Found {len(records)} test files with {sum(r.assertion_count for r in records)} assertions")

        # Save parsed data
        out = config.output_dir / "parsed" / app_name
        out.mkdir(parents=True, exist_ok=True)
        for r in records:
            data = r.to_dict()
            data["stripped_source"] = r.stripped_source
            (out / f"{r.class_name}.json").write_text(json.dumps(data, indent=2))

    click.echo("\nParsing complete.")


@main.command()
@click.option("--app", "-a", multiple=True, help="App(s) to process")
@click.pass_context
def generate_variants(ctx, app):
    """Generate test variants A, B, C."""
    config = ctx.obj["config"]
    apps = app if app else list(config.apps.keys())

    for app_name in apps:
        click.echo(f"\n=== Generating variants for {app_name} ===")
        app_config = config.apps[app_name]
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)

        if not test_dir.exists():
            continue

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
@click.option("--treatment", "-t", multiple=True, help="Treatment(s): A, B, C")
@click.option("--skip-execution", is_flag=True, help="Skip test execution (only generate assertions)")
@click.pass_context
def run(ctx, app, model, treatment, skip_execution):
    """Run the full experiment pipeline."""
    config = ctx.obj["config"]
    apps = app if app else list(config.apps.keys())
    models = model if model else [config.default_model]
    treatments = treatment if treatment else ["A", "B", "C"]

    store = ResultStore(config.output_dir / "results.db")
    docker = DockerManager(config)
    all_results = []

    for model_name in models:
        click.echo(f"\n{'='*60}")
        click.echo(f"Model: {model_name}")
        click.echo(f"{'='*60}")

        try:
            llm = create_client(config, model_name)
        except ValueError as e:
            click.echo(f"  Error: {e}")
            continue

        for app_name in apps:
            click.echo(f"\n--- {app_name} ---")
            app_config = config.apps[app_name]
            version = app_config["versions"][0]
            test_dir = config.get_app_test_path(app_name)

            if not test_dir.exists():
                click.echo(f"  Test dir not found, skipping")
                continue

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
                    # Check if already done (resumability)
                    if store.has_result(app_name, version, record.class_name, t, model_name):
                        click.echo(f"  [{t}] {record.class_name}: already done, skipping")
                        continue

                    click.echo(f"  [{t}] {record.class_name}: ", nl=False)

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
                        click.echo(f"LLM error: {e}")
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
                        click.echo("invalid assertion")
                    elif skip_execution:
                        # Just compute similarity without running
                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.notes = "execution skipped"
                        click.echo(f"sim={result.semantic_similarity:.2f}")
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

                        status = "PASS" if result.passes else "FAIL"
                        click.echo(
                            f"{status} (sim={result.semantic_similarity:.2f}, "
                            f"cat={result.error_category.value})"
                        )

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
    store = ResultStore(config.output_dir / "results.db")

    rows = store.get_all_results()
    if not rows:
        click.echo("No results found. Run the experiment first.")
        return

    click.echo(f"Found {len(rows)} results")
    summary = store.get_summary()
    click.echo(json.dumps(summary, indent=2))

    store.close()


@main.command()
@click.pass_context
def info(ctx):
    """Show information about configured apps and available tests."""
    config = ctx.obj["config"]

    for app_name, app_config in config.apps.items():
        version = app_config["versions"][0]
        test_dir = config.get_app_test_path(app_name)
        gherkin_dir = config.get_gherkin_path(app_name)

        test_count = len(list(test_dir.glob("*.java"))) if test_dir.exists() else 0
        gherkin_count = len(list(gherkin_dir.glob("*.feature"))) if gherkin_dir.exists() else 0

        click.echo(f"{app_name} ({version}): {test_count} test files, {gherkin_count} feature files")


if __name__ == "__main__":
    main()
