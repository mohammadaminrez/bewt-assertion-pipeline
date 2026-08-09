from __future__ import annotations

"""Core experiment runner — no CLI dependency. Can be called from notebooks."""

from typing import Callable

from .config import Config
from .models import ErrorCategory, ExecutionOutcome, ExperimentResult, TestRecord
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin
from .variants.generator import generate_variant_a, generate_variant_b
from .variants.html_capture import capture_html_static
from .llm.client import create_client
from .llm.prompt_builder import build_assertion_prompt, treatment_ingredients
from .llm.types import LLMCall, LLMResponse
from .llm.observability import emit_llm_call, flush_observability
from .llm.response_parser import extract_assertion_from_response, validate_assertion
from .execution.java_injector import (
    classes_with_compile_errors,
    prepare_suite_copy,
    restore_gold_assertion,
)
from .execution.test_runner import compile_project, run_test_suite
from .evaluation.comparator import compute_semantic_similarity, check_exact_match
from .evaluation.reporter import generate_full_report
from .data.store import ResultStore

# A generated assertion that does not compile breaks the whole suite build, so
# offenders are reverted to their gold assertion and the build is retried. The
# cap stops a pathological case from looping forever.
MAX_COMPILE_ATTEMPTS = 6

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
    mode: str = "cumulative",
) -> LLMCall:
    return LLMCall(
        experiment_id=experiment_id,
        call_type="generation",
        app=record.app,
        class_name=record.class_name,
        method_name=record.method_name,
        treatment=treatment,
        mode=mode,
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


def _outcome_from_test_result(test_result) -> tuple[ExecutionOutcome, str]:
    """Translate a Surefire testcase into an outcome.

    A JUnit *failure* is the assertion rejecting a value — a genuine verdict on
    the assertion. An *error* is any other exception, meaning the test never
    reached the assertion, so it says nothing about assertion quality.
    """
    if test_result.passed:
        return ExecutionOutcome.PASSED, ""
    if test_result.failed:
        return ExecutionOutcome.FAILED, test_result.error_type
    return ExecutionOutcome.ERRORED, test_result.error_type


def execute_treatment_suite(
    config: Config,
    app: str,
    records: list[TestRecord],
    generated: dict[str, str],
    variant_sources: dict[str, str],
    treatment: str,
    model: str,
    results: dict[str, ExperimentResult],
    on_message=None,
) -> None:
    """Run one suite containing every generated assertion for this treatment.

    Tests are order-dependent, so the whole suite runs once and each test's
    outcome is read back from the Surefire reports. Results are written onto
    ``results`` in place.
    """
    def _say(msg: str):
        if on_message:
            on_message(msg)

    by_class = {r.class_name: r for r in records}
    work_dir = prepare_suite_copy(
        config, app, records, generated, variant_sources, treatment, model
    )

    # Build the suite, dropping assertions that do not compile until it builds.
    # A reverted test keeps its gold assertion so it still creates the state
    # that later tests in the suite depend on.
    remaining = dict(generated)
    compiled = False
    for _ in range(MAX_COMPILE_ATTEMPTS):
        if not remaining:
            break
        compile_result = compile_project(work_dir)
        if compile_result.success:
            compiled = True
            break

        blamed = classes_with_compile_errors(compile_result.errors, records) & set(remaining)
        if not blamed:
            # Cannot attribute the failure to any one test — fail them all
            # rather than silently reporting a wrong outcome.
            detail = "; ".join(compile_result.errors[:3])
            for class_name in remaining:
                results[class_name].set_outcome(ExecutionOutcome.NOT_COMPILED)
                results[class_name].notes = detail
            _say(f"suite build failed, unattributable: {detail[:120]}")
            return

        for class_name in blamed:
            restore_gold_assertion(work_dir, by_class[class_name])
            results[class_name].set_outcome(ExecutionOutcome.NOT_COMPILED)
            results[class_name].notes = "; ".join(
                e for e in compile_result.errors if f"{class_name}.java" in e
            )[:500]
            del remaining[class_name]
        _say(f"reverted {len(blamed)} non-compiling assertion(s): {', '.join(sorted(blamed))}")

    if not remaining:
        _say("no compilable assertions in this treatment; suite not run")
        return
    if not compiled:
        for class_name in remaining:
            results[class_name].set_outcome(ExecutionOutcome.NOT_COMPILED)
            results[class_name].notes = "suite still failed to compile after retries"
        return

    _say(f"running suite with {len(remaining)} generated assertion(s)")
    test_results = run_test_suite(work_dir)
    by_test_class = {}
    for tr in test_results:
        by_test_class[tr.test_class.rsplit(".", 1)[-1]] = tr

    for class_name in remaining:
        tr = by_test_class.get(class_name)
        if tr is None:
            results[class_name].set_outcome(ExecutionOutcome.ERRORED)
            results[class_name].notes = "no Surefire result for this test"
            continue
        outcome, error_type = _outcome_from_test_result(tr)
        results[class_name].set_outcome(outcome, error_type)
        if tr.error_message:
            results[class_name].notes = tr.error_message[:500]


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
    mode: str = "cumulative",
) -> list[ExperimentResult]:
    """Run the full experiment pipeline.

    Args:
        config: Pipeline configuration.
        apps: App names to process (default: all configured apps).
        models: Model names to use (default: config default).
        treatments: Treatment codes to run.
        execute: If True, compile and run assertions against live apps.
        on_progress: Callback for progress reporting.
        mode: Experiment design — "cumulative" (context added step by step) or
            "singular" (each treatment isolates one context source).

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

            # Treatment is the outer loop: every assertion for a treatment is
            # generated first, then the suite runs once with all of them
            # injected. BEWT tests depend on state created by earlier tests, so
            # a test only reaches its assertion when the suite runs in order.
            for t in treatments:
                ingredients = treatment_ingredients(mode, t)

                generated_by_class: dict[str, str] = {}
                variant_by_class: dict[str, str] = {}
                results_by_class: dict[str, ExperimentResult] = {}
                calls_by_class: dict[str, tuple] = {}

                for record in records:
                    completed += 1

                    # Resumability. The record still stays in the suite with its
                    # gold assertion so later tests keep their preconditions.
                    if store.has_result(app_name, version, record.class_name, t, model_name, mode):
                        _progress(t, record.class_name, "skipped")
                        continue

                    if ingredients.comments:
                        variant_source = generate_variant_b(record, gherkin_map)
                    else:
                        variant_source = generate_variant_a(record)
                    variant_by_class[record.class_name] = variant_source

                    html = None
                    if ingredients.html:
                        html = capture_html_static(config, app_name, record.class_name)
                        if not html:
                            _progress(t, record.class_name, "no_html")

                    system, user = build_assertion_prompt(
                        record,
                        variant_source=variant_source,
                        include_comments=ingredients.comments,
                        html_content=html,
                        strings_source=strings_source if ingredients.project else None,
                        page_object_sources=(
                            record.page_object_sources if ingredients.project else None
                        ),
                    )

                    try:
                        llm_response = llm.generate(system, user)
                        raw_response = llm_response.text
                    except Exception as e:
                        error_response = LLMResponse(text=str(e), provider="", model=model_name)
                        result = ExperimentResult(
                            test_record=record, treatment=t, model=model_name, mode=mode,
                            prompt=user, raw_response=str(e),
                            generated_assertion="",
                            error_category=ErrorCategory.NOT_EXECUTABLE,
                            notes=f"LLM error: {e}",
                        )
                        experiment_id = store.save_result(result)
                        _save_and_emit_llm_call(store, _build_generation_call(
                            record, t, model_name, system, user, error_response, experiment_id, mode
                        ))
                        all_results.append(result)
                        _progress(t, record.class_name, f"error:{e}")
                        continue

                    generated = extract_assertion_from_response(raw_response)
                    result = ExperimentResult(
                        test_record=record, treatment=t, model=model_name, mode=mode,
                        prompt=user, raw_response=raw_response,
                        generated_assertion=generated,
                    )
                    result.exact_match = check_exact_match(generated, record.assertions)
                    result.semantic_similarity = compute_semantic_similarity(
                        generated, record.assertions
                    )
                    results_by_class[record.class_name] = result
                    calls_by_class[record.class_name] = (system, user, llm_response)

                    if not validate_assertion(generated):
                        result.error_category = ErrorCategory.NOT_EXECUTABLE
                        result.set_outcome(ExecutionOutcome.NOT_COMPILED)
                        result.notes = "Generated assertion failed validation"
                        _progress(t, record.class_name, "invalid")
                    elif not execute:
                        result.notes = "execution skipped"
                        _progress(t, record.class_name, f"sim={result.semantic_similarity:.2f}")
                    else:
                        generated_by_class[record.class_name] = generated

                if execute and generated_by_class:
                    _progress(t, "", f"suite:{len(generated_by_class)} tests")
                    execute_treatment_suite(
                        config, app_name, records, generated_by_class, variant_by_class,
                        t, model_name, results_by_class,
                        on_message=lambda msg: _progress(t, "", msg),
                    )
                    for class_name, result in results_by_class.items():
                        # Only the mechanical part is decided here. Whether an
                        # assertion is over- or under-assertive needs a reviewer
                        # who can read the Page Object methods it calls, so that
                        # label stays PENDING until import-excel supplies it.
                        result.error_category = (
                            ErrorCategory.PENDING if result.compiles
                            else ErrorCategory.NOT_EXECUTABLE
                        )
                        _progress(t, class_name, result.outcome.value)

                for record in records:
                    result = results_by_class.get(record.class_name)
                    if result is None:
                        continue
                    system, user, llm_response = calls_by_class[record.class_name]
                    experiment_id = store.save_result(result)
                    _save_and_emit_llm_call(store, _build_generation_call(
                        record, t, model_name, system, user, llm_response, experiment_id, mode
                    ))
                    all_results.append(result)

    # Generate reports
    if all_results:
        generate_full_report(all_results, config.output_dir / "reports")

    store.close()
    flush_observability()
    return all_results
