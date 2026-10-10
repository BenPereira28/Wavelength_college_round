"""
tests/test_extract.py
--------------------------------------------------------------------------------
Comprehensive unit tests for file_extract.py.
Tests all file formats, encodings, boundary cases, corrupt/empty data,
length limits, page caps, and scanned PDF detection.
Zero LLM or Gemini API calls.
--------------------------------------------------------------------------------
"""

import io
import zipfile
import pytest
import pypdf
import docx

from file_extract import (
    extract_text,
    detect_file_type_magic,
    ExtractionError,
    UnsupportedFileTypeError,
    ScannedPdfError,
    CorruptFileError,
    FileTooLargeError
)


# ==============================================================================
# HELPERS TO GENERATE MINIMAL IN-MEMORY FILES
# ==============================================================================
def create_minimal_pdf(pages_text: list[str]) -> bytes:
    """Creates a minimal valid PDF with specified text per page using pypdf."""
    writer = pypdf.PdfWriter()
    for text in pages_text:
        # pypdf allows creating blank pages or adding text
        page = writer.add_blank_page(width=612, height=792)
        # Note: blank pages in pypdf don't contain a text stream unless drawn.
        # Alternatively, build standard PDF streams directly:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_text_pdf(pages_text: list[str]) -> bytes:
    """
    Constructs a minimal valid PDF with an actual text stream on each page
    using pure PDF syntax without external heavy PDF generators.
    """
    objects = []
    
    def add_obj(content: str) -> int:
        objects.append(content)
        return len(objects)

    # obj 1: Catalog
    # obj 2: Pages
    # subsequent objs: Page, Content stream, Font
    page_obj_ids = []
    
    # We will build after defining font and contents
    font_id = 3
    # Reserve slots for Catalog (1) and Pages (2)
    # Font object
    font_str = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    page_entries = []
    for text in pages_text:
        # Sanitize text for PDF string literal
        safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_stream = f"BT /F1 12 Tf 50 750 Td ({safe_text}) Tj ET"
        stream_bytes = content_stream.encode("latin-1")
        content_obj = f"<< /Length {len(stream_bytes)} >>\nstream\n{content_stream}\nendstream"
        page_entries.append((content_obj, safe_text))

    # Assemble all objects
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    
    offsets = [0]
    all_objs = []
    
    # We will lay out:
    # 1: Catalog
    # 2: Pages
    # 3: Font
    # 4..(4+len*2-1): Page objs and Stream objs
    num_pages = len(pages_text)
    cur_id = 4
    page_ids = []
    stream_ids = []
    for _ in range(num_pages):
        page_ids.append(cur_id)
        cur_id += 1
        stream_ids.append(cur_id)
        cur_id += 1

    catalog = f"<< /Type /Catalog /Pages 2 0 R >>"
    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages = f"<< /Type /Pages /Kids [ {kids_str} ] /Count {num_pages} >>"
    
    all_objs.append((1, catalog))
    all_objs.append((2, pages))
    all_objs.append((3, font_str))
    
    for i in range(num_pages):
        pid = page_ids[i]
        sid = stream_ids[i]
        page_obj = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {sid} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        all_objs.append((pid, page_obj))
        all_objs.append((sid, page_entries[i][0]))

    for obj_id, obj_body in all_objs:
        offsets.append(out.tell())
        out.write(f"{obj_id} 0 obj\n{obj_body}\nendobj\n".encode("latin-1"))

    xref_offset = out.tell()
    out.write(f"xref\n0 {len(all_objs) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))

    out.write(f"trailer\n<< /Size {len(all_objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("latin-1"))
    return out.getvalue()


def create_minimal_docx(paragraphs: list[str], table_data: list[list[str]] = None, header_text: str = "", footer_text: str = "") -> bytes:
    """Creates a valid DOCX file in memory with paragraphs and an optional table."""
    doc = docx.Document()
    if header_text and doc.sections:
        doc.sections[0].header.paragraphs[0].text = header_text
    for p in paragraphs:
        doc.add_paragraph(p)
    if table_data:
        table = doc.add_table(rows=len(table_data), cols=len(table_data[0]))
        for r_idx, row in enumerate(table_data):
            for c_idx, val in enumerate(row):
                table.cell(r_idx, c_idx).text = val
    if footer_text and doc.sections:
        doc.sections[0].footer.paragraphs[0].text = footer_text
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ==============================================================================
# TXT EXTRACTION TESTS (Multiple Encodings & Normalization)
# ==============================================================================
def test_extract_txt_utf8():
    text = "Artificial Intelligence audit report.\n\nAll facts verified."
    data = text.encode("utf-8")
    res = extract_text("test.txt", data)
    assert res["file_type"] == "txt"
    assert "Artificial Intelligence audit report." in res["text"]
    assert res["char_count"] == len(res["text"])
    assert res["truncated"] is False
    assert res["warnings"] == []


def test_extract_txt_utf8_sig():
    text = "UTF-8 with BOM content: verified and factual."
    data = text.encode("utf-8-sig")
    res = extract_text("bom.txt", data)
    assert res["file_type"] == "txt"
    assert "UTF-8 with BOM content" in res["text"]
    assert not res["text"].startswith("\ufeff")


def test_extract_txt_utf16():
    text = "UTF-16 encoded source document."
    data = text.encode("utf-16")
    res = extract_text("utf16.txt", data)
    assert res["file_type"] == "txt"
    assert "UTF-16 encoded source document." in res["text"]


def test_extract_txt_cp1252():
    # CP1252 specific byte codes: 0x93 (“), 0x94 (”), 0x97 (—)
    data = b"Windows-1252 test: \x93quoted text\x94 and \x97 dash."
    res = extract_text("cp1252.txt", data)
    assert res["file_type"] == "txt"
    assert "quoted text" in res["text"]


def test_extract_txt_latin1():
    text = "Café au lait and naïve résumé."
    data = text.encode("latin-1")
    res = extract_text("latin1.txt", data)
    assert res["file_type"] == "txt"
    assert "Café au lait" in res["text"]


def test_extract_txt_empty_or_whitespace():
    with pytest.raises(CorruptFileError):
        extract_text("empty.txt", b"")
    with pytest.raises(CorruptFileError):
        extract_text("spaces.txt", b"   \n\n\t   ")


def test_extract_txt_line_ending_and_hyphen_normalization():
    raw = "This is a demon-\nstration of hyphen-\nation in text.\r\nNext line here.\rAnd here."
    res = extract_text("norm.txt", raw.encode("utf-8"))
    assert "demonstration" in res["text"]
    assert "hyphenation" in res["text"]
    assert "\r" not in res["text"]


# ==============================================================================
# PDF EXTRACTION TESTS (Text, Multi-page, Encrypted, Scanned, Limits)
# ==============================================================================
def test_extract_pdf_single_page():
    pdf_bytes = create_text_pdf(["The Curiosity rover successfully explored Gale Crater on Mars."])
    res = extract_text("rover.pdf", pdf_bytes)
    assert res["file_type"] == "pdf"
    assert "Curiosity rover" in res["text"]
    assert res["page_count"] == 1
    assert res["truncated"] is False


def test_extract_pdf_multi_page_with_page_markers():
    pages = [
        "First page contains trusted premise information.",
        "Second page elaborates on secondary conclusions."
    ]
    pdf_bytes = create_text_pdf(pages)
    res = extract_text("multi.pdf", pdf_bytes, insert_page_markers=True)
    assert res["file_type"] == "pdf"
    assert res["page_count"] == 2
    assert "[Page 1]" in res["text"]
    assert "[Page 2]" in res["text"]
    assert "First page contains" in res["text"]
    assert "Second page elaborates" in res["text"]


def test_extract_pdf_scanned_image_only_raises_422():
    # A PDF with empty pages and no text layer simulates a scanned PDF
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    scanned_bytes = buf.getvalue()

    with pytest.raises(ScannedPdfError) as exc_info:
        extract_text("scanned.pdf", scanned_bytes)
    assert exc_info.value.status_code == 422
    assert "Scanned or image-only" in exc_info.value.message


def test_extract_pdf_encrypted_raises_clear_error():
    # Encrypt a PDF with a password
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret_password")
    buf = io.BytesIO()
    writer.write(buf)
    enc_bytes = buf.getvalue()

    with pytest.raises(ExtractionError) as exc_info:
        extract_text("encrypted.pdf", enc_bytes)
    assert "Encrypted or password-protected" in exc_info.value.message


def test_extract_pdf_page_limit_exceeded():
    pages = [f"Page content {i} with sufficient text length to avoid scanned detection." for i in range(5)]
    pdf_bytes = create_text_pdf(pages)
    # Set cap to 3 pages
    with pytest.raises(ExtractionError) as exc_info:
        extract_text("long.pdf", pdf_bytes, max_pdf_pages=3)
    assert "exceeds the maximum limit of 3 pages" in exc_info.value.message


# ==============================================================================
# DOCX EXTRACTION TESTS (Paragraphs, Tables, Headers, Footers, Legacy Formats)
# ==============================================================================
def test_extract_docx_paragraphs_and_tables():
    docx_bytes = create_minimal_docx(
        paragraphs=["Standard operational procedure for data verification.", "Second paragraph detailing safety checks."],
        table_data=[["Metric", "Value"], ["Reliability", "98%"], ["Latency", "120ms"]],
        header_text="CONFIDENTIAL AUDIT REPORT",
        footer_text="Page 1 of Document"
    )
    res = extract_text("report.docx", docx_bytes)
    assert res["file_type"] == "docx"
    assert "CONFIDENTIAL AUDIT REPORT" in res["text"]
    assert "Standard operational procedure" in res["text"]
    assert "Metric | Value" in res["text"]
    assert "Reliability | 98%" in res["text"]
    assert "Page 1 of Document" in res["text"]


def test_extract_docx_rejects_legacy_doc():
    # Mock legacy .doc magic bytes: \xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1
    legacy_doc_data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    with pytest.raises(UnsupportedFileTypeError) as exc:
        extract_text("old.doc", legacy_doc_data)
    assert "Legacy Word documents (.doc) are not supported" in exc.value.message


def test_extract_docx_rejects_macro_docm():
    # Create a zip containing vbaProject.bin to simulate a macro file
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<document/>")
        zf.writestr("word/vbaProject.bin", b"fake macro binary")
    macro_data = buf.getvalue()

    with pytest.raises(UnsupportedFileTypeError) as exc:
        extract_text("malicious.docm", macro_data)
    assert "Macro-enabled Word documents (.docm) are not allowed" in exc.value.message


def test_extract_zip_not_docx_rejected():
    # A standard zip file with random files inside
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("image.png", b"fake png data")
        zf.writestr("notes.txt", b"some notes")
    fake_docx = buf.getvalue()

    with pytest.raises(UnsupportedFileTypeError) as exc:
        extract_text("not_a_doc.docx", fake_docx)
    assert "Zip archive is not a valid DOCX document" in exc.value.message


# ==============================================================================
# PIPELINE LENGTH CAP & TRUNCATION WARNINGS
# ==============================================================================
def test_truncation_warning_on_long_text():
    # Generate text longer than 20,000 characters
    long_content = "This is a verification sentence that will be repeated many times. " * 400
    assert len(long_content) > 20_000
    res = extract_text("massive.txt", long_content.encode("utf-8"), max_length_cap=20_000)
    assert res["truncated"] is True
    assert len(res["text"]) == 20_000
    assert len(res["warnings"]) == 1
    assert "exceeds the pipeline limit of 20,000 characters" in res["warnings"][0]


# ==============================================================================
# MISMATCHED EXTENSIONS & CORRUPT DATA
# ==============================================================================
def test_mismatched_extension_detected_by_magic_bytes():
    # A text file renamed to .pdf
    fake_pdf = b"This is clearly not a PDF file header."
    with pytest.raises(CorruptFileError) as exc:
        extract_text("fake.pdf", fake_pdf)
    assert "File has .pdf extension but does not contain valid PDF header" in exc.value.message


def test_corrupt_pdf_header_raises_corrupt():
    corrupt_pdf = b"%PDF-1.4\ncorrupt gibberish with no xref table"
    with pytest.raises(CorruptFileError):
        extract_text("broken.pdf", corrupt_pdf)
