"""
file_extract.py
--------------------------------------------------------------------------------
Backend file extraction module for Second Opinion.
Extracts clean, normalized plain text from PDF, TXT, and DOCX files.

Key guarantees:
  - Untrusted data handling: files are never executed or rendered.
  - Safe in-memory stream processing.
  - Robust multi-encoding support for TXT files.
  - Scanned PDF detection (no OCR support).
  - Strict format validation for DOCX (rejects legacy .doc and macro-enabled .docm).
  - Truncation indicator and warnings if source length cap is exceeded.
--------------------------------------------------------------------------------
"""

import io
import os
import re
import zipfile
from typing import Dict, Any, List, Optional
import pypdf
import docx

# Custom Exception Hierarchy
class ExtractionError(Exception):
    """Base exception for file extraction errors with custom HTTP status codes."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code

class UnsupportedFileTypeError(ExtractionError):
    def __init__(self, message: str = "Unsupported file type."):
        super().__init__(message, status_code=415)

class ScannedPdfError(ExtractionError):
    def __init__(self, message: str = "Scanned or image-only PDFs are not supported. No text layer found (OCR not supported)."):
        super().__init__(message, status_code=422)

class FileTooLargeError(ExtractionError):
    def __init__(self, message: str = "File exceeds the maximum allowed size."):
        super().__init__(message, status_code=413)

class CorruptFileError(ExtractionError):
    def __init__(self, message: str = "The uploaded file is corrupt or unreadable."):
        super().__init__(message, status_code=400)


def detect_file_type_magic(data: bytes, filename: str = "") -> str:
    """
    Validates true file type by checking magic bytes and payload signatures.
    Returns normalized file type: 'pdf', 'docx', or 'txt'.
    Raises UnsupportedFileTypeError or CorruptFileError if invalid.
    """
    if not data or len(data.strip()) == 0:
        raise CorruptFileError("Uploaded file is empty.")

    ext = os.path.splitext(filename.lower())[1] if filename else ""

    # PDF Magic Bytes: starts with %PDF-
    if data.startswith(b"%PDF-") or (b"%PDF-" in data[:1024]):
        return "pdf"

    # Zip-based formats (DOCX / DOCM / ODT / etc.)
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06") or data.startswith(b"PK\x07\x08"):
        # Explicit check for legacy .doc or macro-enabled .docm
        if ext == ".docm":
            raise UnsupportedFileTypeError("Macro-enabled Word documents (.docm) are not allowed.")
        if ext == ".doc":
            raise UnsupportedFileTypeError("Legacy Word documents (.doc) are not supported. Please convert to .docx.")

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                namelist = zf.namelist()
                # Strict check for docx: must contain word/document.xml
                if "word/document.xml" in namelist:
                    # Check for macro components inside zip
                    if any("vbaProject.bin" in name.lower() for name in namelist):
                        raise UnsupportedFileTypeError("Macro-enabled Word documents (.docm) are not allowed.")
                    return "docx"
                else:
                    raise UnsupportedFileTypeError("Zip archive is not a valid DOCX document.")
        except zipfile.BadZipFile:
            raise CorruptFileError("Invalid or corrupted zip/docx archive.")

    # Legacy OLE2 .doc magic bytes: \xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise UnsupportedFileTypeError("Legacy Word documents (.doc) are not supported. Please convert to .docx.")

    # Check for text files: must not have excessive null bytes
    if ext == ".txt":
        null_count = data[:1024].count(b"\x00")
        # UTF-16 may have null bytes, so let txt decoding handle UTF-16, but reject pure binary garbage
        if null_count > 500 and not (data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff")):
            raise UnsupportedFileTypeError("File appears to be binary data, not a plain text file.")
        return "txt"

    # If extension is pdf/docx but magic bytes failed
    if ext == ".pdf":
        raise CorruptFileError("File has .pdf extension but does not contain valid PDF header (%PDF-).")
    if ext in [".docx", ".docm", ".doc"]:
        raise CorruptFileError("File has Word extension but is not a valid document archive.")

    raise UnsupportedFileTypeError(f"Unsupported file type '{ext or 'unknown'}'. Only .pdf, .docx, and .txt files are supported.")


def _clean_text_formatting(text: str) -> str:
    """
    Cleans common text extraction artifacts:
    - Normalizes Windows (\\r\\n) and legacy Mac (\\r) line breaks to \\n
    - Joins hyphenated line breaks (e.g. 'instruc-\\ntion' -> 'instruction')
    - Collapses repeated horizontal spaces/tabs
    - Collapses 3+ consecutive newlines to 2
    """
    if not text:
        return ""

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Join hyphenated line breaks: word ending with hyphen, followed by newline, followed by lower-case word
    text = re.sub(r'(\b[a-zA-Z]+)-\n([a-zA-Z]+\b)', r'\1\2', text)

    # Collapse repeated spaces/tabs (preserve newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)

    # Clean lines with only whitespace
    text = re.sub(r'\n[ \t]+\n', '\n\n', text)

    # Collapse excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def extract_txt(data: bytes) -> Dict[str, Any]:
    """
    Extracts text from raw bytes using encoding fallback cascade:
    utf-8-sig -> utf-16 (if BOM present or null-interleaved) -> cp1252 -> latin-1
    """
    decoded_text: Optional[str] = None
    used_encoding: Optional[str] = None

    # Check for UTF-16 BOM explicitly first
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            decoded_text = data.decode("utf-16")
            used_encoding = "utf-16"
        except UnicodeDecodeError:
            pass

    # Try utf-8-sig (handles UTF-8 with or without BOM)
    if decoded_text is None:
        try:
            decoded_text = data.decode("utf-8-sig")
            used_encoding = "utf-8-sig"
        except UnicodeDecodeError:
            pass

    # If it has heavy null byte proportion, it could be UTF-16 without BOM
    if decoded_text is None and data[:512].count(b"\x00") > 10:
        for u16 in ["utf-16-le", "utf-16-be", "utf-16"]:
            try:
                decoded_text = data.decode(u16)
                used_encoding = u16
                break
            except UnicodeDecodeError:
                pass

    # Fallback to cp1252 (Windows Western European)
    if decoded_text is None:
        try:
            decoded_text = data.decode("cp1252")
            used_encoding = "cp1252"
        except UnicodeDecodeError:
            pass

    # Final fallback to latin-1 (ISO-8859-1)
    if decoded_text is None:
        try:
            decoded_text = data.decode("latin-1")
            used_encoding = "latin-1"
        except UnicodeDecodeError:
            pass

    if decoded_text is None:
        raise CorruptFileError("Unable to decode text file with supported encodings (UTF-8, UTF-16, CP1252, Latin-1).")

    cleaned = _clean_text_formatting(decoded_text)
    if not cleaned:
        raise CorruptFileError("Text file is empty or contains only whitespace.")

    return {
        "text": cleaned,
        "page_count": None,
        "encoding": used_encoding
    }


def extract_pdf(data: bytes, max_pages: int = 100, insert_page_markers: bool = True) -> Dict[str, Any]:
    """
    Extracts text page-by-page from PDF bytes using pypdf.
    - Rejects encrypted PDFs with a clear error.
    - Limits page count to max_pages.
    - Detects scanned/image-only PDFs (no text layer).
    - Optionally inserts '[Page N]' markers for evidence traceability.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as e:
        raise CorruptFileError(f"Corrupt or unreadable PDF: {str(e)}")

    if reader.is_encrypted:
        raise ExtractionError("Encrypted or password-protected PDFs are not supported. Please decrypt before uploading.", status_code=400)

    total_pages = len(reader.pages)
    if total_pages == 0:
        raise CorruptFileError("PDF contains no pages.")

    if total_pages > max_pages:
        raise ExtractionError(
            f"PDF exceeds the maximum limit of {max_pages} pages (uploaded file has {total_pages} pages).",
            status_code=400
        )

    page_texts: List[str] = []
    total_raw_chars = 0

    for idx, page in enumerate(reader.pages, start=1):
        try:
            p_text = page.extract_text() or ""
        except Exception as e:
            p_text = ""

        cleaned_p = _clean_text_formatting(p_text)
        total_raw_chars += len(cleaned_p)

        if cleaned_p:
            if insert_page_markers and total_pages > 1:
                page_texts.append(f"[Page {idx}]\n{cleaned_p}")
            else:
                page_texts.append(cleaned_p)

    # Scanned PDF check: average characters per page < 10 or total chars < 15
    if total_raw_chars < 15 or (total_pages > 0 and (total_raw_chars / total_pages) < 8):
        raise ScannedPdfError("Scanned or image-only PDFs are not supported. No text layer was found (OCR not supported).")

    full_text = "\n\n".join(page_texts)
    return {
        "text": full_text,
        "page_count": total_pages
    }


