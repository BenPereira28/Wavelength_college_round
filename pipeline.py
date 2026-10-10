"""
pipeline.py
--------------------------------------------------------------------------------
Core Audit Pipeline for Second Opinion.

This script executes the complete 6-stage auditing process:
  1. Claim Extraction: Breaks the AI answer into atomic factual claims with
     substring positions.
  2. Evidence Retrieval: For short sources uses full text; for long sources
     retrieves the most relevant chunks per claim.
  3. Claim Judging: The LLM evaluates each claim against the source evidence.
  4. Programmatic Verification: Strictly checks whether LLM-quoted evidence
     actually exists verbatim in the source (never blindly trusts the LLM).
  5. Reasoning Check: A separate pass checking logical soundness and safety.
  6. Summary & Scoring: Aggregates verdicts into a transparent 0-100 reliability score.

Plain English explanations are included above every class and function.
--------------------------------------------------------------------------------
"""

import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

from llm_client import call_llm_json


# ==============================================================================
# PYDANTIC DATA MODELS
# These guarantee that our data has the exact types and fields we expect.
# ==============================================================================

class ExtractedClaim(BaseModel):
    """Represents a single atomic claim extracted from the AI answer."""
    claim_id: int
    claim_text: str = Field(description="A single atomic factual statement.")
    original_text_quote: str = Field(
        description="The exact substring from the AI answer this claim was derived from."
    )
    start_char: Optional[int] = None
    end_char: Optional[int] = None


class ClaimJudgment(BaseModel):
    """The judgment and verification result for one specific claim."""
    claim_id: int
    claim_text: str
    original_text_quote: str
    start_char: int
    end_char: int
    verdict: Literal["supported", "contradicted", "insufficient_evidence"]
    error_type: Literal[
        "none",
        "hallucinated_fact",
        "unsupported_claim",
        "logical_inconsistency",
        "unsafe_recommendation"
    ]
    evidence_quote: str = ""
    quote_verified: bool = False
    quote_char_start: Optional[int] = None
    quote_char_end: Optional[int] = None
    explanation: str
    confidence: Literal["high", "medium", "low"]


class ReasoningIssue(BaseModel):
    """A logical flaw, extrapolation, or unsafe leap detected in the answer."""
    issue_type: str
    description: str
    severity: Literal["low", "medium", "high"]


class ReasoningReport(BaseModel):
    """The outcome of the reasoning verification pass."""
    has_reasoning_flaws: bool
    reasoning_summary: str
    issues: List[ReasoningIssue] = []


class AuditSummary(BaseModel):
    """High-level metrics and reliability score for the audit."""
    total_claims: int
    supported_count: int
    contradicted_count: int
    insufficient_evidence_count: int
    problematic_count: int
    unverified_quotes_count: int
    reliability_score: int  # 0 to 100
    assessment_text: str


class AuditResult(BaseModel):
    """The comprehensive audit report returned to users or frontend."""
    source_text: str
    original_answer: str
    claims: List[ClaimJudgment]
    reasoning: ReasoningReport
    summary: AuditSummary


# ==============================================================================
# HELPER FUNCTIONS: SUBSTRING SEARCH & EVIDENCE RETRIEVAL
# ==============================================================================

