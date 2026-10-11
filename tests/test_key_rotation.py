"""
test_key_rotation.py
--------------------------------------------------------------------------------
Unit tests for multi-key rotation and exhausted-key tracking in llm_client.py.
All Gemini client interactions are mocked; no real API calls are performed.
--------------------------------------------------------------------------------
"""

import os
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import llm_client
from llm_client import (
    get_api_keys,
    get_client,
    is_key_exhausted,
    mark_key_exhausted,
    clear_exhausted_keys,
    call_llm_json,
    QuotaExhaustedError,
)


@pytest.fixture(autouse=True)
def reset_keys_state():
    """Ensure a clean slate for keys and cache before and after each test."""
    clear_exhausted_keys()
    yield
    clear_exhausted_keys()


def test_get_api_keys_single_key():
    """Verify that GEMINI_API_KEY continues to work as a single key."""
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_single_key_123"}, clear=True):
        keys = get_api_keys()
        assert keys == ["test_single_key_123"]


def test_get_api_keys_comma_separated():
    """Verify that GEMINI_API_KEYS is parsed as a comma-separated list."""
    with patch.dict(os.environ, {"GEMINI_API_KEYS": "key_a, key_b , key_c"}, clear=True):
        keys = get_api_keys()
        assert keys == ["key_a", "key_b", "key_c"]


def test_get_api_keys_both_env_vars_and_deduplication():
    """Verify both env vars combined without duplicates, preserving order."""
    with patch.dict(os.environ, {
        "GEMINI_API_KEYS": "key_1, key_2",
        "GEMINI_API_KEY": "key_1"
    }, clear=True):
        keys = get_api_keys()
        assert keys == ["key_1", "key_2"]


def test_get_api_keys_filters_placeholders():
    """Verify placeholder values are ignored."""
    with patch.dict(os.environ, {
        "GEMINI_API_KEY": "your_gemini_api_key_here",
        "GEMINI_API_KEYS": "your_gemini_api_key_1_here, key_real_val"
    }, clear=True):
        keys = get_api_keys()
        assert keys == ["key_real_val"]


