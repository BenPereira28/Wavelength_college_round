"""
llm_client.py
--------------------------------------------------------------------------------
Isolated LLM Client Module for Second Opinion.

Why this file exists:
- All calls to the Gemini API (google-genai SDK) are centralized here.
- If you ever want to swap providers (e.g., to OpenAI, Anthropic, or a local
  model like Ollama), you ONLY need to edit this file!
- It handles API key loading, model selection, structured JSON prompting,
  disk caching (.llm_cache/), quota exhaustion detection (QuotaExhaustedError),
  and automatic retries.
--------------------------------------------------------------------------------
"""

import os
import json
import re
import time
import random
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env
load_dotenv()

# Global cached clients by key hash and active client
_clients: dict[str, genai.Client] = {}
_client: Optional[genai.Client] = None

# Track exhausted keys (key_hash -> date_str)
_exhausted_keys: dict[str, str] = {}

# Cache directory
CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"


class QuotaExhaustedError(Exception):
    """Raised when the daily Gemini free-tier quota is exhausted across all available keys/models."""
    pass


def _load_exhausted_keys_file() -> dict[str, str]:
    exhausted_file = CACHE_DIR / "exhausted_keys.json"
    if not exhausted_file.exists():
        return {}
    try:
        with open(exhausted_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_exhausted_keys_file(key_hash: str, date_str: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        exhausted_file = CACHE_DIR / "exhausted_keys.json"
        data = _load_exhausted_keys_file()
        data[key_hash] = date_str
        with open(exhausted_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def is_key_exhausted(key: str) -> bool:
    """
    Checks if an API key is marked exhausted for the current calendar day.
    Automatically resets when the date advances to the next day.
    Never prints or logs the key.
    """
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_local = datetime.now().strftime("%Y-%m-%d")
    valid_todays = {today_utc, today_local}

    # 1. In-memory check
    exp_date = _exhausted_keys.get(key_hash)
    if exp_date:
        if exp_date in valid_todays:
            return True
        else:
            del _exhausted_keys[key_hash]

    # 2. Persisted file check
    persisted = _load_exhausted_keys_file()
    exp_date = persisted.get(key_hash)
    if exp_date:
        if exp_date in valid_todays:
            _exhausted_keys[key_hash] = exp_date
            return True

    return False


def mark_key_exhausted(key: str) -> None:
    """
    Marks an API key as exhausted for the current calendar day.
    Uses SHA-256 hash to prevent leaking or logging raw keys.
    """
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _exhausted_keys[key_hash] = today_str
    _save_exhausted_keys_file(key_hash, today_str)


def clear_exhausted_keys() -> None:
    """
    Clears exhausted key tracking in memory and on disk.
    Helpful for tests and daily resets.
    """
    global _exhausted_keys, _clients, _client
    _exhausted_keys.clear()
    _clients.clear()
    _client = None
    exhausted_file = CACHE_DIR / "exhausted_keys.json"
    if exhausted_file.exists():
        try:
            exhausted_file.unlink()
        except Exception:
            pass


def get_api_keys() -> list[str]:
    """
    Reads API keys from GEMINI_API_KEYS (comma-separated list) and GEMINI_API_KEY from .env.
    Returns a deduplicated list of non-empty, non-placeholder keys preserving order.
    Never logs or prints any key.
    """
    keys: list[str] = []

    # 1. Comma-separated list
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    if raw_keys:
        for k in raw_keys.split(","):
            cleaned = k.strip().strip("'\"")
            if cleaned and not cleaned.startswith("your_gemini_api_key"):
                if cleaned not in keys:
                    keys.append(cleaned)

    # 2. Single key (fallback or additional)
    single_key = os.getenv("GEMINI_API_KEY", "").strip().strip("'\"")
    if single_key and not single_key.startswith("your_gemini_api_key"):
        if single_key not in keys:
            keys.append(single_key)

    return keys


def get_client(api_key: Optional[str] = None) -> genai.Client:
    """
    Initializes and returns the Google GenAI client.
    If api_key is None, selects the first available non-exhausted key.
    Never prints or logs the key.
    """
    global _client, _clients
    if api_key is None:
        keys = get_api_keys()
        if not keys:
            raise ValueError(
                "GEMINI_API_KEY or GEMINI_API_KEYS is not set. Please add your API key to the .env file."
            )
        available = [k for k in keys if not is_key_exhausted(k)]
        if not available:
            raise QuotaExhaustedError(
                "Daily Gemini free-tier quota used up for all configured API keys."
            )
        api_key = available[0]

    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    if key_hash not in _clients:
        _clients[key_hash] = genai.Client(api_key=api_key)
    _client = _clients[key_hash]
    return _clients[key_hash]


def get_model_name() -> str:
    """
    Returns the configured Gemini model name from .env, defaulting to 'gemini-3.8-flash'.
    """
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()


def get_fallback_model_name() -> str:
    """
    Returns the configured fallback Gemini model name from .env, defaulting to 'gemini-3.7-flash'.
    """
    return os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash").strip()


def clean_json_text(raw_text: str) -> str:
    """
    Strips markdown code blocks (e.g. ```json ... ```) from LLM output
    so it can be reliably parsed by json.loads().
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_retry_delay(err: Exception) -> Optional[int]:
    """
    Extracts retryDelay in seconds from the error string or payload if present.
    Example: 'retryDelay': '48918s' or 'retryDelay': '30s'
    """
    err_str = str(err)
    match = re.search(r"retrydelay['\":\s]+(\d+)", err_str, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return None


def call_llm_json(
    prompt: str,
    system_instruction: Optional[str] = None,
    retries: int = 3,
    temperature: float = 0.1
) -> Any:
    """
    Sends a prompt to the LLM and guarantees a parsed JSON response (dict or list).
    
    Features:
      - Multi-key rotation: Automatically switches to the next available API key when encountering
        a 429 quota error (without waiting), remembering exhausted keys until the next calendar day.
      - Exponential backoff & jitter: Retries on 503/UNAVAILABLE or 500 errors with backoff
        (waits of 2, 5, 12, 30s + jitter), switching to GEMINI_FALLBACK_MODEL before giving up.
      - Disk caching (.llm_cache/) keyed on sha256(model + temp + system_instruction + prompt).
        Disabled if LLM_CACHE=off in .env.
      - QuotaExhaustedError: Raised when all available keys have exhausted their quota.
    """
    model_name = get_model_name()
    cache_enabled = os.getenv("LLM_CACHE", "on").strip().lower() != "off"

    # 1. Check disk cache before making any API call (including temperature in hash)
    cache_payload = f"{model_name}:{temperature}:{system_instruction or ''}:{prompt}"
    cache_key = hashlib.sha256(cache_payload.encode("utf-8")).hexdigest()
    cache_file = CACHE_DIR / f"{cache_key}.json"

    if cache_enabled and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            return cached_data
        except Exception:
            # If cache read fails, proceed to call API
            pass

    # 2. API Call Execution with multi-key failover and error resiliency
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
        system_instruction=system_instruction
    )

    last_error: Optional[Exception] = None
    last_quota_error: Optional[Exception] = None

    while True:
        keys = get_api_keys()
        if not keys:
            raise ValueError(
                "GEMINI_API_KEY or GEMINI_API_KEYS is not set. Please add your API key to the .env file."
            )

        available_keys = [k for k in keys if not is_key_exhausted(k)]
        if not available_keys:
            if last_quota_error:
                raise QuotaExhaustedError(
                    "Daily Gemini free-tier quota used up for all configured API keys. Try again later or switch model/keys."
                ) from last_quota_error
            raise QuotaExhaustedError(
                "Daily Gemini free-tier quota used up for all configured API keys. Try again later or switch model/keys."
            )

        current_key = available_keys[0]
        key_idx = keys.index(current_key) if current_key in keys else 0
        client = get_client(current_key)
        switched_key_due_to_quota = False
        current_prompt = prompt

        current_model = model_name
        fallback_model = get_fallback_model_name()
        has_switched_to_fallback = False

        backoff_delays = [2, 5, 12, 30]
        server_error_attempt = 0
        json_retries_left = retries

        while True:
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=current_prompt,
                    config=config
                )

                raw_text = response.text or ""
                cleaned = clean_json_text(raw_text)
                parsed = json.loads(cleaned)

                # 3. Store valid result in disk cache
                if cache_enabled:
                    try:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(parsed, f, indent=2)
                    except Exception:
                        pass

                return parsed

            except (json.JSONDecodeError, Exception) as e:
                last_error = e
                err_text = str(e)
                err_lower = err_text.lower()
                retry_delay = extract_retry_delay(e)

                # 1. 429 Quota exhaustion: Rotate immediately to next key, never wait
                is_quota = (
                    "429" in err_lower
                    or "quota" in err_lower
                    or "resource_exhausted" in err_lower
                    or "perday" in err_lower
                    or (retry_delay is not None and retry_delay > 60)
                )
                if is_quota:
                    logging.warning(
                        f"Quota error (429) on API key index {key_idx}. Rotating to next API key."
                    )
                    mark_key_exhausted(current_key)
                    last_quota_error = e
                    switched_key_due_to_quota = True
                    break  # Break inner loop to rotate to next key in outer loop

                # 2. 503 / UNAVAILABLE or 500 error: Exponential backoff with jitter, then fallback model
                is_server_error = (
                    "503" in err_lower
                    or "unavailable" in err_lower
                    or "500" in err_lower
                    or "internal" in err_lower
                )
                if is_server_error:
                    if server_error_attempt < len(backoff_delays):
                        base_wait = backoff_delays[server_error_attempt]
                        jitter = random.uniform(0.1, 1.0)
                        wait_sec = base_wait + jitter
                        server_error_attempt += 1
                        time.sleep(wait_sec)
                        continue
                    elif not has_switched_to_fallback and fallback_model != current_model:
                        # Switch to fallback model before giving up
                        logging.warning(
                            f"Server error retries exhausted on {current_model}. Switching to fallback model {fallback_model} before giving up."
                        )
                        current_model = fallback_model
                        has_switched_to_fallback = True
                        server_error_attempt = 0
                        continue
                    else:
                        raise RuntimeError(
                            f"Server error persisted on {current_model} after retries. Last error: {last_error}"
                        ) from e

                # 3. JSONDecodeError or other general parsing errors
                if json_retries_left > 0:
                    json_retries_left -= 1
                    time.sleep(1.0)
                    current_prompt += "\n\nCRITICAL: Please ensure your output is strictly valid, parseable JSON."
                    continue

                raise RuntimeError(
                    f"Failed to obtain valid JSON from LLM after {retries + 1} attempts. Last error: {last_error}"
                ) from e