def find_substring_span(source: str, target: str) -> tuple[int, int]:
    """
    Finds the exact start and end character offsets of `target` in `source`.
    If exact match fails, attempts a case-insensitive and whitespace-flexible match.
    Returns (start, end) or (-1, -1) if not found.
    """
    if not target or not source:
        return -1, -1

    # 1. Exact match attempt
    idx = source.find(target)
    if idx != -1:
        return idx, idx + len(target)

    # 2. Case-insensitive attempt
    idx = source.lower().find(target.lower())
    if idx != -1:
        return idx, idx + len(target)

    # 3. Normalized whitespace attempt
    cleaned_target = " ".join(target.split())
    cleaned_source = " ".join(source.split())
    if cleaned_target in cleaned_source:
        # Find approximate match location using regex pattern with flexible spaces
        escaped_words = [re.escape(w) for w in cleaned_target.split()]
        pattern = r"\s+".join(escaped_words)
        match = re.search(pattern, source, re.IGNORECASE)
        if match:
            return match.start(), match.end()

    return -1, -1


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> List[str]:
    """
    Splits text into manageable chunks with overlap for long source documents.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks = []
    current_chunk = ""

    for p in paragraphs:
        if len(current_chunk) + len(p) < chunk_size:
            current_chunk += ("\n" + p) if current_chunk else p
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = p

    if current_chunk:
        chunks.append(current_chunk)

    return chunks if chunks else [text]


def retrieve_evidence_for_claims(source: str, claims: List[ExtractedClaim]) -> dict[int, str]:
    """
    Step 2: Evidence Retrieval.
    - If the source is short (<= 1500 chars), return the entire source for all claims.
    - If the source is long, split into chunks and select the top relevant chunks
      using keyword overlap.
    """
    evidence_map = {}

    # For short sources, the entire source is passed directly
    if len(source) <= 1500:
        for claim in claims:
            evidence_map[claim.claim_id] = source
        return evidence_map

    # For long sources, chunk and rank
    chunks = chunk_text(source)
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "in", "on", "at", "to", "for", "of", "with"}

    for claim in claims:
        # Extract meaningful keywords from claim
        words = set(re.findall(r"\w+", claim.claim_text.lower())) - stopwords
        if not words:
            evidence_map[claim.claim_id] = source[:1500]
            continue

        scored_chunks = []
        for ch in chunks:
            ch_lower = ch.lower()
            score = sum(1 for w in words if w in ch_lower)
            scored_chunks.append((score, ch))

        # Sort by score descending and take top 2 chunks
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        top_chunks = [ch for score, ch in scored_chunks[:2] if score > 0]

        if not top_chunks:
            # Fallback to the first chunk if no keyword overlap
            evidence_map[claim.claim_id] = chunks[0]
        else:
            evidence_map[claim.claim_id] = "\n\n---\n\n".join(top_chunks)

    return evidence_map


# ==============================================================================
# PIPELINE STEP IMPLEMENTATIONS
# ==============================================================================

def step_1_extract_claims(ai_answer: str) -> List[ExtractedClaim]:
    """
    Stage 1: Claim Extraction.
    Sends the AI answer to the LLM and extracts atomic factual claims.
    Each claim must include the exact substring quote from the answer.
    """
    prompt = f"""You are an expert fact-checking parser.
Analyze the following AI-generated text and extract all distinct, atomic factual statements or claims.

Rules:
1. Each claim must be an atomic factual claim (one single verifiable fact).
2. "original_text_quote" MUST be an EXACT substring from the AI answer text below.
3. Keep claims in the order they appear.

AI-GENERATED ANSWER:
\"\"\"{ai_answer}\"\"\"

Respond with a JSON array of objects with keys:
- "claim_id": integer (1, 2, 3...)
- "claim_text": string (the atomic factual statement)
- "original_text_quote": string (verbatim substring from the AI answer)
"""

    raw_data = call_llm_json(
        prompt=prompt,
        system_instruction="You extract atomic factual claims from text into structured JSON arrays."
    )

    claims = []
    # If LLM wrapped it in a dict like {"claims": [...]}, unwrap it
    items = raw_data.get("claims", raw_data) if isinstance(raw_data, dict) else raw_data

    for item in items:
        claim_id = item.get("claim_id", len(claims) + 1)
        claim_text = item.get("claim_text", "").strip()
        quote = item.get("original_text_quote", "").strip()

        # Locate substring in original answer
        start, end = find_substring_span(ai_answer, quote)
        if start == -1:
            # Fallback: try finding claim_text in ai_answer
            start, end = find_substring_span(ai_answer, claim_text)
            if start != -1:
                quote = ai_answer[start:end]
            else:
                start, end = 0, len(ai_answer)
                quote = ai_answer

        claims.append(ExtractedClaim(
            claim_id=claim_id,
            claim_text=claim_text,
            original_text_quote=quote,
            start_char=start,
            end_char=end
        ))

    return claims


def step_3_judge_claims(claims: List[ExtractedClaim], evidence_map: dict[int, str]) -> List[dict]:
    """
    Stage 3: Claim Judging.
    Evaluates each claim against its retrieved evidence text.
    Returns raw judgments from the LLM.
    """
    # Build prompt with each claim and its specific evidence
    claims_payload = []
    for c in claims:
        claims_payload.append({
            "claim_id": c.claim_id,
            "claim_text": c.claim_text,
            "evidence_context": evidence_map.get(c.claim_id, "")
        })

    prompt = f"""You are an objective auditor evaluating AI claims against provided source material.
