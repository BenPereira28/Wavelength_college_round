# Second Opinion

An AI auditing tool that checks an AI-generated answer against source material. It breaks the answer into claims, finds evidence for each one, and flags hallucinated facts, unsupported claims, logical gaps and unsafe advice, pointing at the exact text that is wrong.

Built for the GDG On Campus "Bit N Build" college round (AI/ML track).

## How it works

1. **Extract claims:** the answer is split into individual factual claims, each located in the original text.
2. **Find evidence:** relevant passages are pulled from the source (the full source if it is short).
3. **Judge:** each claim gets a verdict (`supported`, `contradicted`, `insufficient_evidence`), an error type, a confidence level and an explanation.
4. **Verify quotes:** every evidence quote is checked against the source text in code. A "supported" verdict without a verified quote is downgraded.
5. **Check reasoning:** a separate pass looks for conclusions that do not follow from the evidence and for unsafe advice.
6. **Score:** counts and a reliability score from 0 to 100, with a breakdown of how it was computed.

Each audit makes 3 Gemini requests (steps 1, 3 and 5).

## Setup

```bash
git clone https://github.com/BenPereira28/Wavelength_college_round.git
cd Wavelength_college_round

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
copy .env.example .env         # Mac/Linux: cp .env.example .env
```

Open `.env` and paste your own free Gemini key from https://aistudio.google.com/ . Each person uses their own key. Never commit it or paste it in chat.

Check your setup:

```bash
python test_setup.py
```

Run the server:

```bash
python main.py
```

Then open http://127.0.0.1:8000 (page) or http://127.0.0.1:8000/docs (interactive API docs).

## Free-tier quota warning

The free Gemini tier allows only a small number of requests per day per model (at the time of writing, 20). One audit uses 3. When the quota runs out the API returns an error until it resets. Build the frontend and the evaluation against `sample_response.json` instead of the live API wherever you can.

## Project layout

```
main.py               FastAPI app: GET /, GET /health, POST /audit   (Dev 1)
pipeline.py           the 6-stage audit pipeline                      (Dev 1)
llm_client.py         the only file that talks to Gemini              (Dev 1)
static/               frontend                                        (Dev 2)
eval/                 test cases and eval script                      (Tester)
sample_response.json  the API response format, a real example
test_setup.py         checks packages and key
test_api.py           checks the endpoints
```

## API

`POST /audit` with `{"source": "...", "answer": "..."}` returns the audit result.

`sample_response.json` is a real example of the response. Do not change its shape without telling the whole team, and update the file in the same commit.

Top level: `source_text`, `original_answer`, `claims`, `reasoning`, `summary`.

Each item in `claims`:

| Field | Meaning |
|---|---|
| `claim_id` | 1, 2, 3, in order of appearance |
| `claim_text` | the claim in one sentence |
| `original_text_quote` | the exact words in the answer the claim came from |
| `start_char`, `end_char` | character offsets of that quote in `original_answer` (`end_char` is exclusive). Both are `null` when `located` is `false`; show the claim without a highlight |
| `located` | `false` if the claim could not be matched to text in the answer |
| `verdict` | `supported`, `contradicted` or `insufficient_evidence` |
| `error_type` | `none`, `hallucinated_fact`, `unsupported_claim`, `logical_inconsistency` or `unsafe_recommendation` |
| `evidence_quote` | text quoted from the source, or `""` when there is none |
| `quote_verified` | `true` only if the quote was found in the source by code |
| `quote_char_start`, `quote_char_end` | offsets of the quote in `source_text`, or `null` |
| `explanation` | one or two sentences |
| `confidence` | `high`, `medium` or `low`. Confidence is not proof of correctness |

`reasoning`: `has_reasoning_flaws`, `reasoning_summary`, and `issues` (each with `issue_type`, `description`, `severity` of `low`, `medium` or `high`).

`summary`: `total_claims`, `supported_count`, `contradicted_count`, `insufficient_evidence_count`, `problematic_count`, `unverified_quotes_count`, `reliability_score` (0 to 100), `score_breakdown`, `assessment_text`.

## Score formula

```
score = supported%
        - 15 per contradicted claim
        - 20 per claim with error_type unsafe_recommendation
        - 5 per high-severity reasoning issue (max 15)
clamped to 0-100
```

`summary.score_breakdown` shows each part so the number can be explained.

## Team workflow

- Work only in your own files and folders (see layout above).
- Use your own branch (`backend`, `frontend`, `eval`). Do not push directly to `main`.
- Run `git pull` before you start working.
- Merge through a Pull Request once something works.
- Never commit `.env` or `__pycache__/`.

## Limitations

To be filled in by the team after evaluation: what the tool misses, where confidence is unreliable, and which kinds of errors are hardest to catch.
