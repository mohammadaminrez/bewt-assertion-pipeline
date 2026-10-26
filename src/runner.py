from __future__ import annotations

"""Core experiment runner — no CLI dependency. Can be called from notebooks."""

import re
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
from .execution.reference import ReferenceRunError, load_reference, reference_dir, run_isolated
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


def _variant_source(record, treatment: str, gherkin_map) -> tuple[str, bool, bool]:
    """Test code shown for a treatment: (source, include_comments, has_spec).

    A test without a Gherkin specification gets no comment (B equals A for
    it); the prompt then says nothing about comments either.
    """
    ingredients = treatment_ingredients(treatment)
    has_spec = gherkin_comment(record, gherkin_map) is not None
    include_comments = ingredients.comments and has_spec
    source = generate_variant_b(record, gherkin_map) if include_comments else generate_variant_a(record)
    return source, include_comments, has_spec


def _judge(config: Config, app_name: str, record, variant_source: str, generated: str,
           model_name: str, treatment: str) -> dict:
    """Execute one answer in isolation and decide its category."""
    filled, fill_note = fill_placeholders(variant_source, generated)
    work_dir = config.output_dir / "runs" / app_name / model_name / treatment / record.class_name
    compile_res, test_res = run_isolated(config, app_name, record.class_name, filled, work_dir)
    notes = [fill_note] if fill_note else []
    passes, error_type = False, ""
    if test_res is not None:
        passes, error_type = test_res.passed, test_res.error_type
        if test_res.error_message:
            notes.append(test_res.error_message[:500])
    else:
        notes.append("; ".join(compile_res.errors[:3]))
    return {
        "compiles": compile_res.success,
        "passes": passes,
        "failure_type": "" if passes else error_type,
        "category": classify_outcome(generated, record.assertions, compile_res.success, passes, error_type),
        "notes": notes,
    }


