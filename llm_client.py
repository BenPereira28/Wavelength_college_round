"""
llm_client.py
--------------------------------------------------------------------------------
Isolated LLM Client Module for Second Opinion.

Why this file exists:
- All calls to the Gemini API (google-genai SDK) are centralized here.
- If you ever want to swap providers (e.g., to OpenAI, Anthropic, or a local
  model like Ollama), you ONLY need to edit this file!
- It handles API key loading, model selection, structured JSON prompting,
  and automatic retries if the model returns malformed JSON.
--------------------------------------------------------------------------------
"""

import os
import json
import re
import time
from typing import Any, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables from .env
load_dotenv()

# Global cached client
_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    """
    Initializes and returns the Google GenAI client using GEMINI_API_KEY from .env.
    """
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please add your API key to the .env file."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def get_model_name() -> str:
    """
    Returns the configured Gemini model name from .env, defaulting to 'gemini-flash-latest'.
    """
    return os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()


def clean_json_text(raw_text: str) -> str:
    """
    Strips markdown code blocks (e.g. ```json ... ```) from LLM output
    so it can be reliably parsed by json.loads().
    """
    text = raw_text.strip()
    # Remove markdown code block fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def call_llm_json(
    prompt: str,
    system_instruction: Optional[str] = None,
    retries: int = 2,
    temperature: float = 0.1
) -> Any:
    """
    Sends a prompt to the LLM and guarantees a parsed JSON response (dict or list).
    
    If the response is malformed or an error occurs, it will retry up to `retries` times.
    
    Args:
        prompt: The user query or task instructions.
        system_instruction: Optional system persona or constraints.
        retries: Number of retry attempts on failure.
        temperature: Creativity parameter (lower = more deterministic for audits).
        
    Returns:
        Parsed Python dictionary or list.
    """
    client = get_client()
    model_name = get_model_name()

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
        system_instruction=system_instruction
    )

    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )

            raw_text = response.text or ""
            cleaned = clean_json_text(raw_text)
            
            parsed = json.loads(cleaned)
            return parsed

        except (json.JSONDecodeError, Exception) as e:
            last_error = e
            if attempt < retries:
                # Brief backoff before retry
                time.sleep(1.0)
                # Append a reminder to return valid JSON on retry
                prompt += "\n\nCRITICAL: Please ensure your output is strictly valid, parseable JSON."

    raise RuntimeError(
        f"Failed to obtain valid JSON from LLM after {retries + 1} attempts. Last error: {last_error}"
    )
