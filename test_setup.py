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
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    print("-" * 60)
    print(f"Target Gemini Model: {model}")

    if not api_key or api_key == "your_gemini_api_key_here":
        print("[!] GEMINI_API_KEY is currently empty or set to placeholder.")
        print("    -> Open '.env' in your editor and paste your free key from:")
        print("       https://aistudio.google.com/")
    else:
        # Mask the key for display security
        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
        print(f"[OK] GEMINI_API_KEY detected: {masked}")

    print("=" * 60)
    print("Step 1 verification completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
