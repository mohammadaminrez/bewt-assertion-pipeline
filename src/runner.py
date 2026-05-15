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
    build_prompt_a, build_prompt_b, build_prompt_c, build_prompt_d,
    build_prompt_with_page_objects,
)
from .llm.types import LLMCall, LLMResponse
from .llm.observability import emit_llm_call, flush_observability
from .llm.response_parser import extract_assertion_from_response, validate_assertion
from .execution.java_injector import prepare_project_copy
from .execution.test_runner import compile_project, run_single_test
from .evaluation.comparator import classify_error, compute_semantic_similarity, check_exact_match
from .evaluation.reporter import generate_full_report
from .data.store import ResultStore

# on_progress(completed, total, treatment, class_name, message)
ProgressCallback = Callable[[int, int, str, str, str], None]


def _build_generation_call(
    record,
    treatment: str,
    model_name: str,
    system: str,
    user: str,
    response: LLMResponse,
    experiment_id: int | None = None,
) -> LLMCall:
    return LLMCall(
        experiment_id=experiment_id,
        call_type="generation",
        app=record.app,
        class_name=record.class_name,
        method_name=record.method_name,
        treatment=treatment,
        model=model_name,
        provider=response.provider,
        system_prompt=system,
        user_prompt=user,
        raw_response=response.text,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        cached_input_tokens=response.cached_input_tokens,
        cache_creation_input_tokens=response.cache_creation_input_tokens,
        cache_read_input_tokens=response.cache_read_input_tokens,
        reasoning_tokens=response.reasoning_tokens,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
    )


def _save_and_emit_llm_call(store: ResultStore, call: LLMCall) -> int:
    call_id = store.save_llm_call(call)
    call.id = call_id
    emit_llm_call(call)
    return call_id


def count_experiments(
    config: Config,
    apps: list[str],
    models: list[str],
    treatments: tuple[str, ...],
    limit: int | None = None,
) -> int:
    """Count the total number of experiments that will be run."""
    total = 0
    for _ in models:
        for app_name in apps:
            test_dir = config.get_app_test_path(app_name)
            if test_dir.exists():
                n_tests = len([f for f in test_dir.glob("*.java")
                               if f.stem not in ("BaseTest", "TestSuite", "Installer")])
                if limit is not None:
                    n_tests = min(n_tests, limit)
                total += n_tests * len(treatments)
    return total


def run_experiment(
    config: Config,
    apps: list[str] | None = None,
    models: list[str] | None = None,
    treatments: tuple[str, ...] = ("A", "B", "C"),
    execute: bool = False,
    on_progress: ProgressCallback | None = None,
    limit: int | None = None,
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

    total = count_experiments(config, apps, models, treatments, limit=limit)
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
            strings_source = strings_path.read_text() if strings_path.exists() else ""
            records = parse_all_tests(test_dir, app_name, app_config["variant"], version, constants)
            if limit is not None:
                records = sorted(records, key=lambda r: r.class_name)[:limit]

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
                    elif t == "D":
                        variant_source = generate_variant_b(record, gherkin_map)
                        html = capture_html_static(config, app_name, record.class_name)
                        if not html:
                            _progress(t, record.class_name, "no_html")
                        system, user = build_prompt_d(
                            record, variant_source, html, strings_source
                        )
                    else:
                        continue

                    # Add page object context
                    system, user = build_prompt_with_page_objects(
                        (system, user), record.page_object_sources
                    )

                    # Call LLM
                    llm_response = None
                    try:
                        llm_response = llm.generate(system, user)
                        raw_response = llm_response.text
                    except Exception as e:
                        error_response = LLMResponse(
                            text=str(e),
                            provider="",
                            model=model_name,
                        )
                        result = ExperimentResult(
                            test_record=record, treatment=t, model=model_name,
                            prompt=user, raw_response=str(e),
                            generated_assertion="",
                            error_category=ErrorCategory.NOT_EXECUTABLE,
                            notes=f"LLM error: {e}",
                        )
                        experiment_id = store.save_result(result)
                        _save_and_emit_llm_call(store, _build_generation_call(
                            record, t, model_name, system, user, error_response, experiment_id
                        ))
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
                            test_res = run_single_test(work_dir, record.class_name)
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

                    experiment_id = store.save_result(result)
                    _save_and_emit_llm_call(store, _build_generation_call(
                        record, t, model_name, system, user, llm_response, experiment_id
                    ))
                    all_results.append(result)

    # Generate reports
    if all_results:
        generate_full_report(all_results, config.output_dir / "reports")

    store.close()
    flush_observability()
    return all_results