For each claim, carefully evaluate it against the provided evidence_context.

Evaluation criteria:
- "verdict":
    * "supported": The claim is directly confirmed and backed by the evidence.
    * "contradicted": The claim directly conflicts with or denies what the evidence says.
    * "insufficient_evidence": The evidence does not provide enough information to verify the claim, or the claim is not mentioned.

- "error_type":
    * "none": If the verdict is "supported".
    * "hallucinated_fact": If the claim asserts specific false facts contradicted by or fabricated beyond source facts.
    * "unsupported_claim": If the claim is unmentioned or lacks backing in the evidence.
    * "logical_inconsistency": If the statement contradicts itself or misinterprets the evidence.
    * "unsafe_recommendation": If the statement recommends an unsafe, harmful, or unwarranted action not backed by the evidence.

- "evidence_quote":
    * MUST BE A DIRECT, VERBATIM SUBSTRING QUOTE from the evidence_context.
    * If verdict is "insufficient_evidence" and nothing relevant exists, use an empty string "".

- "explanation":
    * One or two concise sentences explaining your decision.

- "confidence":
    * "high", "medium", or "low" based on how clear-cut the source text is.

CLAIMS TO EVALUATE:
{claims_payload}

Respond with a JSON array of objects with keys:
- "claim_id": integer
- "verdict": "supported" | "contradicted" | "insufficient_evidence"
- "error_type": "none" | "hallucinated_fact" | "unsupported_claim" | "logical_inconsistency" | "unsafe_recommendation"
- "evidence_quote": string (verbatim quote from evidence, or "")
- "explanation": string
- "confidence": "high" | "medium" | "low"
"""

    raw_judgments = call_llm_json(
        prompt=prompt,
        system_instruction="You are an uncompromising factual auditor. Never assume; verify strictly against provided text."
    )

    items = raw_judgments.get("judgments", raw_judgments) if isinstance(raw_judgments, dict) else raw_judgments
    return items


def step_4_verify_judgments(
    claims: List[ExtractedClaim],
    raw_judgments: List[dict],
    source_text: str
) -> List[ClaimJudgment]:
    """
    Stage 4: Programmatic Verification Step.
    DO NOT TRUST THE LLM BLINDLY:
    Programmatically verify whether every `evidence_quote` cited by the LLM
    actually appears verbatim in the source material.
    If the LLM claimed a fact was "supported" based on a hallucinated citation,
    we downgrade the verdict to "insufficient_evidence" and flag it.
    """
    judgment_map = {j.get("claim_id"): j for j in raw_judgments if isinstance(j, dict)}
    verified_claims: List[ClaimJudgment] = []

    for claim in claims:
        j = judgment_map.get(claim.claim_id, {})
        verdict = j.get("verdict", "insufficient_evidence")
        error_type = j.get("error_type", "unsupported_claim")
        quote = j.get("evidence_quote", "").strip()
        explanation = j.get("explanation", "No explanation provided.")
        confidence = j.get("confidence", "medium")

        quote_verified = False
        quote_start = None
        quote_end = None

        if quote:
            # Check if quote really exists in source
            start, end = find_substring_span(source_text, quote)
            if start != -1:
                quote_verified = True
                quote_start = start
                quote_end = end
            else:
                # Quote was hallucinated or altered by the LLM!
                quote_verified = False
                if verdict == "supported":
                    # Downgrade because the cited proof does not exist
                    verdict = "insufficient_evidence"
                    error_type = "unsupported_claim"
                    confidence = "low"
                    explanation += " [Verification Alert: The cited evidence quote was NOT found verbatim in the source text; downgraded from supported.]"
                else:
                    explanation += " [Verification Alert: The cited quote was not found verbatim in the source text.]"
        else:
            # Empty quote
            quote_verified = False

        # Ensure correct error_type for supported claims
        if verdict == "supported":
            error_type = "none"

        verified_claims.append(ClaimJudgment(
            claim_id=claim.claim_id,
            claim_text=claim.claim_text,
            original_text_quote=claim.original_text_quote,
            start_char=claim.start_char or 0,
            end_char=claim.end_char or len(claim.original_text_quote),
            verdict=verdict,
            error_type=error_type,
            evidence_quote=quote,
            quote_verified=quote_verified,
            quote_char_start=quote_start,
            quote_char_end=quote_end,
            explanation=explanation,
            confidence=confidence
        ))

    return verified_claims


def step_5_reasoning_check(source_text: str, ai_answer: str) -> ReasoningReport:
    """
    Stage 5: Reasoning Check.
    A separate LLM pass that checks whether conclusions in the answer follow
    logically from the evidence and earlier statements, detecting unwarranted
    leaps, contradictions, or dangerous extrapolations.
    """
    prompt = f"""You are a logical consistency and safety auditor.
