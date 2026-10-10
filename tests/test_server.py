"""
tests/test_server.py
--------------------------------------------------------------------------------
Comprehensive test suite for Second Opinion Backend API.
Uses standard Python unittest and Starlette TestClient.
--------------------------------------------------------------------------------
"""

import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from starlette.testclient import TestClient

# Ensure root directory is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import app
from llm_client import QuotaExhaustedError
from pipeline import AuditResult, AuditSummary, ReasoningReport


def make_dummy_audit_result(source: str, answer: str) -> AuditResult:
    return AuditResult(
        source_text=source,
        original_answer=answer,
        claims=[],
        reasoning=ReasoningReport(has_reasoning_flaws=False, reasoning_summary="No issues.", issues=[]),
        summary=AuditSummary(
            total_claims=0,
            supported_count=0,
            contradicted_count=0,
            insufficient_evidence_count=0,
            problematic_count=0,
            unverified_quotes_count=0,
            reliability_score=100,
            score_breakdown={"final_score": 100},
            assessment_text="All sound."
        )
    )


class TestSecondOpinionServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_01_root_index_html(self):
        """Test that GET / returns the frontend HTML page with proper elements."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        content = response.text
        self.assertIn("Second Opinion", content)
        self.assertIn("sourceInput", content)
        self.assertIn("demoDataBanner", content)
        self.assertIn("Mars Rover", content)

    def test_02_static_css_and_js_served(self):
        """Test that static CSS and JavaScript files are served with HTTP 200."""
        css_res = self.client.get("/static/style.css")
        self.assertEqual(css_res.status_code, 200)
        self.assertIn("demo-data-banner", css_res.text)

        js_res = self.client.get("/static/app.js")
        self.assertEqual(js_res.status_code, 200)
        self.assertIn("triggerAudit", js_res.text)
        self.assertIn("loadSampleAudit", js_res.text)

    def test_03_health_endpoint(self):
        """Test GET /health returns healthy status."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "healthy")
        self.assertEqual(data.get("service"), "Second Opinion Audit API")

    def test_04_system_status_endpoint(self):
        """Test GET /api/status returns valid JSON structure and health info."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "online")
        self.assertIn("has_api_key", data)
        self.assertIn("model", data)
        self.assertTrue(data.get("pipeline_ready"))

    def test_05_sample_audit_endpoint(self):
        """Test GET /api/sample-audit returns canonical sample with is_sample_data=True."""
        response = self.client.get("/api/sample-audit")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("source_text", data)
        self.assertIn("original_answer", data)
        self.assertIn("summary", data)
        self.assertIn("claims", data)
        self.assertGreater(len(data["claims"]), 0)
        self.assertTrue(data.get("is_sample_data"))

    def test_06_audit_post_validation_empty(self):
        """Test POST /audit rejects empty inputs with HTTP 400."""
        response = self.client.post("/audit", json={"source": "", "answer": ""})
        self.assertEqual(response.status_code, 400)

    def test_07_audit_post_missing_key_503(self):
        """Test POST /audit returns 503 when API key is missing (never silent demo data)."""
        with patch("main.has_valid_gemini_key", return_value=False):
            payload = {"source": "Valid source", "answer": "Valid answer"}
            response = self.client.post("/audit", json=payload)
            self.assertEqual(response.status_code, 503)
            self.assertIn("Gemini API key is not configured", response.json().get("detail", ""))

    def test_08_audit_post_quota_exhausted_429(self):
        """Test POST /audit returns 429 when quota is exhausted."""
        with patch("main.has_valid_gemini_key", return_value=True), \
             patch("main.run_audit", side_effect=QuotaExhaustedError("Daily limit reached")):
            payload = {"source": "Valid source", "answer": "Valid answer"}
            response = self.client.post("/audit", json=payload)
            self.assertEqual(response.status_code, 429)
            self.assertIn("Daily Gemini API quota exhausted", response.json().get("detail", ""))

    def test_09_audit_post_success(self):
        """Test POST /audit returns 200 with is_sample_data=False on live run."""
        with patch("main.has_valid_gemini_key", return_value=True), \
             patch("main.run_audit", side_effect=make_dummy_audit_result):
            payload = {"source": "Valid source", "answer": "Valid answer"}
            response = self.client.post("/audit", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data.get("is_sample_data"), False)
            self.assertEqual(data.get("data_source"), "live_gemini_pipeline")

    def test_10_api_audit_alias_endpoint(self):
        """Test POST /api/audit alias functions identically to /audit."""
        with patch("main.has_valid_gemini_key", return_value=False):
            payload = {"source": "Valid source", "answer": "Valid answer"}
            response = self.client.post("/api/audit", json=payload)
            self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
