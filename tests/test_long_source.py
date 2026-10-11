"""
tests/test_long_source.py
--------------------------------------------------------------------------------
Unit tests for Task 1:
- A 5,000-char source sends the full source in the judge prompt
- 25 claims make 3 judge calls and keep their order
- An over-limit source (>100k) or answer (>20k) returns 400
- A daily-quota 429 fails fast with QuotaExhaustedError
- A per-minute 429 waits and retries once (mock time.sleep)
--------------------------------------------------------------------------------
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from starlette.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from main import app, MAX_SOURCE_CHARS, MAX_ANSWER_CHARS
import pipeline
from pipeline import step_3_judge_claims, ExtractedClaim
import llm_client
from llm_client import QuotaExhaustedError, call_llm_json


class TestLongSourceAndLimits(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_5000_char_source_sends_full_source_in_judge_prompt(self):
        """A 5,000-char source sends the full source in the judge prompt."""
        source_text = "A" * 5000
        claims = [
            ExtractedClaim(
                claim_id=1,
                claim_text="Claim one",
                original_text_quote="Claim one quote",
                start_char=0,
                end_char=9
            )
        ]
        evidence_map = {1: "Some chunk snippet"}

        mock_llm_response = {
            "verdicts": [
                {
                    "claim_id": 1,
                    "verdict": "supported",
                    "confidence": "high",
                    "explanation": "Claim matches full source.",
                    "evidence_quote": "A" * 20
                }
            ]
        }

        captured_prompts = []

        def fake_call_llm_json(prompt, *args, **kwargs):
            captured_prompts.append(prompt)
            return mock_llm_response

        with patch("pipeline.call_llm_json", side_effect=fake_call_llm_json):
            results = step_3_judge_claims(claims, evidence_map, source_text=source_text)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(captured_prompts), 1)
        # Verify the 5,000 character source is in the prompt
        self.assertIn(source_text, captured_prompts[0])
        self.assertIn("SOURCE MATERIAL:", captured_prompts[0])

    def test_25_claims_make_3_judge_calls_and_keep_order(self):
        """25 claims make 3 judge calls and keep their order."""
        source_text = "B" * 5000
        claims = [
            ExtractedClaim(
                claim_id=i,
                claim_text=f"Claim text {i}",
                original_text_quote=f"Quote {i}",
                start_char=i * 10,
                end_char=i * 10 + 9
            )
            for i in range(25)
        ]
        evidence_map = {i: "Snippet" for i in range(25)}

        judge_calls = 0

        def fake_call_llm_json(prompt, *args, **kwargs):
            nonlocal judge_calls
            judge_calls += 1
            # Extract which claims are in this prompt
            batch_verdicts = []
            for i in range(25):
                cid = i
                if f"'claim_id': {cid}" in prompt or f'"claim_id": {cid}' in prompt:
                    batch_verdicts.append({
                        "claim_id": cid,
                        "verdict": "supported",
                        "confidence": "high",
                        "explanation": f"Explanation for {cid}",
                        "evidence_quote": "B" * 10
                    })
            return batch_verdicts

        with patch("pipeline.call_llm_json", side_effect=fake_call_llm_json):
            results = step_3_judge_claims(claims, evidence_map, source_text=source_text)

        self.assertEqual(judge_calls, 3)
        self.assertEqual(len(results), 25)
        # Check order is preserved
        expected_ids = list(range(25))
        actual_ids = [r["claim_id"] if isinstance(r, dict) else r.claim_id for r in results]
        self.assertEqual(actual_ids, expected_ids)

    def test_over_limit_source_or_answer_returns_400(self):
        """An over-limit source (>100k) or answer (>20k) returns 400."""
        # 1. Over-limit source
        over_source = "x" * (MAX_SOURCE_CHARS + 1)
        res_source = self.client.post("/audit", json={"source": over_source, "answer": "Valid answer"})
        self.assertEqual(res_source.status_code, 400)
        self.assertIn("100,000", res_source.json().get("detail", ""))

        # 2. Over-limit answer
        over_answer = "y" * (MAX_ANSWER_CHARS + 1)
        res_answer = self.client.post("/audit", json={"source": "Valid source", "answer": over_answer})
        self.assertEqual(res_answer.status_code, 400)
        self.assertIn("20,000", res_answer.json().get("detail", ""))

    def test_daily_quota_429_fails_fast_with_quota_exhausted_error(self):
        """A daily-quota 429 fails fast with QuotaExhaustedError."""
        llm_client.clear_exhausted_keys()

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception(
            "RESOURCE_EXHAUSTED: Quota exceeded perday for model gemini-3.8-flash"
        )

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_daily_key", "LLM_CACHE": "off"}, clear=True), \
             patch("llm_client.get_client", return_value=mock_client), \
             patch("time.sleep") as mock_sleep:
            with self.assertRaises(QuotaExhaustedError):
                call_llm_json(prompt="Hello world")

            # Must not do sleep backoffs since daily quota fails fast
            mock_sleep.assert_not_called()
            self.assertTrue(llm_client.is_key_exhausted("test_daily_key"))

    def test_per_minute_429_waits_and_retries_once(self):
        """A per-minute 429 waits and retries once (mock time.sleep)."""
        llm_client.clear_exhausted_keys()

        mock_resp = MagicMock()
        mock_resp.text = '{"success": true}'

        mock_client = MagicMock()
        # First call fails with 429 per-minute rate limit, second succeeds
        mock_client.models.generate_content.side_effect = [
            Exception("429 Too Many Requests: Rate limit exceeded. Please wait before retrying."),
            mock_resp
        ]

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test_rate_key", "LLM_CACHE": "off"}, clear=True), \
             patch("llm_client.get_client", return_value=mock_client), \
             patch("time.sleep") as mock_sleep:
            result = call_llm_json(prompt="Test prompt")

            self.assertEqual(result, {"success": True})
            # mock_sleep called once for the rate limit wait
            self.assertEqual(mock_sleep.call_count, 1)
            # The key should not be exhausted because retry succeeded
            self.assertFalse(llm_client.is_key_exhausted("test_rate_key"))

    def test_503_fallback_caches_under_fallback_model(self):
        """When 503 fallback model answers, cache under fallback model's key."""
        import hashlib
        import tempfile
        import shutil

        temp_cache = Path(tempfile.mkdtemp())
        prompt_text = "Test fallback caching prompt"
        fallback_model = "gemini-3.7-flash"
        primary_model = "gemini-3.8-flash"

        mock_resp = MagicMock()
        mock_resp.text = '{"cached_by": "fallback"}'

        mock_client = MagicMock()
        # Fail 5 times with 503, then succeed on fallback model
        mock_client.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            mock_resp
        ]

        try:
            with patch.dict(os.environ, {
                "GEMINI_API_KEY": "test_cache_key",
                "GEMINI_MODEL": primary_model,
                "GEMINI_FALLBACK_MODEL": fallback_model,
                "LLM_CACHE": "on"
            }, clear=True), \
                 patch("llm_client.CACHE_DIR", temp_cache), \
                 patch("llm_client.get_client", return_value=mock_client), \
                 patch("time.sleep"):
                res = call_llm_json(prompt=prompt_text)
                self.assertEqual(res, {"cached_by": "fallback"})

                # Calculate expected cache file for fallback model
                fallback_payload = f"{fallback_model}:0.1::{prompt_text}"
                fallback_key = hashlib.sha256(fallback_payload.encode("utf-8")).hexdigest()
                fallback_file = temp_cache / f"{fallback_key}.json"

                # Calculate primary model cache file
                primary_payload = f"{primary_model}:0.1::{prompt_text}"
                primary_key = hashlib.sha256(primary_payload.encode("utf-8")).hexdigest()
                primary_file = temp_cache / f"{primary_key}.json"

                self.assertTrue(fallback_file.exists(), "Cache file must exist under fallback model key")
                self.assertFalse(primary_file.exists(), "Cache file must NOT exist under primary model key")
        finally:
            shutil.rmtree(temp_cache, ignore_errors=True)
