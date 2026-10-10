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

def print_table(results: list[dict], total_cases: int) -> None:
    """Print a per-case console table and totals from completed cases."""
    completed = [r for r in results if r.get("status") != "error" and r.get("reliability_score") is not None]
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
        if r.get("status") == "error":
            print(
                f"{r['id']:<24} {r['category']:<18} {'--':>6} "
                f"{r['total_planted']:>7} {'--':>6} {'--':>6} "
                f"{r['total_clean']:>5} {'--':>5} {'--':>4} {'ERR':>5}"
            )
        else:
            print(
                f"{r['id']:<24} {r['category']:<18} {r['total_claims']:>6} "
                f"{r['total_planted']:>7} {r['caught']:>6} {r['verdict_matched']:>6} "
                f"{r['total_clean']:>5} {r['false_flags']:>5} "
                f"{r['unverified_quotes']:>4} {r['reliability_score']:>5}"
            )

    print(sep)

    if not completed:
        print(f"\n  No cases completed successfully (0/{total_cases}).\n")
        return

    # Totals from completed cases only
    tot_planted = sum(r["total_planted"] for r in completed)
    tot_caught = sum(r["caught"] for r in completed)
    tot_vmatch = sum(r["verdict_matched"] for r in completed)
    tot_clean = sum(r["total_clean"] for r in completed)
    tot_ff = sum(r["false_flags"] for r in completed)
    tot_unvq = sum(r["unverified_quotes"] for r in completed)
    tot_claims = sum(r["total_claims"] for r in completed)
    avg_claims = tot_claims / len(completed) if completed else 0

    recall = (tot_caught / tot_planted * 100) if tot_planted else 0
    vmatch_rate = (tot_vmatch / tot_planted * 100) if tot_planted else 0
    ff_rate = (tot_ff / tot_clean * 100) if tot_clean else 0

    print(f"\n  Completed cases:          {len(completed)}/{total_cases}")
    print(f"  Planted-error recall:     {tot_caught}/{tot_planted}  ({recall:.1f}%)")
    print(f"  Verdict match rate:       {tot_vmatch}/{tot_planted}  ({vmatch_rate:.1f}%)")
    print(f"  False-flag rate (clean):  {tot_ff}/{tot_clean}  ({ff_rate:.1f}%)")
    print(f"  Unverified quotes total:  {tot_unvq}")
    print(f"  Average claims/case:      {avg_claims:.1f}")
    print()