def extract_docx(data: bytes) -> Dict[str, Any]:
    """
    Extracts text from DOCX bytes using python-docx.
    Includes paragraphs, table cells, and headers/footers if present.
    """
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise CorruptFileError(f"Corrupt or unreadable DOCX file: {str(e)}")

    parts: List[str] = []

    # 1. Headers (if unique and not empty)
    header_texts = set()
    for section in doc.sections:
        if section.header and section.header.paragraphs:
            for p in section.header.paragraphs:
                ht = p.text.strip()
                if ht and ht not in header_texts:
                    header_texts.add(ht)
    if header_texts:
        parts.append("\n".join(header_texts))

    # 2. Main paragraphs & tables in document order
    # Extract paragraphs
    for p in doc.paragraphs:
        pt = p.text.strip()
        if pt:
            parts.append(pt)

    # Extract tables
    for table in doc.tables:
        table_rows = []
        for row in table.rows:
            # Join cells in a row with ' | '
            row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            # Remove consecutive duplicates from merged cells
            dedup_cells = []
            for c in row_cells:
                if not dedup_cells or c != dedup_cells[-1]:
                    dedup_cells.append(c)
            row_line = " | ".join(filter(None, dedup_cells))
            if row_line:
                table_rows.append(row_line)
        if table_rows:
            parts.append("\n".join(table_rows))

    # 3. Footers (if unique and not empty)
    footer_texts = set()
    for section in doc.sections:
        if section.footer and section.footer.paragraphs:
            for p in section.footer.paragraphs:
                ft = p.text.strip()
                if ft and ft not in footer_texts:
                    footer_texts.add(ft)
    if footer_texts:
        parts.append("\n".join(footer_texts))

    combined = "\n\n".join(parts)
    cleaned = _clean_text_formatting(combined)

    if not cleaned:
        raise CorruptFileError("DOCX file contains no readable text.")

    return {
        "text": cleaned,
        "page_count": None
    }


