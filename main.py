"""
main.py
--------------------------------------------------------------------------------
FastAPI Backend Server for Second Opinion.

This server provides:
  1. GET  /                   -> Serves the single-page application (static/index.html)
  2. GET  /health             -> Health check endpoint
  3. GET  /api/status         -> API health, Gemini API availability, and active model
  4. POST /audit & /api/audit -> Evaluates an answer against source material (with offline fallback)
  5. GET  /api/sample-audit   -> Canonical sample audit response (sample_response.json)
  6. Static assets            -> Mounted at /static for CSS and JS files

Single-Command Launch:
  `python main.py` or `uvicorn main:app --reload`
--------------------------------------------------------------------------------
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Core verification pipeline & LLM error types
from pipeline import run_audit, AuditResult
from llm_client import QuotaExhaustedError

# Document extraction
from file_extract import extract_text, ExtractionError

# ------------------------------------------------------------------------------
# APP INITIALIZATION
# ------------------------------------------------------------------------------
app = FastAPI(
    title="Second Opinion API",
    description="Automated auditing platform evaluating AI-generated answers against source material.",
    version="1.0.0"
)

# Enable CORS for frontend and API consumers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Mount the static directory
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ------------------------------------------------------------------------------
# DATA MODELS
# ------------------------------------------------------------------------------
class AuditRequest(BaseModel):
    source: str = Field(..., description="The trusted source material or reference document.")
    answer: str = Field(..., description="The AI-generated answer or text to audit.")


# ------------------------------------------------------------------------------
# HELPER FUNCTIONS
# ------------------------------------------------------------------------------
def has_valid_gemini_key() -> bool:
    """Returns True if a real Gemini API key is configured."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return bool(key and key != "your_gemini_api_key_here")


def load_canonical_sample() -> Dict[str, Any]:
    """Loads sample_response.json from project root."""
    sample_file = BASE_DIR / "sample_response.json"
    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "source_text": "Sample source unavailable.",
        "original_answer": "Sample answer unavailable.",
        "claims": [],
        "reasoning": {"has_reasoning_flaws": False, "reasoning_summary": "No flaws.", "issues": []},
        "summary": {
            "total_claims": 0,
            "supported_count": 0,
            "contradicted_count": 0,
            "insufficient_evidence_count": 0,
            "problematic_count": 0,
            "unverified_quotes_count": 0,
            "reliability_score": 100,
            "score_breakdown": {},
            "assessment_text": "No data available."
        }
    }


# ------------------------------------------------------------------------------
# API ROUTES
# ------------------------------------------------------------------------------
@app.get("/", summary="Serve the Web Frontend")
def serve_frontend():
    """Serves the single-page application from static/index.html."""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(index_file)


@app.get("/health", summary="Health Check")
def health_check():
    """Returns 200 OK status to confirm the server is running."""
    return {
        "status": "healthy",
        "service": "Second Opinion Audit API",
        "version": "1.0.0"
    }


@app.get("/api/status", summary="System & Model Status")
def api_status():
    """Returns system status, active model, and Gemini key availability."""
    has_key = has_valid_gemini_key()
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    return {
        "status": "online",
        "service": "Second Opinion Audit Platform",
        "has_api_key": has_key,
        "model": model,
        "pipeline_ready": True,
        "mode": "live_ready" if has_key else "sample_fallback_ready"
    }


@app.get("/api/sample-audit", summary="Get Canonical Sample Audit")
def get_sample_audit():
    """Returns canonical sample audit response (sample_response.json)."""
    data = load_canonical_sample()
    data["is_sample_data"] = True
    data["data_source"] = "canonical_sample_response_json"
    return data


