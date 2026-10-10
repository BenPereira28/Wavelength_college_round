# Second Opinion

An AI auditing tool that checks an AI-generated answer against source material. It breaks the answer into claims, finds evidence for each one, and flags hallucinated facts, unsupported claims, logical gaps and unsafe advice, pointing at the exact text that is wrong.

Built for the GDG On Campus "Bit N Build" college round (AI/ML track).

## How it works

1. **Extract claims:** the answer is split into individual factual claims.
2. **Find evidence:** relevant passages are pulled from the source.
3. **Judge:** each claim gets a verdict (`supported`, `contradicted`, `insufficient_evidence`), an error type, a confidence level and an explanation.
4. **Verify quotes:** every evidence quote is checked against the source text in code. If the quote is not really in the source, the finding is downgraded.
5. **Check reasoning:** a separate pass looks for conclusions that do not follow from the evidence.
6. **Summarise:** counts and an overall reliability score.

## Setup

```bash
git clone https://github.com/BenPereira28/Wavelength_college_round.git
cd Wavelength_college_round

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
cp .env.example .env           # Windows: copy .env.example .env
```

Open `.env` and paste your own free Gemini key from https://aistudio.google.com/ . Do not share it or commit it.

Check your setup:

```bash
python test_setup.py
```

Run the server (once `app/main.py` exists):

```bash
uvicorn app.main:app --reload
```

## Project layout

```
app/                  backend (Dev 1)
  main.py             FastAPI app, POST /audit
  pipeline/           claim extraction, judging, verification
static/               frontend (Dev 2)
eval/                 test cases and eval script (Tester)
sample_response.json  the agreed API response format
```

## API response format

`sample_response.json` is the contract between backend and frontend. Do not change its shape without telling the whole team, and update the file in the same commit.

Key points:
- `answer_span` gives character offsets (`start` inclusive, `end` exclusive) into the original `answer`, used for highlighting.
- `evidence_span` gives offsets into the original `source`, or `null` when there is no evidence.
- `verdict` is one of `supported`, `contradicted`, `insufficient_evidence`.
- `error_type` is one of `none`, `hallucinated_fact`, `unsupported_claim`, `logical_inconsistency`, `unsafe_recommendation`.
- `confidence` is one of `high`, `medium`, `low`. Confidence is not proof of correctness.

## Team workflow

- Work only in your own folders. Dev 1: `app/`. Dev 2: `static/`. Tester: `eval/`.
- Use your own branch (`backend`, `frontend`, `eval`). Do not push directly to `main`.
- Run `git pull` before you start working.
- Merge through a Pull Request once something works.
- Never commit `.env` or paste API keys in chat.

## Limitations

To be filled in by the team after evaluation: what the tool misses, where confidence is unreliable, and which kinds of errors are hardest to catch.