Evaluate whether the AI-generated answer makes logically sound conclusions based on the source text,
or whether it contains logical fallacies, non-sequiturs, unwarranted causal leaps, or unsafe recommendations.

SOURCE TEXT:
\"\"\"{source_text}\"\"\"

AI-GENERATED ANSWER:
\"\"\"{ai_answer}\"\"\"

Respond with a JSON object containing:
- "has_reasoning_flaws": boolean (true if reasoning errors or unsafe leaps exist, false otherwise)
- "reasoning_summary": string (a 1-2 sentence overall assessment of logical soundness)
- "issues": array of objects with:
    * "issue_type": string (e.g. "unwarranted_extrapolation", "contradiction", "unsafe_advice")
    * "description": string (explanation of the logical flaw)
    * "severity": "low" | "medium" | "high"
"""

    raw = call_llm_json(
        prompt=prompt,
        system_instruction="You evaluate reasoning chains, logical validity, and safety implications."
    )

    if not isinstance(raw, dict):
        raw = {"has_reasoning_flaws": False, "reasoning_summary": "No logical flaws detected.", "issues": []}

    issues_raw = raw.get("issues", [])
    issues = [
        ReasoningIssue(
            issue_type=iss.get("issue_type", "logical_flaw"),
            description=iss.get("description", ""),
            severity=iss.get("severity", "medium")
        )
        for iss in issues_raw if isinstance(iss, dict)
    ]

    return ReasoningReport(
        has_reasoning_flaws=bool(raw.get("has_reasoning_flaws", False)),
        reasoning_summary=raw.get("reasoning_summary", "Logical analysis completed."),
        issues=issues
    )


def step_6_calculate_summary(
    claims: List[ClaimJudgment],
    reasoning: ReasoningReport
) -> AuditSummary:
    """
    Stage 6: Summary & Reliability Scoring.
    Counts verdicts and computes an overall reliability score (0-100).
    """
    total = len(claims)
    if total == 0:
        return AuditSummary(
            total_claims=0,
            supported_count=0,
            contradicted_count=0,
            insufficient_evidence_count=0,
            problematic_count=0,
            unverified_quotes_count=0,
            reliability_score=100,
            assessment_text="No factual claims found to audit."
        )

    supported = sum(1 for c in claims if c.verdict == "supported")
    contradicted = sum(1 for c in claims if c.verdict == "contradicted")
    insufficient = sum(1 for c in claims if c.verdict == "insufficient_evidence")
    unverified = sum(1 for c in claims if c.evidence_quote and not c.quote_verified)

    problematic = contradicted + insufficient

    # Transparent scoring formula:
    # Supported claims give positive weight: (supported / total) * 100
    # Contradicted claims penalize heavily (-15 per contradiction)
    # Severe reasoning flaws penalize (-15)
    base_score = (supported / total) * 100
    penalty = (contradicted * 15) + (15 if reasoning.has_reasoning_flaws else 0)
    score = int(max(0, min(100, round(base_score - penalty))))

    assessment = (
        f"{problematic} of {total} claims have problems "
        f"({contradicted} contradicted, {insufficient} unverified). "
        f"Overall reliability score: {score}/100."
    )

    return AuditSummary(
        total_claims=total,
        supported_count=supported,
        contradicted_count=contradicted,
        insufficient_evidence_count=insufficient,
        problematic_count=problematic,
        unverified_quotes_count=unverified,
        reliability_score=score,
        assessment_text=assessment
    )


# ==============================================================================
# MAIN ENTRY POINT FOR AUDIT PIPELINE
# ==============================================================================

def run_audit(source_text: str, ai_answer: str) -> AuditResult:
    """
    Runs the full Second Opinion audit pipeline on the provided source and answer.
    """
    # 1. Claim extraction
    claims = step_1_extract_claims(ai_answer)

    # 2. Evidence retrieval
    evidence_map = retrieve_evidence_for_claims(source_text, claims)

    # 3. Claim judging
    raw_judgments = step_3_judge_claims(claims, evidence_map)

    # 4. Programmatic verification (verbatim quote validation)
    verified_claims = step_4_verify_judgments(claims, raw_judgments, source_text)

    # 5. Reasoning check
    reasoning = step_5_reasoning_check(source_text, ai_answer)

    # 6. Summary & scoring
    summary = step_6_calculate_summary(verified_claims, reasoning)

    return AuditResult(
        source_text=source_text,
        original_answer=ai_answer,
        claims=verified_claims,
        reasoning=reasoning,
        summary=summary
    )


# ==============================================================================
# STANDALONE DEMO (When running python pipeline.py)
# ==============================================================================

if __name__ == "__main__":
    import json

    print("=" * 70)
    print(" SECOND OPINION: Running Pipeline Demo")
    print("=" * 70)

    # Sample source article: Mars Rover Mission Brief
    sample_source = (
        "The Curiosity rover landed on Mars in Gale Crater on August 6, 2012. "
        "Its primary mission was to determine whether Mars could have supported "
        "microbial life. Curiosity is powered by a Multi-Mission Radioisotope "
        "Thermoelectric Generator (MMRTG), not solar panels. During its journey, "
        "it confirmed evidence of past liquid water in ancient lake beds."
    )

    # Sample AI answer containing:
    # 1. Supported claim: Landed in August 2012 in Gale Crater
    # 2. Contradicted claim (hallucination): Powered by high-efficiency solar panels
    # 3. Unsupported / unsafe claim: Astronauts can safely drink water found by Curiosity
    sample_answer = (
        "Curiosity successfully touched down in Gale Crater in August 2012. "
        "The rover is powered exclusively by high-efficiency solar panels. "
        "Because ancient water was discovered, astronauts arriving on Mars can "
        "safely drink surface water without any filtration."
    )

    print("\n--- SOURCE TEXT ---")
    print(sample_source)
    print("\n--- AI-GENERATED ANSWER ---")
    print(sample_answer)
    print("\n[>>] Auditing now (extracting claims, retrieving evidence, verifying)...")

    result = run_audit(sample_source, sample_answer)

    print("\n" + "=" * 70)
    print(" AUDIT RESULTS")
    print("=" * 70)

    print(f"\n[Summary] {result.summary.assessment_text}")
    print(f"[Score]   {result.summary.reliability_score}/100")
    print(f"[Claims]  Total: {result.summary.total_claims} | "
          f"Supported: {result.summary.supported_count} | "
          f"Contradicted: {result.summary.contradicted_count} | "
          f"Insufficient Evidence: {result.summary.insufficient_evidence_count}")

    print("\n--- DETAILED CLAIM BREAKDOWN ---")
    for c in result.claims:
        status_symbol = "[OK]" if c.verdict == "supported" else "[X]" if c.verdict == "contradicted" else "[?]"
        print(f"\n{status_symbol} Claim #{c.claim_id}: \"{c.claim_text}\"")
        print(f"    Verdict:     {c.verdict.upper()} (Error: {c.error_type})")
        print(f"    Confidence:  {c.confidence.upper()}")
        print(f"    Explanation: {c.explanation}")
        if c.evidence_quote:
            verified_tag = "VERIFIED IN SOURCE" if c.quote_verified else "HALLUCINATED QUOTE"
            print(f"    Evidence:    \"{c.evidence_quote}\" [{verified_tag}]")

    print("\n--- REASONING & SAFETY REPORT ---")
    print(f"Has Flaws: {result.reasoning.has_reasoning_flaws}")
    print(f"Summary:   {result.reasoning.reasoning_summary}")
    for issue in result.reasoning.issues:
        print(f"  - [{issue.severity.upper()}] {issue.issue_type}: {issue.description}")

    print("\n" + "=" * 70)
    print("Pipeline run completed successfully!")
    print("=" * 70)
