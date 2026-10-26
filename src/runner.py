from __future__ import annotations

"""Core experiment runner — no CLI dependency. Can be called from notebooks."""

from pathlib import Path
from typing import Callable

from .config import Config
from .models import ExperimentResult, ErrorCategory
from .parsing.java_parser import parse_all_tests, resolve_strings_constants
from .parsing.gherkin_parser import parse_all_gherkin
from .variants.generator import generate_variant_a, generate_variant_b, gherkin_comment
from .variants.html_capture import capture_html_static
from .llm.client import create_client
from .llm.prompt_builder import build_assertion_prompt, treatment_ingredients
from .llm.types import LLMCall, LLMResponse
from .llm.observability import emit_llm_call, flush_observability
from .llm.response_parser import (
    extract_assertion_from_response,
    has_extra_statements,
    validate_assertion,
)
from .execution.java_injector import fill_placeholders
from .execution.reference import ReferenceRunError, load_reference, run_isolated
from .evaluation.comparator import classify_outcome, compute_semantic_similarity, check_exact_match
from .evaluation.reporter import generate_full_report
from .data.store import ResultStore

# on_progress(completed, total, treatment, class_name, message)
ProgressCallback = Callable[[int, int, str, str, str], None]


class MissingContextError(RuntimeError):
    """A treatment's context source (HTML / state DOM) is missing for some tests.

    Raised before any LLM call: silently dropping the source would make e.g.
    treatment C identical to B and break the paired A/B/C/D comparison.
    """


