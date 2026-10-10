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

    # 4. Test POST /audit when API key is missing (returns HTTP 503)
    print("[4] Testing POST /audit when GEMINI_API_KEY is missing (HTTP 503) ...")
    with patch("main.has_valid_gemini_key", return_value=False):
        res_nokey = client.post("/audit", json={"source": "Real source", "answer": "Real answer"})
        assert res_nokey.status_code == 503, f"Expected 503, got {res_nokey.status_code}"
        assert "Gemini API key is not configured" in res_nokey.json().get("detail", "")
    print("    [OK] POST /audit correctly returns HTTP 503 when API key is missing.")

    # 5. Test POST /audit with mocked/live audit execution
    print("[5] Testing POST /audit execution ({}) ...".format("live" if LIVE else "mocked"))
    payload = {
        "source": "Water boils at 100 degrees Celsius at standard atmospheric pressure.",
        "answer": "Water boils at 100 degrees Celsius at sea level, but boils at 200 degrees on Mount Everest."
    }
    if LIVE:
        res_audit = client.post("/audit", json=payload)
    else:
        with patch("main.has_valid_gemini_key", return_value=True), \
             patch("main.run_audit", side_effect=mock_audit_result):
            res_audit = client.post("/audit", json=payload)
    assert res_audit.status_code == 200, f"Expected 200, got {res_audit.status_code}: {res_audit.text}"
    data = res_audit.json()
    assert data.get("is_sample_data") is False
    print("    [OK] POST /audit returned HTTP 200 (is_sample_data=False).")

    # 6. Test GET /api/sample-audit (explicit canonical demo)
    print("[6] Testing GET /api/sample-audit (explicit demo endpoint) ...")
    res_sample = client.get("/api/sample-audit")
    assert res_sample.status_code == 200
    sample_data = res_sample.json()
    assert sample_data.get("is_sample_data") is True
    assert "claims" in sample_data
    print("    [OK] GET /api/sample-audit returned HTTP 200 with is_sample_data=True.")

    print("=" * 60)
    print("All API tests passed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    test_endpoints()