@app.post("/api/extract", summary="Extract Text from Document (PDF, TXT, DOCX)")
async def extract_document(file: UploadFile = File(...)):
    """
    Extracts text from an uploaded PDF, TXT, or DOCX document.
    Does NOT call Gemini API or consume LLM quota.
    Validates magic bytes, enforces configurable size and page caps,
    and returns in-memory extracted text.
    """
    # Configurable limits from environment
    max_mb_str = os.getenv("MAX_UPLOAD_MB", "5").strip()
    try:
        max_upload_mb = float(max_mb_str)
    except ValueError:
        max_upload_mb = 5.0
    max_bytes = int(max_upload_mb * 1024 * 1024)

    max_pages_str = os.getenv("MAX_PDF_PAGES", "100").strip()
    try:
        max_pdf_pages = int(max_pages_str)
    except ValueError:
        max_pdf_pages = 100

    filename = file.filename or "unknown"
    ext = os.path.splitext(filename.lower())[1]

    # Pre-check extension
    allowed_exts = {".pdf", ".txt", ".docx"}
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file extension '{ext or 'none'}'. Supported formats are .pdf, .docx, and .txt."
        )

    # Bounded read to prevent memory exhaustion
    buffer = bytearray()
    chunk_size = 64 * 1024  # 64 KB chunks

    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds the maximum upload limit of {max_upload_mb:g} MB."
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed reading uploaded file: {str(e)}"
        )
    finally:
        await file.close()

    raw_data = bytes(buffer)
    if len(raw_data) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Extract text with magic bytes validation and formatting cleanup
    try:
        result = extract_text(
            filename=filename,
            data=raw_data,
            max_length_cap=20_000,
            max_pdf_pages=max_pdf_pages,
            insert_page_markers=True
        )
        return {
            "filename": filename,
            **result
        }
    except ExtractionError as ee:
        raise HTTPException(status_code=ee.status_code, detail=ee.message)
    except Exception as e:
        logging.error("File extraction error: %s", type(e).__name__)
        raise HTTPException(status_code=400, detail="The file could not be extracted.")


@app.post("/audit", summary="Run Full Audit Pipeline")
@app.post("/api/audit", summary="Run Full Audit Pipeline (Alias)")
def audit_text(payload: AuditRequest):
    """
    Evaluates an AI-generated answer against provided source material.
    Requires a valid GEMINI_API_KEY. Returns 503 if API key is not configured,
    and 429 if the daily quota is exhausted. Never falls back to canned sample data.
    """
    source_clean = payload.source.strip()
    answer_clean = payload.answer.strip()

    if len(payload.source) > 20_000:
        raise HTTPException(status_code=400, detail="Source material must be 20,000 characters or fewer.")

    if len(payload.answer) > 20_000:
        raise HTTPException(status_code=400, detail="AI answer must be 20,000 characters or fewer.")

    if not source_clean:
        raise HTTPException(status_code=400, detail="Source material cannot be empty.")

    if not answer_clean:
        raise HTTPException(status_code=400, detail="AI answer cannot be empty.")

    # Require valid API key — never return canned sample data silently
    if not has_valid_gemini_key():
        raise HTTPException(
            status_code=503,
            detail="Gemini API key is not configured. Please add your GEMINI_API_KEY to the .env file to run live audits."
        )

    # Live Gemini Pipeline Execution
    try:
        result = run_audit(source_clean, answer_clean)
        res_dict = result.model_dump()
        res_dict["is_sample_data"] = False
        res_dict["data_source"] = "live_gemini_pipeline"
        return res_dict
    except QuotaExhaustedError:
        raise HTTPException(
            status_code=429,
            detail="Daily Gemini API quota exhausted for this key and model. Please try again later or update your API key in .env."
        )
    except Exception as e:
        logging.error(f"Audit pipeline error: {e}")
        raise HTTPException(
            status_code=502,
            detail="The language model service could not complete the audit."
        )


# ------------------------------------------------------------------------------
# SERVER ENTRY POINT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print("=" * 65)
    print("  SECOND OPINION: AI Fact-Checking & Auditing Platform")
    print("=" * 65)
    print("Server running at:       http://127.0.0.1:8000")
    print("Interactive Swagger Docs: http://127.0.0.1:8000/docs")
    print("API Health Endpoint:      http://127.0.0.1:8000/health")
    print("=" * 65)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
