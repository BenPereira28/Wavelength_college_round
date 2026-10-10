# Evaluation Suite for Second Opinion

This folder contains a curated test suite and automated harness for measuring
how well the audit pipeline catches factual errors, unsupported claims, unsafe
recommendations, and logical leaps — while **not** falsely flagging correct
content.

## Files

| File | Purpose |
|---|---|
| `cases.json` | 15 hand-authored test cases with planted errors and clean spans |
| `run_eval.py` | Evaluation harness — runs the pipeline, scores results, writes reports |
| `results.json` | Machine-readable output (generated after a run) |
| `RESULTS.md` | Human-readable markdown table (generated after a run) |

## Quick Start

From the **project root** (not from inside `eval/`):

```bash
# Run the full suite (15 cases)
python -m eval.run_eval

# Run only the first 3 cases
python -m eval.run_eval --limit 3

# Run a single case by ID
python -m eval.run_eval --case wrong_fact_01

# Disable the LLM disk cache (forces fresh API calls)
python -m eval.run_eval --no-cache
```

> **Note:** The first run will make real LLM API calls and populate the disk
> cache (`.llm_cache/`). Subsequent runs reuse cached responses and finish in
> seconds. Use `--no-cache` to force fresh calls.

## What the Numbers Mean

The console output and `RESULTS.md` show a table with these columns:

| Column | Meaning |
|---|---|
| **Claims** | Total atomic claims the pipeline extracted from the answer |
| **Planted** | Number of deliberate errors seeded into this case |
| **Caught** | How many planted errors were flagged (verdict ≠ "supported") by at least one overlapping claim |
| **V-Match** | How many caught errors received the *exact* expected verdict ("contradicted" vs "insufficient_evidence") |
| **Clean** | Number of known-correct spans in the answer |
| **F-Flag** | How many clean spans were *incorrectly* flagged as problematic |
| **Unv.Q** | Claims where the pipeline's evidence quote was *not* found verbatim in the source |
| **Score** | The pipeline's own 0–100 reliability score for that answer |

### Summary Metrics

| Metric | Ideal | What it tells you |
|---|---|---|
| **Planted-error recall** | 100% | Fraction of seeded errors the pipeline caught |
| **Verdict match rate** | 100% | Of caught errors, fraction with the exact expected verdict |
| **False-flag rate** | 0% | Fraction of correct spans wrongly marked problematic |
| **Unverified quotes** | 0 | Indicates the LLM hallucinated evidence citations |
| **Avg claims/case** | varies | How finely the pipeline decomposes answers |

### Test-Case Categories

| Category | Count | What's being tested |
|---|---|---|
| `wrong_fact` | 5 | Incorrect numbers, dates, or named entities |
| `unsupported_claim` | 3 | Claims that are true in reality but absent from the source |
| `unsafe_recommendation` | 2 | Dangerous advice contradicted by the source |
| `logical_leap` | 2 | Conclusions that wildly overreach the evidence |
| `correct` | 3 | Fully faithful answers (should trigger zero flags) |

## Quota Handling

If your Gemini API key hits its daily rate limit, the script stops cleanly,
prints how many cases finished, and reminds you that finished cases are cached.
Simply re-run the same command later to pick up where you left off.
