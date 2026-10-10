#!/usr/bin/env python3
"""
run_eval.py
--------------------------------------------------------------------------------
Evaluation harness for the Second Opinion audit pipeline.

Runs curated test cases from cases.json through the pipeline and measures:
  - Recall of planted errors (did the pipeline catch them?)
  - Verdict match rate (did it assign the expected verdict?)
  - False-flag rate on known-clean spans
  - Count of unverified quotes
  - Average claims per case

Usage:
    python -m eval.run_eval                   # run all cases
    python -m eval.run_eval --limit 3         # first 3 cases
    python -m eval.run_eval --case wrong_fact_01  # single case
    python -m eval.run_eval --no-cache        # disable LLM cache
--------------------------------------------------------------------------------
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup – allow running as `python -m eval.run_eval` from the project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVAL_DIR = Path(__file__).resolve().parent
CASES_FILE = EVAL_DIR / "cases.json"
RESULTS_JSON = EVAL_DIR / "results.json"
RESULTS_MD = EVAL_DIR / "RESULTS.md"


# ===========================================================================
# 1. VALIDATION: every planted_error.text and clean_span must be an exact
#    substring of its case's answer.  Exit immediately if not.
# ===========================================================================

def validate_cases(cases: list[dict]) -> None:
    """Abort with a clear error if any planted/clean text isn't in its answer."""
    errors: list[str] = []

    for case in cases:
        cid = case["id"]
        answer = case["answer"]

        for i, pe in enumerate(case.get("planted_errors", [])):
            if pe["text"] not in answer:
                errors.append(
                    f'Case "{cid}" planted_errors[{i}]: '
                    f'text "{pe["text"]}" is NOT a substring of the answer.'
                )

        for i, cs in enumerate(case.get("clean_spans", [])):
            if cs not in answer:
                errors.append(
                    f'Case "{cid}" clean_spans[{i}]: '
                    f'text "{cs}" is NOT a substring of the answer.'
                )

    if errors:
        print("=" * 70, file=sys.stderr)
        print("VALIDATION FAILED – substring mismatches:", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print(
            "\nFix cases.json so every planted_errors[].text and "
            "clean_spans[] value is an exact substring of its answer.",
            file=sys.stderr,
        )
        sys.exit(1)


# ===========================================================================
# 2. OVERLAP HELPERS
# ===========================================================================

def _span_of_substring(answer: str, text: str) -> tuple[int, int]:
    """Return (start, end) char offsets of *text* inside *answer*."""
    idx = answer.find(text)
    assert idx != -1, f"Substring not found (should have been validated): {text!r}"
    return idx, idx + len(text)


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True when [a_start, a_end) and [b_start, b_end) share at least one char."""
    return a_start < b_end and b_start < a_end


# ===========================================================================
# 3. SINGLE-CASE EVALUATION
# ===========================================================================

def evaluate_case(case: dict) -> dict:
    """Run the audit pipeline on one case and score it."""
    from pipeline import run_audit  # deferred import so validation runs fast

    result = run_audit(source_text=case["source"], ai_answer=case["answer"])
    answer = case["answer"]
    claims = result.claims

    # --- Planted errors ---------------------------------------------------
    planted_results = []
    for pe in case.get("planted_errors", []):
        pe_start, pe_end = _span_of_substring(answer, pe["text"])
        caught = False
        verdict_matched = False

        for claim in claims:
            if claim.start_char is None or claim.end_char is None:
                continue
            if _ranges_overlap(claim.start_char, claim.end_char, pe_start, pe_end):
                if claim.verdict != "supported":
                    caught = True
                    if claim.verdict == pe["expected_verdict"]:
                        verdict_matched = True

        planted_results.append({
            "text": pe["text"],
            "expected_verdict": pe["expected_verdict"],
            "expected_error_type": pe["expected_error_type"],
            "caught": caught,
            "verdict_matched": verdict_matched,
        })

    # --- Clean spans ------------------------------------------------------
    clean_results = []
    for cs in case.get("clean_spans", []):
        cs_start, cs_end = _span_of_substring(answer, cs)
        falsely_flagged = False

        for claim in claims:
            if claim.start_char is None or claim.end_char is None:
                continue
            if _ranges_overlap(claim.start_char, claim.end_char, cs_start, cs_end):
                if claim.verdict != "supported":
                    falsely_flagged = True
                    break

        clean_results.append({
            "text": cs,
            "falsely_flagged": falsely_flagged,
        })

    # --- Aggregate metrics ------------------------------------------------
    total_planted = len(planted_results)
    caught_count = sum(1 for p in planted_results if p["caught"])
    verdict_match_count = sum(1 for p in planted_results if p["verdict_matched"])
    total_clean = len(clean_results)
    false_flag_count = sum(1 for c in clean_results if c["falsely_flagged"])

    unverified_quotes = sum(
        1 for c in claims if c.evidence_quote and not c.quote_verified
    )

    return {
        "id": case["id"],
        "category": case["category"],
        "total_claims": len(claims),
        "total_planted": total_planted,
        "caught": caught_count,
        "verdict_matched": verdict_match_count,
        "total_clean": total_clean,
        "false_flags": false_flag_count,
        "unverified_quotes": unverified_quotes,
        "planted_detail": planted_results,
        "clean_detail": clean_results,
        "reliability_score": result.summary.reliability_score,
    }


# ===========================================================================
# 4. PRINTING & SERIALISATION
# ===========================================================================

def print_table(results: list[dict]) -> None:
    """Print a per-case console table and totals."""
    header = (
        f"{'ID':<24} {'Cat':<18} {'Claims':>6} "
        f"{'Planted':>7} {'Caught':>6} {'VMatch':>6} "
        f"{'Clean':>5} {'FFlag':>5} {'UnvQ':>4} {'Score':>5}"
    )
    sep = "-" * len(header)

    print("\n" + sep)
    print(header)
    print(sep)

    for r in results:
        print(
            f"{r['id']:<24} {r['category']:<18} {r['total_claims']:>6} "
            f"{r['total_planted']:>7} {r['caught']:>6} {r['verdict_matched']:>6} "
            f"{r['total_clean']:>5} {r['false_flags']:>5} "
            f"{r['unverified_quotes']:>4} {r['reliability_score']:>5}"
        )

    print(sep)

    # Totals
    tot_planted = sum(r["total_planted"] for r in results)
    tot_caught = sum(r["caught"] for r in results)
    tot_vmatch = sum(r["verdict_matched"] for r in results)
    tot_clean = sum(r["total_clean"] for r in results)
    tot_ff = sum(r["false_flags"] for r in results)
    tot_unvq = sum(r["unverified_quotes"] for r in results)
    tot_claims = sum(r["total_claims"] for r in results)
    avg_claims = tot_claims / len(results) if results else 0

    recall = (tot_caught / tot_planted * 100) if tot_planted else 0
    vmatch_rate = (tot_vmatch / tot_planted * 100) if tot_planted else 0
    ff_rate = (tot_ff / tot_clean * 100) if tot_clean else 0

    print(f"\n  Planted-error recall:     {tot_caught}/{tot_planted}  ({recall:.1f}%)")
    print(f"  Verdict match rate:       {tot_vmatch}/{tot_planted}  ({vmatch_rate:.1f}%)")
    print(f"  False-flag rate (clean):  {tot_ff}/{tot_clean}  ({ff_rate:.1f}%)")
    print(f"  Unverified quotes total:  {tot_unvq}")
    print(f"  Average claims/case:      {avg_claims:.1f}")
    print()


def save_results_json(results: list[dict]) -> None:
    """Write eval/results.json."""
    tot_planted = sum(r["total_planted"] for r in results)
    tot_caught = sum(r["caught"] for r in results)
    tot_vmatch = sum(r["verdict_matched"] for r in results)
    tot_clean = sum(r["total_clean"] for r in results)
    tot_ff = sum(r["false_flags"] for r in results)
    tot_unvq = sum(r["unverified_quotes"] for r in results)
    tot_claims = sum(r["total_claims"] for r in results)

    payload = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "cases_evaluated": len(results),
        "totals": {
            "total_planted": tot_planted,
            "caught": tot_caught,
            "recall_pct": round(tot_caught / tot_planted * 100, 1) if tot_planted else 0,
            "verdict_matched": tot_vmatch,
            "verdict_match_pct": round(tot_vmatch / tot_planted * 100, 1) if tot_planted else 0,
            "total_clean": tot_clean,
            "false_flags": tot_ff,
            "false_flag_pct": round(tot_ff / tot_clean * 100, 1) if tot_clean else 0,
            "unverified_quotes": tot_unvq,
            "avg_claims_per_case": round(tot_claims / len(results), 1) if results else 0,
        },
        "per_case": results,
    }

    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"  → Saved {RESULTS_JSON}")


def save_results_md(results: list[dict]) -> None:
    """Write eval/RESULTS.md as a markdown table."""
    tot_planted = sum(r["total_planted"] for r in results)
    tot_caught = sum(r["caught"] for r in results)
    tot_vmatch = sum(r["verdict_matched"] for r in results)
    tot_clean = sum(r["total_clean"] for r in results)
    tot_ff = sum(r["false_flags"] for r in results)
    tot_unvq = sum(r["unverified_quotes"] for r in results)
    tot_claims = sum(r["total_claims"] for r in results)
    avg_claims = round(tot_claims / len(results), 1) if results else 0

    recall = round(tot_caught / tot_planted * 100, 1) if tot_planted else 0
    vmatch_rate = round(tot_vmatch / tot_planted * 100, 1) if tot_planted else 0
    ff_rate = round(tot_ff / tot_clean * 100, 1) if tot_clean else 0

    lines = [
        f"# Evaluation Results",
        f"",
        f"**Run:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Cases evaluated:** {len(results)}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Planted-error recall | {tot_caught}/{tot_planted} ({recall}%) |",
        f"| Verdict match rate | {tot_vmatch}/{tot_planted} ({vmatch_rate}%) |",
        f"| False-flag rate (clean spans) | {tot_ff}/{tot_clean} ({ff_rate}%) |",
        f"| Unverified quotes | {tot_unvq} |",
        f"| Avg claims per case | {avg_claims} |",
        f"",
        f"## Per-Case Breakdown",
        f"",
        f"| ID | Category | Claims | Planted | Caught | V-Match | Clean | F-Flag | Unv.Q | Score |",
        f"|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for r in results:
        lines.append(
            f"| {r['id']} | {r['category']} | {r['total_claims']} "
            f"| {r['total_planted']} | {r['caught']} | {r['verdict_matched']} "
            f"| {r['total_clean']} | {r['false_flags']} "
            f"| {r['unverified_quotes']} | {r['reliability_score']} |"
        )

    lines.append(f"| **Totals** | | **{tot_claims}** "
                 f"| **{tot_planted}** | **{tot_caught}** | **{tot_vmatch}** "
                 f"| **{tot_clean}** | **{tot_ff}** "
                 f"| **{tot_unvq}** | |")
    lines.append("")

    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  → Saved {RESULTS_MD}")


# ===========================================================================
# 5. MAIN
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run evaluation suite for the Second Opinion audit pipeline."
    )
    parser.add_argument(
        "--limit", "-n", type=int, default=None,
        help="Run only the first N cases.",
    )
    parser.add_argument(
        "--case", type=str, default=None,
        help="Run a single case by ID.",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable LLM disk cache (sets LLM_CACHE=off).",
    )
    args = parser.parse_args()

    # --no-cache: set env var before importing pipeline / llm_client
    if args.no_cache:
        os.environ["LLM_CACHE"] = "off"
        print("[eval] LLM cache DISABLED (--no-cache)")

    # Load cases
    with open(CASES_FILE, "r", encoding="utf-8") as f:
        all_cases: list[dict] = json.load(f)

    # Filter
    if args.case:
        cases = [c for c in all_cases if c["id"] == args.case]
        if not cases:
            print(f"Error: no case with id '{args.case}' found.", file=sys.stderr)
            sys.exit(1)
    elif args.limit:
        cases = all_cases[: args.limit]
    else:
        cases = all_cases

    # Validate all substrings BEFORE any LLM calls
    validate_cases(cases)
    print(f"[eval] Validated {len(cases)} case(s) — all substrings OK.\n")

    # Import pipeline (and therefore llm_client) after env-var setup
    # This also triggers load_dotenv() inside llm_client
    from llm_client import QuotaExhaustedError

    # Detect whether a case was a cache hit by checking if the cache dir
    # contains the relevant hashes.  A simpler proxy: measure wall-clock time.
    # If the pipeline returns in < 1s, it was almost certainly cached.
    results: list[dict] = []
    finished = 0

    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] Running case: {case['id']} ({case['category']})")
        t0 = time.time()
        try:
            result = evaluate_case(case)
        except QuotaExhaustedError:
            print(
                f"\n{'=' * 70}\n"
                f"⚠  QuotaExhaustedError after {finished}/{len(cases)} cases.\n"
                f"   Finished cases are cached — re-run later to continue.\n"
                f"{'=' * 70}\n"
            )
            break
        elapsed = time.time() - t0
        was_cached = elapsed < 2.0
        results.append(result)
        finished += 1

        status = "✓ cached" if was_cached else f"✓ {elapsed:.1f}s"
        caught_info = (
            f"caught {result['caught']}/{result['total_planted']}"
            if result["total_planted"] else "no planted errors"
        )
        print(f"       {status}  |  {caught_info}  |  score {result['reliability_score']}")

        # Rate-limit: wait 3 s between non-cached calls (skip after last case)
        if not was_cached and i < len(cases) - 1:
            print("       (waiting 3 s …)")
            time.sleep(3)

    if not results:
        print("No cases were evaluated.")
        return

    # Print table & save
    print_table(results)
    save_results_json(results)
    save_results_md(results)
    print("Done.\n")


if __name__ == "__main__":
    main()
