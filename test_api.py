"""
test_api.py
--------------------------------------------------------------------------------
Automated test script for Step 3: FastAPI Endpoints.
Uses FastAPI's TestClient to verify the endpoints without needing
a running background server.
--------------------------------------------------------------------------------
"""

import sys
from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app
from pipeline import AuditResult, AuditSummary, ReasoningReport

client = TestClient(app)
# Only the standalone script may opt into the real Gemini-backed audit.
LIVE = __name__ == "__main__" and "--live" in sys.argv


def mock_audit_result(source: str, answer: str) -> AuditResult:
    """Return a valid audit response without contacting Gemini."""
    return AuditResult(
        source_text=source,
        original_answer=answer,
        claims=[],
        reasoning=ReasoningReport(
            has_reasoning_flaws=False,
            reasoning_summary="Mock audit completed.",
            issues=[]
        ),
        summary=AuditSummary(
            total_claims=0,
            supported_count=0,
            contradicted_count=0,
            insufficient_evidence_count=0,
            problematic_count=0,
            unverified_quotes_count=0,
            reliability_score=100,
            score_breakdown={"final_score": 100},
            assessment_text="Mock audit completed."
        )
    )

def test_endpoints():
    print("=" * 60)
    print("Testing Second Opinion FastAPI Endpoints (Step 3)")
    print("=" * 60)

    # 1. Test GET /
    print("[1] Testing GET / ...")
    res_root = client.get("/")
    assert res_root.status_code == 200, f"Expected 200, got {res_root.status_code}"
    assert "Second Opinion" in res_root.text
    print("    [OK] GET / returned HTTP 200 with HTML frontend.")

    # 2. Test GET /health
    print("[2] Testing GET /health ...")
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json().get("status") == "healthy"
    print("    [OK] GET /health returned HTTP 200:", res_health.json())

    # 3. Test POST /audit validation (empty source)
    print("[3] Testing POST /audit validation with empty body ...")
    res_empty = client.post("/audit", json={"source": "", "answer": ""})
    assert res_empty.status_code == 400, f"Expected 400, got {res_empty.status_code}"
    print("    [OK] POST /audit correctly rejected empty input with HTTP 400.")

    # 4. Test POST /audit with sample data. Mock by default so this script
    # never calls Gemini unless explicitly run with `python test_api.py --live`.
    print("[4] Testing POST /audit with {}audit request ...".format("real " if LIVE else "mocked "))
    payload = {
        "source": "Water boils at 100 degrees Celsius at standard atmospheric pressure.",
        "answer": "Water boils at 100 degrees Celsius at sea level, but boils at 200 degrees on Mount Everest."
    }
    if LIVE:
        res_audit = client.post("/audit", json=payload)
    else:
        with patch("main.run_audit", side_effect=mock_audit_result):
            res_audit = client.post("/audit", json=payload)
    assert res_audit.status_code == 200, f"Expected 200, got {res_audit.status_code}: {res_audit.text}"
    
    data = res_audit.json()
    print("    [OK] POST /audit returned HTTP 200.")
    print("    [Result Summary]   ", data.get("summary", {}).get("assessment_text"))
    print("    [Reliability Score]", data.get("summary", {}).get("reliability_score"))
    print("    [Score Breakdown]  ", data.get("summary", {}).get("score_breakdown"))
    print("    [Claims Audited]   ", len(data.get("claims", [])))

    print("=" * 60)
    print("All API tests passed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    if LIVE:
        test_endpoints()
    else:
        with patch("main.run_audit", side_effect=mock_audit_result):
            test_endpoints()
