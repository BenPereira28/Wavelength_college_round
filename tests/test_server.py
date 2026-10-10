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

    def test_11_extract_txt_endpoint_success(self):
        """Test POST /api/extract extracts plain text successfully with no LLM calls."""
        with patch("main.run_audit") as mock_audit:
            files = {"file": ("document.txt", b"Ground truth reference data from extracted text.", "text/plain")}
            response = self.client.post("/api/extract", files=files)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["file_type"], "txt")
            self.assertIn("Ground truth reference data", data["text"])
            self.assertEqual(data["truncated"], False)
            # Ensure LLM pipeline was never invoked
            mock_audit.assert_not_called()

    def test_12_extract_empty_file_400(self):
        """Test POST /api/extract returns 400 for empty file."""
        files = {"file": ("empty.txt", b"", "text/plain")}
        response = self.client.post("/api/extract", files=files)
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json().get("detail", "").lower())

    def test_13_extract_unsupported_extension_415(self):
        """Test POST /api/extract returns 415 for unsupported extension."""
        files = {"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")}
        response = self.client.post("/api/extract", files=files)
        self.assertEqual(response.status_code, 415)
        self.assertIn("Unsupported file extension", response.json().get("detail", ""))

    def test_14_extract_magic_byte_mismatch_400(self):
        """Test POST /api/extract returns 400 if a file has .pdf extension but fake contents."""
        files = {"file": ("fake.pdf", b"This is not a real PDF file header.", "application/pdf")}
        response = self.client.post("/api/extract", files=files)
        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF", response.json().get("detail", ""))

    def test_15_extract_file_too_large_413(self):
        """Test POST /api/extract returns 413 if file exceeds MAX_UPLOAD_MB."""
        with patch.dict("os.environ", {"MAX_UPLOAD_MB": "0.001"}):  # ~1 KB cap
            oversized_data = b"A" * 2048
            files = {"file": ("oversized.txt", oversized_data, "text/plain")}
            response = self.client.post("/api/extract", files=files)
            self.assertEqual(response.status_code, 413)
            self.assertIn("exceeds the maximum upload limit", response.json().get("detail", ""))

    def test_16_extract_scanned_pdf_422(self):
        """Test POST /api/extract returns 422 for a scanned/image-only PDF."""
        import pypdf
        import io
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)
        files = {"file": ("scanned.pdf", buf.getvalue(), "application/pdf")}
        response = self.client.post("/api/extract", files=files)
        self.assertEqual(response.status_code, 422)
        self.assertIn("Scanned or image-only", response.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
