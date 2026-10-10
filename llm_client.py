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
import hashlib
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env
load_dotenv()

# Global cached client
_client: Optional[genai.Client] = None

# Cache directory
CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"


class QuotaExhaustedError(Exception):
    """Raised when the daily Gemini free-tier quota is exhausted for a key/model."""
    pass


def get_client() -> genai.Client:
    """
    Initializes and returns the Google GenAI client using GEMINI_API_KEY from .env.
    """
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError(
                "GEMINI_API_KEY is not set. Please add your API key to the .env file."
            )
        _client = genai.Client(api_key=api_key)
    return _client


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
      - Disk caching (.llm_cache/) keyed on sha256(model + temp + system_instruction + prompt).
        Disabled if LLM_CACHE=off in .env.
      - QuotaExhaustedError: immediately raised without retrying if error contains 'PerDay'
        or retryDelay > 60s.
      - Short per-minute 429s: waits retryDelay (max 60s) and retries once.
      - Transient 503 unavailable spikes: backoff and failover to fallback model (GEMINI_FALLBACK_MODEL).
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

    # 2. API Call Execution
    client = get_client()
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
        system_instruction=system_instruction
    )

    last_error: Optional[Exception] = None
    has_retried_429 = False

    for attempt in range(retries + 1):
        try:
            current_model = model_name
            if attempt > 0 and last_error and ("503" in str(last_error).lower() or "unavailable" in str(last_error).lower()):
                current_model = get_fallback_model_name()

            response = client.models.generate_content(
                model=current_model,
                contents=prompt,
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

            # Rule 1: Daily quota exhaustion check (PerDay, RESOURCE_EXHAUSTED or retryDelay > 60s)
            if "perday" in err_lower or "resource_exhausted" in err_lower or (retry_delay is not None and retry_delay > 60):
                raise QuotaExhaustedError(
                    "Daily Gemini free-tier quota used up for this key and model. Try again later or switch model/key."
                ) from e

            if attempt < retries:
                # Rule 2: Short per-minute 429
                if "429" in err_lower or "quota" in err_lower:
                    if has_retried_429:
                        # Only retry once for short 429s
                        break
                    has_retried_429 = True
                    wait_time = min(60, retry_delay) if retry_delay else 5
                    time.sleep(wait_time)
                elif "503" in err_lower or "unavailable" in err_lower:
                    demand_delays = [2, 4, 8]
                    delay = demand_delays[attempt] if attempt < len(demand_delays) else 5
                    time.sleep(delay)
                else:
                    time.sleep(1.0)
                    prompt += "\n\nCRITICAL: Please ensure your output is strictly valid, parseable JSON."

    raise RuntimeError(
        f"Failed to obtain valid JSON from LLM after {retries + 1} attempts. Last error: {last_error}"
    )
