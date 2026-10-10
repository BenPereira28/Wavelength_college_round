"""
test_api.py
--------------------------------------------------------------------------------
Automated test script for Step 3: FastAPI Endpoints.
Uses FastAPI's TestClient to verify the endpoints without needing
a running background server.
--------------------------------------------------------------------------------
"""

import sys
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

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

    # 4. Test POST /audit with sample data
    print("[4] Testing POST /audit with real audit request ...")
    payload = {
        "source": "Water boils at 100 degrees Celsius at standard atmospheric pressure.",
        "answer": "Water boils at 100 degrees Celsius at sea level, but boils at 200 degrees on Mount Everest."
    }
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
    test_endpoints()