def test_no_keys_configured_raises_value_error():
    """Verify ValueError is raised when no keys are present."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="GEMINI_API_KEY or GEMINI_API_KEYS is not set"):
            get_client()


def test_quota_429_switches_to_next_key_and_retries():
    """
    When a call fails with a 429 quota error, switch to the next key,
    mark the first key exhausted, and return the successful result.
    """
    keys = ["key_alpha_mock", "key_beta_mock"]
    clients_called = []

    def mock_get_client(api_key=None):
        clients_called.append(api_key)
        mock_c = MagicMock()
        if api_key == "key_alpha_mock":
            # Simulate 429 Quota Exhaustion error
            mock_c.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
        elif api_key == "key_beta_mock":
            # Second key succeeds
            mock_resp = MagicMock()
            mock_resp.text = '{"success": true, "key_used": "second"}'
            mock_c.models.generate_content.return_value = mock_resp
        return mock_c

    with patch.dict(os.environ, {
        "GEMINI_API_KEYS": f"{keys[0]},{keys[1]}",
        "LLM_CACHE": "off"
    }, clear=True):
        with patch("llm_client.get_client", side_effect=mock_get_client):
            res = call_llm_json("Sample prompt")
            assert res == {"success": True, "key_used": "second"}
            # The first key must be marked exhausted
            assert is_key_exhausted("key_alpha_mock") is True
            # The second key must NOT be exhausted
            assert is_key_exhausted("key_beta_mock") is False
            # Verify client was called with key_alpha first, then key_beta
            assert clients_called == ["key_alpha_mock", "key_beta_mock"]


def test_exhausted_keys_remembered_until_next_day():
    """
    Verify exhausted keys are remembered within the same calendar day,
    and automatically become available on the next day.
    """
    key = "key_remember_test"
    mark_key_exhausted(key)
    assert is_key_exhausted(key) is True

    # Advance date to tomorrow
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    with patch("llm_client.datetime") as mock_dt:
        mock_dt.now.return_value = tomorrow
        mock_dt.timezone = timezone
        # On the next day, the key should no longer be exhausted
        assert is_key_exhausted(key) is False


def test_all_keys_exhausted_raises_quota_exhausted_error():
    """
    When all keys encounter 429 quota errors, QuotaExhaustedError is raised.
    """
    keys = ["key_1_fail", "key_2_fail"]

    def mock_get_client(api_key=None):
        mock_c = MagicMock()
        mock_c.models.generate_content.side_effect = Exception("429 QuotaExhausted: rate limit reached")
        return mock_c

    with patch.dict(os.environ, {
        "GEMINI_API_KEYS": f"{keys[0]},{keys[1]}",
        "LLM_CACHE": "off"
    }, clear=True):
        with patch("llm_client.get_client", side_effect=mock_get_client):
            with pytest.raises(QuotaExhaustedError):
                call_llm_json("Sample prompt")

            assert is_key_exhausted("key_1_fail") is True
            assert is_key_exhausted("key_2_fail") is True


def test_server_error_backoff_and_fallback_model():
    """
    On 503/UNAVAILABLE or 500 error, retries with exponential backoff & jitter,
    and after retries are exhausted, switches to GEMINI_FALLBACK_MODEL before giving up.
    """
    models_tried = []
    sleeps = []

    mock_c = MagicMock()
    def fake_generate(model, contents, config):
        models_tried.append(model)
        # Fail on primary model 4 times (exhausting backoff delays [2, 5, 12, 30])
        if model == "gemini-3.8-flash":
            raise Exception("503 Service Unavailable")
        # Succeed on fallback model
        resp = MagicMock()
        resp.text = '{"recovered_by": "fallback_model"}'
        return resp

    mock_c.models.generate_content.side_effect = fake_generate

    with patch.dict(os.environ, {
        "GEMINI_API_KEY": "test_key_resilient",
        "GEMINI_MODEL": "gemini-3.8-flash",
        "GEMINI_FALLBACK_MODEL": "gemini-3.7-flash",
        "LLM_CACHE": "off"
    }, clear=True):
        with patch("llm_client.get_client", return_value=mock_c), \
             patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
            res = call_llm_json("Test prompt")
            assert res == {"recovered_by": "fallback_model"}
            # Primary was retried 4 times with backoff, then 5th attempt switched to fallback model
            assert models_tried.count("gemini-3.8-flash") == 5
            assert models_tried.count("gemini-3.7-flash") == 1
            # Verify 4 sleep intervals occurred with backoff values >= base delays [2, 5, 12, 30]
            assert len(sleeps) == 4
            assert sleeps[0] >= 2.0
            assert sleeps[1] >= 5.0
            assert sleeps[2] >= 12.0
            assert sleeps[3] >= 30.0


def test_429_logs_only_key_index_never_the_key():
    """
    Verify that 429 error logs only the key's index (never the key itself).
    """
    keys = ["secret_key_zero_xyz", "secret_key_one_abc"]
    log_messages = []

    mock_c = MagicMock()
    def fake_generate(model, contents, config):
        if len(log_messages) == 0:
            raise Exception("429 RESOURCE_EXHAUSTED")
        resp = MagicMock()
        resp.text = '{"result": "ok"}'
        return resp

    mock_c.models.generate_content.side_effect = fake_generate

    class FakeLogger:
        def warning(self, msg, *args, **kwargs):
            log_messages.append(str(msg))

    with patch.dict(os.environ, {
        "GEMINI_API_KEYS": f"{keys[0]},{keys[1]}",
        "LLM_CACHE": "off"
    }, clear=True):
        with patch("llm_client.get_client", return_value=mock_c), \
             patch("llm_client.logging.warning", side_effect=lambda m: log_messages.append(str(m))):
            res = call_llm_json("Test prompt")
            assert res == {"result": "ok"}
            assert len(log_messages) == 1
            # Log message must contain the index '0'
            assert "index 0" in log_messages[0]
            # Log message must NEVER contain the secret keys
            assert "secret_key_zero_xyz" not in log_messages[0]
            assert "secret_key_one_abc" not in log_messages[0]


def test_save_results_md_from_completed_cases_only(tmp_path):
    """
    Verify eval/RESULTS.md reports strictly from completed cases,
    stating clearly how many of the total ran without inventing numbers.
    """
    import eval.run_eval as run_eval

    custom_md = tmp_path / "RESULTS.md"
    with patch.object(run_eval, "RESULTS_MD", custom_md):
        # 2 completed cases, 1 error case, total configured cases: 5
        sample_results = [
            {
                "id": "case_1",
                "category": "wrong_facts",
                "total_claims": 4,
                "total_planted": 2,
                "caught": 2,
                "verdict_matched": 2,
                "total_clean": 2,
                "false_flags": 0,
                "unverified_quotes": 0,
                "reliability_score": 75,
                "status": "success"
            },
            {
                "id": "case_2",
                "category": "unsupported",
                "total_claims": 2,
                "total_planted": 1,
                "caught": 1,
                "verdict_matched": 1,
                "total_clean": 1,
                "false_flags": 0,
                "unverified_quotes": 0,
                "reliability_score": 80,
                "status": "success"
            },
            {
                "id": "case_3",
                "category": "unsafe",
                "status": "error",
                "error": "TimeoutError: connection lost",
                "total_claims": 0,
                "total_planted": 1,
                "caught": 0,
                "verdict_matched": 0,
                "total_clean": 1,
                "false_flags": 0,
                "unverified_quotes": 0,
                "reliability_score": None
            }
        ]

        run_eval.save_results_md(sample_results, total_cases=5)
        content = custom_md.read_text(encoding="utf-8")

        # Must state clearly how many of the total ran
        assert "2 of 5 total test cases (40.0%)" in content
        # Must compute totals from the 2 completed cases only (planted: 3, caught: 3, 100.0%)
        assert "3/3 (100.0%)" in content
        # Must not fabricate numbers for case_3 or the unrun cases 4 and 5
        assert "case_1" in content
        assert "case_2" in content
        assert "TimeoutError: connection lost" in content


def test_run_eval_continues_on_single_case_error(tmp_path):
    """
    Verify run_eval catches an error in one case, records it as 'error',
    continues the remaining cases, and saves progress after each case.
    """
    import eval.run_eval as run_eval

    custom_json = tmp_path / "results.json"
    custom_md = tmp_path / "RESULTS.md"
    custom_cases = tmp_path / "cases.json"

    cases = [
        {"id": "case_ok", "category": "cat1", "source": "s1", "answer": "a1"},
        {"id": "case_bad", "category": "cat2", "source": "s2", "answer": "a2"},
        {"id": "case_ok2", "category": "cat3", "source": "s3", "answer": "a3"},
    ]
    custom_cases.write_text(json.dumps(cases), encoding="utf-8")

    def mock_eval(case):
        if case["id"] == "case_bad":
            raise RuntimeError("API timeout")
        return {
            "id": case["id"],
            "category": case["category"],
            "total_claims": 2,
            "total_planted": 1,
            "caught": 1,
            "verdict_matched": 1,
            "total_clean": 1,
            "false_flags": 0,
            "unverified_quotes": 0,
            "planted_detail": [],
            "clean_detail": [],
            "reliability_score": 90,
        }

    with patch.object(run_eval, "RESULTS_JSON", custom_json), \
         patch.object(run_eval, "RESULTS_MD", custom_md), \
         patch.object(run_eval, "CASES_FILE", custom_cases), \
         patch.object(run_eval, "evaluate_case", side_effect=mock_eval), \
         patch.object(run_eval, "validate_cases"), \
         patch("sys.argv", ["run_eval.py"]):
        run_eval.main()

    # Verify results.json has 2 completed and 1 error case
    data = json.loads(custom_json.read_text(encoding="utf-8"))
    assert data["cases_evaluated"] == 2
    assert len(data["per_case"]) == 3
    assert data["per_case"][1]["status"] == "error"
    assert "API timeout" in data["per_case"][1]["error"]

    # Verify RESULTS.md reflects completed cases only (2 of 3)
    md = custom_md.read_text(encoding="utf-8")
    assert "2 of 3 total test cases (66.7%)" in md
    assert "case_ok" in md
    assert "case_ok2" in md


def test_fallback_model_caches_under_fallback_model_key(tmp_path):
    """
    Mock the Gemini client so the primary model keeps failing with a 503 and
    the fallback model succeeds. Use a temp cache dir and make sure cache is turned on.
    Assert the result is stored under the FALLBACK model's cache key and that no
    cache file exists under the PRIMARY model's key for the same prompt.
    """
    import hashlib

    temp_cache = tmp_path / ".test_cache"
    temp_cache.mkdir(parents=True, exist_ok=True)
    prompt_text = "Prompt to verify fallback caching"
    primary_model = "gemini-3.8-flash"
    fallback_model = "gemini-3.7-flash"

    mock_resp = MagicMock()
    mock_resp.text = '{"cached_by": "fallback"}'

    mock_client = MagicMock()
    # Primary fails with 503 until retries exhausted, then fallback model succeeds
    mock_client.models.generate_content.side_effect = [
        Exception("503 Service Unavailable"),
        Exception("503 Service Unavailable"),
        Exception("503 Service Unavailable"),
        Exception("503 Service Unavailable"),
        Exception("503 Service Unavailable"),
        mock_resp,
    ]

    with patch.dict(os.environ, {
        "GEMINI_API_KEY": "test_cache_key_fb",
        "GEMINI_MODEL": primary_model,
        "GEMINI_FALLBACK_MODEL": fallback_model,
        "LLM_CACHE": "on"
    }, clear=True), \
         patch("llm_client.CACHE_DIR", temp_cache), \
         patch("llm_client.get_client", return_value=mock_client), \
         patch("time.sleep"):
        res = call_llm_json(prompt=prompt_text)
        assert res == {"cached_by": "fallback"}

        # Expected fallback cache file
        fallback_payload = f"{fallback_model}:0.1::{prompt_text}"
        fallback_key = hashlib.sha256(fallback_payload.encode("utf-8")).hexdigest()
        fallback_file = temp_cache / f"{fallback_key}.json"

        # Primary model cache file
        primary_payload = f"{primary_model}:0.1::{prompt_text}"
        primary_key = hashlib.sha256(primary_payload.encode("utf-8")).hexdigest()
        primary_file = temp_cache / f"{primary_key}.json"

        assert fallback_file.exists(), "Cache file must exist under the fallback model's key"
        assert not primary_file.exists(), "No cache file must exist under the primary model's key"



