from __future__ import annotations

"""Core experiment runner — no CLI dependency. Can be called from notebooks."""

from typing import Callable

from .config import Config
from .models import ExperimentResult, ErrorCategory
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin
from .variants.generator import generate_variant_a, generate_variant_b
from .variants.html_capture import capture_html_static
from .llm.client import create_client
from .llm.prompt_builder import (
    build_prompt_a, build_prompt_b, build_prompt_c, build_prompt_with_page_objects,
)
from .llm.response_parser import extract_assertion_from_response, validate_assertion
from .execution.java_injector import prepare_project_copy
from .execution.test_runner import compile_project, run_suite_and_get_result
from .evaluation.comparator import classify_error, compute_semantic_similarity, check_exact_match
from .evaluation.reporter import generate_full_report
from .data.store import ResultStore

# on_progress(completed, total, treatment, class_name, message)
ProgressCallback = Callable[[int, int, str, str, str], None]


def count_experiments(config: Config, apps: list[str], models: list[str], treatments: tuple[str, ...]) -> int:
    """Count the total number of experiments that will be run."""
    total = 0
    for _ in models:
        for app_name in apps:
            test_dir = config.get_app_test_path(app_name)
            if test_dir.exists():
                n_tests = len([f for f in test_dir.glob("*.java")
                               if f.stem not in ("BaseTest", "TestSuite", "Installer")])
                total += n_tests * len(treatments)
    return total


def run_experiment(
    config: Config,
    apps: list[str] | None = None,
    models: list[str] | None = None,
    treatments: tuple[str, ...] = ("A", "B", "C"),
    execute: bool = False,
    on_progress: ProgressCallback | None = None,
) -> list[ExperimentResult]:
    """Run the full experiment pipeline.

    Args:
        config: Pipeline configuration.
        apps: App names to process (default: all configured apps).
        models: Model names to use (default: config default).
        treatments: Treatment codes to run.
        execute: If True, compile and run assertions against live apps.
        on_progress: Callback for progress reporting.

    Returns:
        List of ExperimentResult objects.
    """
    apps = apps or list(config.apps.keys())
    models = models or [config.default_model]

    total = count_experiments(config, apps, models, treatments)
    store = ResultStore(config.output_dir / "results.db")
    all_results = []
    completed = 0

    def _progress(treatment: str, class_name: str, message: str):
        if on_progress:
            on_progress(completed, total, treatment, class_name, message)

    for model_name in models:
        _progress("", "", f"model:{model_name}")
        llm = create_client(config, model_name)

        for app_name in apps:
            test_dir = config.get_app_test_path(app_name)
            if not test_dir.exists():
                _progress("", "", f"skip:{app_name}")
                continue

            _progress("", "", f"app:{app_name}")
            app_config = config.apps[app_name]
            version = app_config["versions"][0]

            # Parse tests
            strings_path = (
                config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
            )
            constants = resolve_strings_constants(strings_path)
            records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)

            # Parse gherkin
            gherkin_dir = config.get_gherkin_path(app_name)
            gherkin_map = parse_all_gherkin(gherkin_dir)

            # Load page object sources
            po_dir = config.get_app_po_path(app_name)
            po_sources = {}
            if po_dir.exists():
                for po_file in po_dir.glob("*.java"):
                    po_sources[po_file.stem] = po_file.read_text()

            for record in records:
                record.page_object_sources = po_sources

                for t in treatments:
                    completed += 1

                    # Resumability
                    if store.has_result(app_name, version, record.class_name, t, model_name):
                        _progress(t, record.class_name, "skipped")
                        continue

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
                            _progress(t, record.class_name, "no_html")
                            system, user = build_prompt_b(record, variant_source)
                    else:
                        continue

                    # Add page object context
                    system, user = build_prompt_with_page_objects(
                        (system, user), record.page_object_sources
                    )

                    # Call LLM
                    try:
                        raw_response = llm.generate(system, user)
                    except Exception as e:
                        result = ExperimentResult(
                            test_record=record, treatment=t, model=model_name,
                            prompt=user, raw_response=str(e),
                            generated_assertion="",
                            error_category=ErrorCategory.NOT_EXECUTABLE,
                            notes=f"LLM error: {e}",
                        )
                        store.save_result(result)
                        all_results.append(result)
                        _progress(t, record.class_name, f"error:{e}")
                        continue

                    # Extract and validate assertion
                    generated = extract_assertion_from_response(raw_response)
                    valid = validate_assertion(generated)

                    result = ExperimentResult(
                        test_record=record, treatment=t, model=model_name,
                        prompt=user, raw_response=raw_response,
                        generated_assertion=generated,
                    )

                    if not valid:
                        result.error_category = ErrorCategory.NOT_EXECUTABLE
                        result.notes = "Generated assertion failed validation"
                        _progress(t, record.class_name, "invalid")
                    elif not execute:
                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.notes = "execution skipped"
                        _progress(t, record.class_name, f"sim={result.semantic_similarity:.2f}")
                    else:
                        work_dir = prepare_project_copy(
                            config, app_name, record, generated,
                            variant_source, t, model_name,
                        )
                        compile_res = compile_project(work_dir)
                        result.compiles = compile_res.success

                        if compile_res.success:
                            test_res = run_suite_and_get_result(work_dir, record.class_name)
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
                        _progress(t, record.class_name,
                                  f"{status}:sim={result.semantic_similarity:.2f},cat={result.error_category.value}")

                    store.save_result(result)
                    all_results.append(result)

    # Generate reports
    if all_results:
        generate_full_report(all_results, config.output_dir / "reports")

    store.close()
    return all_results