def save_results_json(results: list[dict], total_cases: int) -> None:
    """Write eval/results.json."""
    completed = [r for r in results if r.get("status") != "error" and r.get("reliability_score") is not None]
    num_completed = len(completed)

    tot_planted = sum(r["total_planted"] for r in completed)
    tot_caught = sum(r["caught"] for r in completed)
    tot_vmatch = sum(r["verdict_matched"] for r in completed)
    tot_clean = sum(r["total_clean"] for r in completed)
    tot_ff = sum(r["false_flags"] for r in completed)
    tot_unvq = sum(r["unverified_quotes"] for r in completed)
    tot_claims = sum(r["total_claims"] for r in completed)

    payload = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_cases_configured": total_cases,
        "cases_evaluated": num_completed,
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
            "avg_claims_per_case": round(tot_claims / num_completed, 1) if num_completed else 0,
        },
        "per_case": results,
    }

    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def save_results_md(results: list[dict], total_cases: int) -> None:
    """Write eval/RESULTS.md from the completed cases only."""
    completed = [r for r in results if r.get("status") != "error" and r.get("reliability_score") is not None]
    error_cases = [r for r in results if r.get("status") == "error"]
    num_completed = len(completed)
    pct_ran = round(num_completed / total_cases * 100, 1) if total_cases else 0

    if not completed:
        lines = [
            "# Evaluation Results",
            "",
            f"**Run:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
            f"**Cases completed:** 0 of {total_cases} total test cases (0.0%)",
            "",
            "No test cases were successfully completed in this run.",
            ""
        ]
        if error_cases:
            lines.append("## Incomplete / Failed Cases")
            lines.append("")
            lines.append("| ID | Category | Status | Error Message |")
            lines.append("|---|---|:---:|---|")
            for err in error_cases:
                lines.append(f"| {err['id']} | {err['category']} | Error | {err.get('error', 'Unknown error')} |")
            lines.append("")

        with open(RESULTS_MD, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    tot_planted = sum(r["total_planted"] for r in completed)
    tot_caught = sum(r["caught"] for r in completed)
    tot_vmatch = sum(r["verdict_matched"] for r in completed)
    tot_clean = sum(r["total_clean"] for r in completed)
    tot_ff = sum(r["false_flags"] for r in completed)
    tot_unvq = sum(r["unverified_quotes"] for r in completed)
    tot_claims = sum(r["total_claims"] for r in completed)
    avg_claims = round(tot_claims / num_completed, 1) if num_completed else 0

    recall = round(tot_caught / tot_planted * 100, 1) if tot_planted else 0
    vmatch_rate = round(tot_vmatch / tot_planted * 100, 1) if tot_planted else 0
    ff_rate = round(tot_ff / tot_clean * 100, 1) if tot_clean else 0

    lines = [
        "# Evaluation Results",
        "",
        f"**Run:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Cases completed:** {num_completed} of {total_cases} total test cases ({pct_ran}%)  ",
        f"*(Metrics computed strictly from completed cases; no numbers invented for unrun cases)*",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Completed cases | {num_completed} / {total_cases} ({pct_ran}%) |",
        f"| Planted-error recall | {tot_caught}/{tot_planted} ({recall}%) |",
        f"| Verdict match rate | {tot_vmatch}/{tot_planted} ({vmatch_rate}%) |",
        f"| False-flag rate (clean spans) | {tot_ff}/{tot_clean} ({ff_rate}%) |",
        f"| Unverified quotes | {tot_unvq} |",
        f"| Avg claims per completed case | {avg_claims} |",
        "",
        "## Per-Case Breakdown (Completed Cases Only)",
        "",
        "| ID | Category | Claims | Planted | Caught | V-Match | Clean | F-Flag | Unv.Q | Score |",
        "|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for r in completed:
        lines.append(
            f"| {r['id']} | {r['category']} | {r['total_claims']} "
            f"| {r['total_planted']} | {r['caught']} | {r['verdict_matched']} "
            f"| {r['total_clean']} | {r['false_flags']} "
            f"| {r['unverified_quotes']} | {r['reliability_score']} |"
        )

    lines.append(
        f"| **Totals ({num_completed}/{total_cases})** | | **{tot_claims}** "
        f"| **{tot_planted}** | **{tot_caught}** | **{tot_vmatch}** "
        f"| **{tot_clean}** | **{tot_ff}** "
        f"| **{tot_unvq}** | |"
    )
    lines.append("")

    if error_cases:
        lines.append("## Incomplete / Failed Cases")
        lines.append("")
        lines.append("| ID | Category | Status | Error Message |")
        lines.append("|---|---|:---:|---|")
        for err in error_cases:
            lines.append(f"| {err['id']} | {err['category']} | Error | {err.get('error', 'Unknown error')} |")
        lines.append("")

    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


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

    # Load previously finished cases from results.json so re-runs skip finished cases
    cached_cases: dict[str, dict] = {}
    if not args.no_cache and RESULTS_JSON.exists():
        try:
            with open(RESULTS_JSON, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
                for item in prev_data.get("per_case", []):
                    if item.get("status") != "error" and item.get("reliability_score") is not None:
                        cached_cases[item["id"]] = item
            if cached_cases:
                print(f"[eval] Loaded {len(cached_cases)} previously finished case(s) from {RESULTS_JSON.name}.\n")
        except Exception:
            pass

    results: list[dict] = []
    finished = 0
    consecutive_errors = 0

    for i, case in enumerate(cases):
        cid = case["id"]
        cat = case["category"]
        print(f"[{i + 1}/{len(cases)}] Running case: {cid} ({cat})")

        # Skip already completed cases if cached
        if cid in cached_cases:
            res = cached_cases[cid]
            results.append(res)
            finished += 1
            consecutive_errors = 0
            caught_info = (
                f"caught {res['caught']}/{res['total_planted']}"
                if res.get("total_planted") else "no planted errors"
            )
            print(f"       ✓ cached (previous run)  |  {caught_info}  |  score {res.get('reliability_score')}")
            save_results_json(results, total_cases=len(cases))
            save_results_md(results, total_cases=len(cases))
            continue

        t0 = time.time()
        try:
            result = evaluate_case(case)
            result["status"] = "success"
            results.append(result)
            finished += 1
            consecutive_errors = 0

            elapsed = time.time() - t0
            was_cached = elapsed < 2.0
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

        except QuotaExhaustedError:
            print(
                f"\n{'=' * 70}\n"
                f"⚠  QuotaExhaustedError: Stopped cleanly after {finished}/{len(cases)} cases finished.\n"
                f"   Finished cases are saved — re-run later to continue.\n"
                f"{'=' * 70}\n"
            )
            save_results_json(results, total_cases=len(cases))
            save_results_md(results, total_cases=len(cases))
            break
        except Exception as e:
            consecutive_errors += 1
            err_msg = f"{type(e).__name__}: {str(e)}"
            short_err = err_msg if len(err_msg) <= 80 else err_msg[:77] + "..."
            print(f"       ✗ error  |  {short_err}")

            err_record = {
                "id": cid,
                "category": cat,
                "status": "error",
                "error": short_err,
                "total_claims": 0,
                "total_planted": len(case.get("planted_errors", [])),
                "caught": 0,
                "verdict_matched": 0,
                "total_clean": len(case.get("clean_spans", [])),
                "false_flags": 0,
                "unverified_quotes": 0,
                "planted_detail": [],
                "clean_detail": [],
                "reliability_score": None,
            }
            results.append(err_record)

            if consecutive_errors >= 3:
                print(
                    f"\n{'=' * 70}\n"
                    f"⚠  Stopped cleanly: 3 consecutive errors encountered.\n"
                    f"   Cases finished successfully: {finished}/{len(cases)}\n"
                    f"{'=' * 70}\n"
                )
                save_results_json(results, total_cases=len(cases))
                save_results_md(results, total_cases=len(cases))
                break

        # Save progress after every case so a re-run skips finished cases
        save_results_json(results, total_cases=len(cases))
        save_results_md(results, total_cases=len(cases))

    if not results:
        print("No cases were evaluated.")
        return

    # Print table & save final summary
    print_table(results, total_cases=len(cases))
    save_results_json(results, total_cases=len(cases))
    save_results_md(results, total_cases=len(cases))
    print(f"Report written to {RESULTS_MD.name} ({finished}/{len(cases)} cases completed).\n")


if __name__ == "__main__":
    main()