def extract_text(
    filename: str,
    data: bytes,
    max_length_cap: int = 20_000,
    max_pdf_pages: int = 100,
    insert_page_markers: bool = True
) -> Dict[str, Any]:
    """
    Main extraction interface.
    Accepts filename and raw bytes, validates content magic bytes, extracts text,
    normalizes output, and applies length cap warnings.

    Returns:
      {
        "text": str,
        "char_count": int,
        "page_count": int | None,
        "warnings": List[str],
        "truncated": bool,
        "file_type": str
      }
    """
    if not data or len(data) == 0:
        raise CorruptFileError("Uploaded file is empty.")

    # Detect file type from magic bytes & file extension
    file_type = detect_file_type_magic(data, filename)

    warnings: List[str] = []
    page_count: Optional[int] = None
    extracted_text = ""

    if file_type == "txt":
        res = extract_txt(data)
        extracted_text = res["text"]
        page_count = res["page_count"]
    elif file_type == "pdf":
        res = extract_pdf(data, max_pages=max_pdf_pages, insert_page_markers=insert_page_markers)
        extracted_text = res["text"]
        page_count = res["page_count"]
    elif file_type == "docx":
        res = extract_docx(data)
        extracted_text = res["text"]
        page_count = res["page_count"]
    else:
        raise UnsupportedFileTypeError(f"Unsupported file type: {file_type}")

    original_char_count = len(extracted_text)
    truncated = False

    # Check against source length cap (20,000 chars)
    if original_char_count > max_length_cap:
        truncated = True
        warnings.append(
            f"Extracted text ({original_char_count:,} characters) exceeds the pipeline limit of "
            f"{max_length_cap:,} characters. Text was capped at {max_length_cap:,} characters. "
            f"Please review and edit before auditing."
        )
        extracted_text = extracted_text[:max_length_cap]

    return {
        "text": extracted_text,
        "char_count": len(extracted_text),
        "original_char_count": original_char_count,
        "page_count": page_count,
        "warnings": warnings,
        "truncated": truncated,
        "file_type": file_type
    }