def _build_generation_call(
    record,
    treatment: str,
    model_name: str,
    system: str,
    user: str,
    response: LLMResponse,
    experiment_id: int | None = None,
    source: str = "bewt",
    run: int = 1,
) -> LLMCall:
    return LLMCall(
        experiment_id=experiment_id,
        call_type="generation",
        app=record.app,
        class_name=record.class_name,
        method_name=record.method_name,
        treatment=treatment,
        source=source,
        run=run,
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
        from_cache=response.from_cache,
        served_model=response.served_model,
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
    runs: int = 1,
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
                total += n_tests * len(treatments) * runs
    return total


def _load_app_records(config: Config, app_name: str, limit: int | None):
    """Parse an app's tests; returns (records, version, strings_source)."""
    app_config = config.apps[app_name]
    version = app_config["versions"][0]
    strings_path = (
        config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
    )
    constants = resolve_strings_constants(strings_path)
    strings_source = strings_path.read_text() if strings_path.exists() else ""
    records = parse_all_tests(
        config.get_app_test_path(app_name), app_name, app_config["variant"], version, constants
    )
    if limit is not None:
        records = sorted(records, key=lambda r: r.class_name)[:limit]
    return records, version, strings_source


def _check_html_available(
    config: Config, apps: list[str], treatments: tuple[str, ...], limit: int | None,
) -> None:
    """Fail before any LLM call if an HTML treatment lacks a captured page."""
    html_treatments = [t for t in treatments if treatment_ingredients(t).html]
    if not html_treatments:
        return
    missing = []
    for app_name in apps:
        if not config.get_app_test_path(app_name).exists():
            continue
        records, _, _ = _load_app_records(config, app_name, limit)
        missing += [
            f"{app_name}/{r.class_name}" for r in records
            if capture_html_static(config, app_name, r.class_name) is None
        ]
    if missing:
        raise MissingContextError(
            f"Treatment(s) {', '.join(html_treatments)} need the captured HTML page, "
            f"but {len(missing)} test(s) have none:\n  " + "\n  ".join(missing)
            + "\nRun 'bewt-pipeline reference-run --app <app>' first."
        )


def run_experiment(
    config: Config,
    apps: list[str] | None = None,
    models: list[str] | None = None,
    treatments: tuple[str, ...] = ("A", "B", "C"),
    execute: bool = False,
    on_progress: ProgressCallback | None = None,
    limit: int | None = None,
    runs: int = 1,
) -> list[ExperimentResult]:
    """Run the full experiment pipeline.

    Args:
        config: Pipeline configuration.
        apps: App names to process (default: all configured apps).
        models: Model names to use (default: config default).
        treatments: Treatment codes to run.
        execute: If True, compile and run assertions against live apps.
        on_progress: Callback for progress reporting.
        runs: Repetitions; every (test, treatment) is generated `runs` times
            with independent LLM calls.

    Returns:
        List of ExperimentResult objects.
    """
    apps = apps or list(config.apps.keys())
    models = models or [config.default_model]
    _check_html_available(config, apps, treatments, limit)
    if execute:
        for app_name in apps:
            try:
                load_reference(config, app_name)
            except ReferenceRunError as e:
                raise MissingContextError(f"--execute needs a reference run: {e}")

    total = count_experiments(config, apps, models, treatments, limit=limit, runs=runs)
    store = ResultStore(config.output_dir / "results.db")
    all_results = []
    completed = 0

    def _progress(treatment: str, class_name: str, message: str):
        if on_progress:
            on_progress(completed, total, treatment, class_name, message)

    for model_name, run in [(m, r) for m in models for r in range(1, runs + 1)]:
        _progress("", "", f"model:{model_name}" + (f" (run {run}/{runs})" if runs > 1 else ""))
        llm = create_client(config, model_name, run=run)

        for app_name in apps:
            test_dir = config.get_app_test_path(app_name)
            if not test_dir.exists():
                _progress("", "", f"skip:{app_name}")
                continue

            _progress("", "", f"app:{app_name}")
            records, version, strings_source = _load_app_records(config, app_name, limit)

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
                    if store.has_result(app_name, version, record.class_name, t, model_name, run=run):
                        _progress(t, record.class_name, "skipped")
                        continue

                    # Resolve which context sources this treatment includes,
                    # then assemble the prompt.
                    ingredients = treatment_ingredients(t)

                    # A test without a Gherkin specification gets no comment
                    # (B equals A for it); the prompt then says nothing about
                    # comments either.
                    has_spec = gherkin_comment(record, gherkin_map) is not None
                    include_comments = ingredients.comments and has_spec
                    if include_comments:
                        variant_source = generate_variant_b(record, gherkin_map)
                    else:
                        variant_source = generate_variant_a(record)
                    notes = []
                    if ingredients.comments and not has_spec:
                        notes.append("no Gherkin spec: prompt has no comment")

                    html = None
                    if ingredients.html:
                        # Presence is guaranteed by _check_html_available.
                        html = capture_html_static(config, app_name, record.class_name)

                    system, user = build_assertion_prompt(
                        record,
                        variant_source=variant_source,
                        include_comments=include_comments,
                        html_content=html,
                        strings_source=strings_source if ingredients.project else None,
                        page_object_sources=(
                            record.page_object_sources if ingredients.project else None
                        ),
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
                            test_record=record, treatment=t, model=model_name, run=run,
                            prompt=user, raw_response=str(e),
                            generated_assertion="",
                            error_category=ErrorCategory.NOT_EXECUTABLE,
                            notes=f"LLM error: {e}",
                        )
                        experiment_id = store.save_result(result)
                        _save_and_emit_llm_call(store, _build_generation_call(
                            record, t, model_name, system, user, error_response, experiment_id,
                            run=run,
                        ))
                        all_results.append(result)
                        _progress(t, record.class_name, f"error:{e}")
                        continue

                    # Extract and validate assertion
                    generated = extract_assertion_from_response(raw_response)
                    valid = validate_assertion(generated)

                    result = ExperimentResult(
                        test_record=record, treatment=t, model=model_name, run=run,
                        prompt=user, raw_response=raw_response,
                        generated_assertion=generated,
                    )
                    if valid and has_extra_statements(generated):
                        notes.append("answer contains non-assertion statements")

                    if not valid:
                        result.error_category = ErrorCategory.NOT_EXECUTABLE
                        notes.append("answer contains no assertion call")
                        _progress(t, record.class_name, "invalid")
                    elif not execute:
                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        notes.append("execution skipped")
                        _progress(t, record.class_name, f"sim={result.semantic_similarity:.2f}")
                    else:
                        filled, fill_note = fill_placeholders(variant_source, generated)
                        if fill_note:
                            notes.append(fill_note)
                        work_dir = config.output_dir / "runs" / app_name / model_name / t / record.class_name
                        compile_res, test_res = run_isolated(
                            config, app_name, record.class_name, filled, work_dir,
                        )
                        result.compiles = compile_res.success
                        error_type = ""
                        if test_res is not None:
                            result.passes = test_res.passed
                            error_type = test_res.error_type
                            result.failure_type = "" if test_res.passed else error_type
                            if test_res.error_message:
                                notes.append(test_res.error_message[:500])
                        else:
                            notes.append("; ".join(compile_res.errors[:3]))

                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.error_category = classify_outcome(
                            generated, record.assertions,
                            result.compiles, result.passes, error_type,
                        )
                        status = "PASS" if result.passes else "FAIL"
                        _progress(t, record.class_name,
                                  f"{status}:sim={result.semantic_similarity:.2f},cat={result.error_category.value}")

                    result.notes = " | ".join(notes)
                    experiment_id = store.save_result(result)
                    _save_and_emit_llm_call(store, _build_generation_call(
                        record, t, model_name, system, user, llm_response, experiment_id,
                        run=run,
                    ))
                    all_results.append(result)

    # Generate reports
    if all_results:
        generate_full_report(all_results, config.output_dir / "reports")

    store.close()
    flush_observability()
    return all_results


def run_crawled_experiment(
    config: Config,
    app: str,
    models: list[str] | None = None,
    treatments: tuple[str, ...] = ("A", "B", "C"),
    execute: bool = False,
    on_progress: ProgressCallback | None = None,
    limit: int | None = None,
) -> list[ExperimentResult]:
    """Run the assertion-generation pipeline on crawler-generated (DANTE) tests.

    Differences from :func:`run_experiment`:
    - tests come from the ingested DANTE suite (``ingest-crawl``), have no gold
      standard, and therefore no exact-match / similarity metrics;
    - context sources are the auto-generated action trace (treatment B) and the
      crawled per-state DOM (treatment C) — no Gherkin / Page Objects / Strings;
    - with ``execute``, tests run inside a copy of the DANTE test project with
      the pinned JDK 8 toolchain, against a freshly reset app container, and
      the stored error category is execution-based: correct if the assertion
      holds on the live app, wrong_assertion if it fails.
    """
    from .ingest.dante import (
        crawled_variant_source,
        ingest_dante_suite,
        to_test_records,
    )
    from .llm.prompt_builder import (
        build_crawled_assertion_prompt,
        crawled_treatment_ingredients,
    )
    from .execution.dante_runner import (
        compile_crawled_project,
        prepare_crawled_project_copy,
        reset_crawled_app,
        run_crawled_test,
    )

    models = models or [config.default_model]
    app_config = config.apps.get(app, {})
    version = (app_config.get("versions") or ["crawled"])[0]

    suite_path = config.get_dante_suite_path(app)
    crawl_dir = config.get_dante_crawl_dir(app)
    if not suite_path.exists():
        raise FileNotFoundError(
            f"DANTE-generated suite not found: {suite_path}\n"
            f"Run the crawl first (see docs/dante-setup.md)."
        )

    crawled = ingest_dante_suite(app, suite_path, crawl_dir, config.output_dir / "crawled" / app)
    by_class = {t.class_name: t for t in crawled}
    records = to_test_records(crawled, version)
    if limit is not None:
        records = records[:limit]

    html_treatments = [t for t in treatments if crawled_treatment_ingredients(t).html]
    missing = [r.class_name for r in records if not by_class[r.class_name].state_dom_path]
    if html_treatments and missing:
        raise MissingContextError(
            f"Treatment(s) {', '.join(html_treatments)} need the crawled state DOM, "
            f"but {len(missing)} test(s) have none:\n  " + "\n  ".join(missing)
        )

    total = len(records) * len(treatments) * len(models)
    store = ResultStore(config.output_dir / "results.db")
    all_results: list[ExperimentResult] = []
    completed = 0

    def _progress(treatment: str, class_name: str, message: str):
        if on_progress:
            on_progress(completed, total, treatment, class_name, message)

    for model_name in models:
        _progress("", "", f"model:{model_name}")
        llm = create_client(config, model_name)

        for record in records:
            crawled_test = by_class[record.class_name]

            for t in treatments:
                completed += 1

                if store.has_result(app, version, record.class_name, t,
                                    model_name, source="crawled"):
                    _progress(t, record.class_name, "skipped")
                    continue

                ingredients = crawled_treatment_ingredients(t)

                variant_source = crawled_variant_source(
                    record, crawled_test.trace_comment if ingredients.comments else None
                )

                html = None
                if ingredients.html:
                    # Presence is guaranteed by the check above.
                    html = Path(crawled_test.state_dom_path).read_text()

                system, user = build_crawled_assertion_prompt(
                    record,
                    variant_source=variant_source,
                    include_comments=ingredients.comments,
                    html_content=html,
                )

                llm_response = None
                try:
                    llm_response = llm.generate(system, user)
                    raw_response = llm_response.text
                except Exception as e:
                    error_response = LLMResponse(text=str(e), provider="", model=model_name)
                    result = ExperimentResult(
                        test_record=record, treatment=t, model=model_name,
                        source="crawled",
                        prompt=user, raw_response=str(e), generated_assertion="",
                        error_category=ErrorCategory.NOT_EXECUTABLE,
                        notes=f"LLM error: {e}",
                    )
                    experiment_id = store.save_result(result)
                    _save_and_emit_llm_call(store, _build_generation_call(
                        record, t, model_name, system, user, error_response,
                        experiment_id, source="crawled",
                    ))
                    all_results.append(result)
                    _progress(t, record.class_name, f"error:{e}")
                    continue

                generated = extract_assertion_from_response(raw_response)
                valid = validate_assertion(generated)

                result = ExperimentResult(
                    test_record=record, treatment=t, model=model_name,
                    source="crawled",
                    prompt=user, raw_response=raw_response,
                    generated_assertion=generated,
                )

                if not valid:
                    result.error_category = ErrorCategory.NOT_EXECUTABLE
                    result.notes = "Generated assertion failed validation"
                    _progress(t, record.class_name, "invalid")
                elif not execute:
                    result.notes = "execution skipped; no gold standard for crawled tests"
                    _progress(t, record.class_name, "generated")
                else:
                    reset_crawled_app(config, app)
                    work_dir = prepare_crawled_project_copy(
                        config, app, record, generated, variant_source, t, model_name,
                    )
                    compile_res = compile_crawled_project(work_dir)
                    result.compiles = compile_res.success

                    if compile_res.success:
                        test_res = run_crawled_test(work_dir, record.class_name)
                        result.passes = test_res.passed
                        if test_res.error_message:
                            result.notes = test_res.error_message[:500]
                        result.error_category = (
                            ErrorCategory.CORRECT if test_res.passed
                            else ErrorCategory.WRONG_ASSERTION
                        )
                    else:
                        result.error_category = ErrorCategory.NOT_EXECUTABLE
                        result.notes = "; ".join(compile_res.errors[:3])

                    note_suffix = "category is execution-based (no gold standard)"
                    result.notes = (result.notes + "; " if result.notes else "") + note_suffix
                    status = "PASS" if result.passes else "FAIL"
                    _progress(t, record.class_name,
                              f"{status}:cat={result.error_category.value}")

                experiment_id = store.save_result(result)
                _save_and_emit_llm_call(store, _build_generation_call(
                    record, t, model_name, system, user, llm_response,
                    experiment_id, source="crawled",
                ))
                all_results.append(result)

    store.close()
    flush_observability()
    return all_results
