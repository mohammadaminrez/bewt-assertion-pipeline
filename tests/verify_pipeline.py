"""Manual pipeline verification: trace a test case through every stage."""

from __future__ import annotations
import json, sys, textwrap
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config
from src.parsing.java_parser import parse_test_file, resolve_strings_constants, extract_assertions, strip_assertions
from src.parsing.gherkin_parser import parse_all_gherkin, match_gherkin_to_test, generate_comment_from_assertion
from src.variants.generator import generate_variant_a, generate_variant_b
from src.llm.prompt_builder import build_prompt_a, build_prompt_b
from src.llm.response_parser import extract_assertion_from_response, validate_assertion
from src.evaluation.comparator import (
    compute_semantic_similarity, check_exact_match, classify_error,
    _normalize, _extract_string_literals, _extract_assertion_types, _set_similarity, _normalized_levenshtein,
)
from src.parsing.assertion_model import ErrorCategory
from src.data.store import ResultStore

W = 90
SEP = "=" * W
THIN = "-" * W

def header(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def sub(title):
    print(f"\n{THIN}")
    print(f"  {title}")
    print(THIN)

def show(label, value, indent=4):
    prefix = " " * indent
    if isinstance(value, str) and "\n" in value:
        print(f"{prefix}{label}:")
        for line in value.split("\n"):
            print(f"{prefix}  | {line}")
    else:
        print(f"{prefix}{label}: {value}")

def trace_test(config, app_name, test_file_name):
    """Trace a single test through every pipeline stage."""

    header(f"TRACING: {app_name} / {test_file_name}")

    # ── STAGE 1: Read the original Java file ──
    sub("STAGE 1: Original Java Source")
    app_config = config.apps[app_name]
    version = app_config["versions"][0]
    test_dir = config.get_app_test_path(app_name)
    test_path = test_dir / test_file_name

    original_source = test_path.read_text()
    print(f"    File: {test_path}")
    print(f"    Lines: {len(original_source.splitlines())}")

    # ── STAGE 2: Parse assertions ──
    sub("STAGE 2: Extract Assertions")
    strings_path = config.get_app_project_path(app_name) / "src" / "main" / "java" / "utils" / "Strings.java"
    constants = resolve_strings_constants(strings_path)

    record = parse_test_file(test_path, app_name, app_config["variant"], version, constants)
    print(f"    Class: {record.class_name}")
    print(f"    Method: {record.method_name}")
    print(f"    Assertion count: {record.assertion_count}")
    for i, a in enumerate(record.assertions):
        print(f"\n    Assertion #{i+1}:")
        show("type", a.assertion_type.value)
        show("full_text", a.full_text)
        show("expected_value", a.expected_value)
        show("actual_expression", a.actual_expression)
        show("resolved_expected", a.resolved_expected)
        show("lines", f"{a.start_line}-{a.end_line}")

    # ── STAGE 3: Strip assertions ──
    sub("STAGE 3: Stripped Source (assertion removed)")
    stripped = record.stripped_source
    # Show only the relevant lines around the placeholder
    for i, line in enumerate(stripped.splitlines()):
        if "TODO" in line or (i > 0 and "TODO" in stripped.splitlines()[max(0,i-1)]):
            ctx_start = max(0, i - 3)
            ctx_end = min(len(stripped.splitlines()), i + 3)
            for j in range(ctx_start, ctx_end):
                marker = " >>>" if "TODO" in stripped.splitlines()[j] else "    "
                print(f"  {marker} L{j+1}: {stripped.splitlines()[j]}")
            break

    # ── STAGE 4: Gherkin matching ──
    sub("STAGE 4: Gherkin Match")
    gherkin_dir = config.get_gherkin_path(app_name)
    gherkin_map = parse_all_gherkin(gherkin_dir)
    scenario = match_gherkin_to_test(record.class_name, gherkin_map)

    if scenario:
        show("matched feature", scenario.feature_file)
        show("scenario name", scenario.scenario_name)
        show("Then-clauses", "\n".join(scenario.then_clauses))
        show("generated comment", scenario.descriptive_comment)
    else:
        print("    No Gherkin match found.")
        print("    Fallback comments from gold-standard assertions:")
        for a in record.assertions:
            comment = generate_comment_from_assertion(a.full_text)
            show("fallback", comment)

    # ── STAGE 5: Variant generation ──
    sub("STAGE 5: Variant A (no comment)")
    variant_a = generate_variant_a(record)
    # Just show placeholder area
    for i, line in enumerate(variant_a.splitlines()):
        if "TODO" in line:
            ctx_start = max(0, i - 2)
            ctx_end = min(len(variant_a.splitlines()), i + 2)
            for j in range(ctx_start, ctx_end):
                marker = " >>>" if "TODO" in variant_a.splitlines()[j] else "    "
                print(f"  {marker} L{j+1}: {variant_a.splitlines()[j]}")
            break

    sub("STAGE 5: Variant B (with comment)")
    variant_b = generate_variant_b(record, gherkin_map)
    for i, line in enumerate(variant_b.splitlines()):
        if "TODO" in line or "// Assert" in line:
            ctx_start = max(0, i - 2)
            ctx_end = min(len(variant_b.splitlines()), i + 3)
            for j in range(ctx_start, ctx_end):
                ln = variant_b.splitlines()[j]
                marker = " >>>" if ("TODO" in ln or "// Assert" in ln) else "    "
                print(f"  {marker} L{j+1}: {ln}")
            break

    # ── STAGE 6: Check cached LLM responses ──
    sub("STAGE 6: LLM Responses (from DB)")
    store = ResultStore(config.output_dir / "results.db")
    for treatment in ["A", "B"]:
        rows = store.conn.execute(
            "SELECT treatment, raw_response, generated_assertion, gold_standard, "
            "exact_match, semantic_similarity, error_category "
            "FROM experiments WHERE app=? AND class_name=? AND treatment=?",
            (app_name, record.class_name, treatment),
        ).fetchall()

        if not rows:
            print(f"    Treatment {treatment}: no stored result")
            continue

        row = rows[0]
        print(f"\n    Treatment {treatment}:")
        show("raw_response (first 300 chars)", row[1][:300] if row[1] else "(empty)")

        # ── STAGE 7: Response parsing ──
        sub(f"STAGE 7: Response Parsing — Treatment {treatment}")
        re_extracted = extract_assertion_from_response(row[1])
        re_valid = validate_assertion(re_extracted)
        show("extracted assertion", re_extracted)
        show("valid", re_valid)
        show("stored assertion (from DB)", row[2])
        match = re_extracted.strip() == row[2].strip() if row[2] else False
        show("extraction matches DB", match)

        # ── STAGE 8: Evaluation — decomposed ──
        sub(f"STAGE 8: Evaluation — Treatment {treatment}")

        generated = row[2]
        gold_text = row[3]
        show("GENERATED", generated)
        show("GOLD", gold_text)

        # Exact match
        exact = check_exact_match(generated, record.assertions)
        show("exact_match", f"{exact}  (DB says: {bool(row[4])})")

        # Semantic similarity — decomposed
        gen_types = _extract_assertion_types(generated)
        gold_types = [a.assertion_type for a in record.assertions]
        type_score = _set_similarity(
            {t.value for t in gen_types}, {t.value for t in gold_types}
        )
        show("type_match (weight 0.2)", f"{type_score:.3f} → {type_score * 0.2:.3f}")

        gen_expected = _extract_string_literals(generated)
        gold_text_combined = "\n".join(a.full_text for a in record.assertions)
        gold_expected = _extract_string_literals(gold_text_combined)
        if gold_expected or gen_expected:
            exp_score = _set_similarity(gen_expected, gold_expected)
        else:
            exp_score = 1.0
        show("gen string literals", gen_expected)
        show("gold string literals", gold_expected)
        show("expected_val_match (weight 0.4)", f"{exp_score:.3f} → {exp_score * 0.4:.3f}")

        norm_gen = _normalize(generated)
        norm_gold = _normalize(gold_text_combined)
        text_score = _normalized_levenshtein(norm_gen, norm_gold)
        show("text_sim (weight 0.4)", f"{text_score:.3f} → {text_score * 0.4:.3f}")

        total = type_score * 0.2 + exp_score * 0.4 + text_score * 0.4
        show("TOTAL semantic_similarity", f"{total:.3f}  (DB says: {row[5]:.3f})")

        # Error category
        new_cat = classify_error(generated, record.assertions, compiles=False, passes=False)
        show("error_category (recomputed)", f"{new_cat.value}  (DB says: {row[6]})")

    store.close()


def main():
    config = Config()

    # Representative test cases to trace:
    cases = [
        # 1. Simple exact match (both A and B match)
        ("expresscart", "AddEmptyReviewTest.java"),
        # 2. assertFalse — had Bug 1 (sim was 0.6 for exact match)
        ("expresscart", "DeleteDiscountCodeAmountTest.java"),
        # 3. Swapped arg order — had Bug 2
        ("expresscart", "AddReviewTest.java"),
        # 4. Big improvement from A→B (comment helped a lot)
        ("expresscart", "LoginDeletedUserFailsTest.java"),
        # 5. Regression case (A was better than B)
        ("expresscart", "AddEmptyUserTest.java"),
    ]

    for app, test_file in cases:
        trace_test(config, app, test_file)

    print(f"\n{'=' * W}")
    print("  VERIFICATION COMPLETE — review each stage above for correctness")
    print(f"{'=' * W}")


if __name__ == "__main__":
    main()
