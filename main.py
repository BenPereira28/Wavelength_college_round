"""
main.py
--------------------------------------------------------------------------------
FastAPI Backend Server for Second Opinion.

This server provides:
  1. GET  /        -> Serves the web frontend (HTML/CSS/JS)
  2. POST /audit   -> Runs the 6-stage audit pipeline and returns the full
                      audit report as structured JSON
  3. GET  /health  -> Simple status endpoint to confirm the server is running

Beginner Friendly:
  - Run this server by simply typing `python main.py` in your terminal,
    or using `uvicorn main:app --reload`.
--------------------------------------------------------------------------------
"""

import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Import our core audit pipeline
from pipeline import run_audit, AuditResult

# ------------------------------------------------------------------------------
# APP INITIALIZATION
# ------------------------------------------------------------------------------
app = FastAPI(
    title="Second Opinion API",
    description="Automated auditing platform evaluating AI-generated answers against source material.",
    version="1.0.0"
)

# Enable CORS (Cross-Origin Resource Sharing) so any browser client can connect smoothly
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Locate directories
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Ensure static directory exists
STATIC_DIR.mkdir(exist_ok=True)

# Mount the static directory for CSS, JavaScript, and asset files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ------------------------------------------------------------------------------
# REQUEST DATA MODEL
# ------------------------------------------------------------------------------
class AuditRequest(BaseModel):
    """
    The incoming payload required by the /audit endpoint.
    """
    source: str = Field(
        ...,
        description="The trusted source material or reference document."
    )
    answer: str = Field(
        ...,
        description="The AI-generated answer or text to audit."
    )


# ------------------------------------------------------------------------------
# API ROUTES
# ------------------------------------------------------------------------------

@app.get("/", summary="Serve the Web Frontend")
def serve_frontend():
    """
    Serves the single-page application from static/index.html.
    """
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(index_file)


@app.get("/health", summary="Health Check")
def health_check():
    """
    Returns a quick 200 OK status to verify that the server is alive.
    """
    return {
        "status": "healthy",
        "service": "Second Opinion Audit API",
        "version": "1.0.0"
    }


@app.post("/audit", response_model=AuditResult, summary="Run Full Audit Pipeline")
def audit_text(payload: AuditRequest):
    """
    Evaluates an AI-generated answer against provided source material.
    
    Returns:
      - Breakdown of factual claims with substring positions
      - Veracity verdicts (supported, contradicted, insufficient_evidence)
      - Error categorization and verbatim quote verification
      - Logical reasoning & safety check
      - Overall reliability score (0-100) and score breakdown
    """
    source_clean = payload.source.strip()
    answer_clean = payload.answer.strip()

    if not source_clean:
        raise HTTPException(
            status_code=400,
            detail="Source material cannot be empty."
        )

    if not answer_clean:
        raise HTTPException(
            status_code=400,
            detail="AI answer cannot be empty."
        )

    try:
        # Run our 6-stage pipeline
        result = run_audit(source_clean, answer_clean)
        return result
    except Exception as e:
        # Catch unexpected pipeline errors and return a clean 500 error
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while auditing: {str(e)}"
        )


# ------------------------------------------------------------------------------
# SERVER ENTRY POINT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("Starting Second Opinion Server on http://127.0.0.1:8000")
    print("Open your browser and visit: http://127.0.0.1:8000")
    print("Interactive API Docs:        http://127.0.0.1:8000/docs")
    print("=" * 60)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