def rescore_experiment(config: Config, app: str, on_progress: Callable[[str], None] = print) -> dict:
    """Re-execute every stored answer with the current harness (no LLM calls).

    The answers stay exactly as the model wrote them; only compiling,
    running and classifying is redone. Manual review labels are cleared for
    answers whose outcome changed, since they were given for another verdict.
    """
    load_reference(config, app)
    records, _, _ = _load_app_records(config, app, None)
    by_class = {r.class_name: r for r in records}
    gherkin_map = parse_all_gherkin(config.get_gherkin_path(app))
    store = ResultStore(config.output_dir / "results.db")
    rows = [r for r in store.get_all_results() if r["app"] == app and r["source"] == "bewt"]
    changed = []
    for i, row in enumerate(rows, 1):
        record = by_class[row["class_name"]]
        source, _, _ = _variant_source(record, row["treatment"], gherkin_map)
        generated = row["generated_assertion"] or ""
        if not validate_assertion(generated):
            continue
        verdict = _judge(config, app, record, source, generated, row["model"], row["treatment"])
        kept_notes = [n for n in (row["notes"] or "").split(" | ")
                      if n.startswith(("no Gherkin spec", "answer contains"))]
        new = (int(verdict["compiles"]), int(verdict["passes"]), verdict["category"].value)
        old = (row["compiles"], row["passes"], row["error_category"])
        if new != old:
            changed.append({"class": row["class_name"], "treatment": row["treatment"], "run": row["run"],
                            "old": old, "new": new})
        store.conn.execute(
            "UPDATE experiments SET compiles=?, passes=?, failure_type=?, error_category=?, notes=?"
            + (", manual_error_category=NULL, manual_notes=NULL" if new != old else "")
            + " WHERE id=?",
            (*new[:2], verdict["failure_type"], new[2], " | ".join(kept_notes + verdict["notes"]), row["id"]),
        )
        store.conn.commit()
        on_progress(f"  [{i}/{len(rows)}] {row['class_name']} [{row['treatment']}] run{row['run']}: "
                    f"{verdict['category'].value}" + ("   (CHANGED)" if new != old else ""))
    store.close()
    return {"rescored": len(rows), "changed": changed}


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

                    variant_source, include_comments, has_spec = _variant_source(record, t, gherkin_map)
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
                        verdict = _judge(config, app_name, record, variant_source, generated, model_name, t)
                        result.compiles = verdict["compiles"]
                        result.passes = verdict["passes"]
                        result.failure_type = verdict["failure_type"]
                        notes.extend(verdict["notes"])

                        result.exact_match = check_exact_match(generated, record.assertions)
                        result.semantic_similarity = compute_semantic_similarity(
                            generated, record.assertions
                        )
                        result.error_category = verdict["category"]
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
    - context sources are the action trace rendered from the test's own
      statements (treatment B) and the page captured at the assertion point by
      the crawled reference run (treatment C) — no Gherkin / Page Objects;
    - with ``execute``, each answer runs alone (DANTE's JDK 8 toolchain) on the
      app state saved just before its test in the crawled reference run;
      categories come from execution (not_executable / wrong_assertion), and
      an assertion that holds is left for the meaningfulness review.
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

    models = models or [config.default_model]
    app_config = config.apps.get(app, {})
    version = (app_config.get("versions") or ["crawled"])[0]

    suite_path = config.get_dante_suite_path(app)
    if not suite_path.exists():
        raise FileNotFoundError(
            f"DANTE-generated suite not found: {suite_path}\n"
            f"Run the crawl first (see docs/dante-setup.md)."
        )

    crawled = ingest_dante_suite(app, suite_path, config.output_dir / "crawled" / app)
    by_class = {t.class_name: t for t in crawled}
    records = sorted(to_test_records(crawled, version),
                     key=lambda r: int(re.sub(r"\D", "", r.method_name)))
    if limit is not None:
        records = records[:limit]

    html_dir = reference_dir(config, app, "crawled") / "html"
    html_treatments = [t for t in treatments if crawled_treatment_ingredients(t).html]
    missing = [r.class_name for r in records if not (html_dir / f"{r.class_name}.html").exists()]
    if (html_treatments or execute) and missing:
        raise MissingContextError(
            f"Treatment(s) {', '.join(html_treatments) or '(execution)'} need the crawled "
            f"reference run, but {len(missing)} test(s) have no captured page:\n  "
            + "\n  ".join(missing)
            + f"\nRun 'bewt-pipeline reference-run --app {app} --source crawled' first."
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
                    # Captured by the reference run; presence checked above.
                    html = (html_dir / f"{record.class_name}.html").read_text()

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

                notes = []
                if valid and has_extra_statements(generated):
                    notes.append("answer contains non-assertion statements")
                if not valid:
                    result.error_category = ErrorCategory.NOT_EXECUTABLE
                    notes.append("answer contains no assertion call")
                    _progress(t, record.class_name, "invalid")
                elif not execute:
                    notes.append("execution skipped")
                    _progress(t, record.class_name, "generated")
                else:
                    filled, fill_note = fill_placeholders(variant_source, generated)
                    if fill_note:
                        notes.append(fill_note)
                    work_dir = config.output_dir / "runs-crawled" / app / model_name / t / record.class_name
                    compile_res, test_res = run_isolated(
                        config, app, record.class_name, filled, work_dir, source="crawled",
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
                    # No gold: an assertion that holds awaits the meaningfulness
                    # review (needs_review); failures are decided by execution.
                    result.error_category = classify_outcome(
                        generated, [], result.compiles, result.passes, error_type,
                    )
                    status = "PASS" if result.passes else "FAIL"
                    _progress(t, record.class_name, f"{status}:cat={result.error_category.value}")
                result.notes = " | ".join(notes)

                experiment_id = store.save_result(result)
                _save_and_emit_llm_call(store, _build_generation_call(
                    record, t, model_name, system, user, llm_response,
                    experiment_id, source="crawled",
                ))
                all_results.append(result)

    store.close()
    flush_observability()
    return all_results
