"""
test_setup.py
--------------------------------------------------------------------------------
A quick diagnostic script for Step 1.
This script verifies that all required Python packages are installed
and checks whether your Gemini API key is loaded from .env.
--------------------------------------------------------------------------------
"""

import sys
import os
from dotenv import load_dotenv

def main():
    print("=" * 60)
    print("Second Opinion - Step 1 Environment Verification")
    print("=" * 60)

    # 1. Check Python version
    print(f"[OK] Python version: {sys.version.split()[0]}")

    # 2. Check Package Imports
    packages = ["dotenv", "pydantic", "fastapi", "uvicorn", "google.genai"]
    for pkg in packages:
        try:
            __import__(pkg)
            print(f"[OK] Successfully imported: {pkg}")
        except ImportError as e:
            print(f"[FAIL] Missing package: {pkg} -> {e}")
            return

    # 3. Check .env loading
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    fallback_model = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash").strip()

    print("-" * 60)
    print(f"Target Gemini Model:   {model}")
    print(f"Fallback Gemini Model: {fallback_model}")

    from llm_client import get_api_keys
    keys = get_api_keys()

    if not keys:
        print("[!] No valid Gemini API keys detected (GEMINI_API_KEY / GEMINI_API_KEYS empty or placeholder).")
        print("    -> Open '.env' in your editor and paste your free key from:")
        print("       https://aistudio.google.com/")
    else:
        print(f"[OK] Gemini API key(s) detected: {len(keys)} configured.")

    print("=" * 60)
    print("Step 1 verification completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
