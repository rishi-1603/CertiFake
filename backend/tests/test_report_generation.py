"""Unit tests for PDF report generation (app/report.py).

WHY THESE EXIST (Day 7)
-----------------------
When coverage was first measured, app/report.py sat at 12% -- the second-worst
module in the repository, despite being one of the three artifacts the API
serves to clients (the compose smoke test downloads a real report end to end,
but that proves delivery, not generation).

create_report() is a pure function from a dict to a PDF file, so it needs no
fixtures beyond a tmp path and no network. These tests pin:

  * _wrap(): word preservation (nothing dropped or duplicated), the empty and
    single-long-word edges, and that every produced line actually fits the
    width it was wrapped to -- measured with the same stringWidth() the
    function uses, not by eyeballing character counts.
  * create_report(): that a full and a completely empty data dict both yield a
    real PDF (%PDF magic, non-trivial size), so the .get() defaults cannot
    crash generation on a row with missing fields.
  * the pagination branch: a long ocr_preview must produce more than one page.
    Reportlab writes page objects as `/Type /Page`, so counting them is a
    crude but adequate structural check -- deliberately not parsing the PDF
    with a library, which would test the parser rather than this code.

NOT CLAIMED: that the report is visually correct or that its layout is
sensible. These are structural tests; nobody has looked at the rendered PDF in
this pass, and that is stated rather than implied.
"""
from reportlab.pdfbase.pdfmetrics import stringWidth

from app import report

FULL_DATA = {
    "file_name": "certificate.png",
    "user": "smoke@example.com",
    "authenticity_score": 50.0,
    "verdict": "SUSPICIOUS",
    "signals": ["Compression inconsistency", "Weak OCR text"],
    "python_compat": "yes",
    "ocr_preview": "CERTIFICATE OF COMPLETION awarded to Jane A Doe for the "
    "course in Distributed Systems Engineering, issued 2026-09-28.",
}


def _pages(raw: bytes) -> int:
    return raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")


# ---------------------------------------------------------------------------
# _wrap
# ---------------------------------------------------------------------------
def test_wrap_empty_and_none():
    assert report._wrap("", 100) == []
    assert report._wrap(None, 100) == []


def test_wrap_preserves_every_word_exactly_once():
    text = "the quick brown fox jumps over the lazy dog " * 6
    lines = report._wrap(text, 120)
    assert " ".join(lines).split() == text.split()


def test_wrap_single_unbreakable_word_is_not_dropped():
    word = "x" * 500
    assert report._wrap(word, 50) == [word]


def test_every_wrapped_line_fits_the_requested_width():
    text = "certificate degree diploma university completion issued " * 8
    for line in report._wrap(text, 200, size=9):
        assert stringWidth(line, "Helvetica", 9) <= 200


# ---------------------------------------------------------------------------
# create_report
# ---------------------------------------------------------------------------
def test_create_report_full_data_produces_a_real_pdf(tmp_path):
    out = tmp_path / "report.pdf"
    report.create_report(str(out), FULL_DATA)
    raw = out.read_bytes()
    assert raw[:5] == b"%PDF-"
    assert len(raw) > 1000


def test_create_report_with_empty_dict_does_not_crash(tmp_path):
    """A row with every optional field missing must still produce a report;
    the .get() defaults exist precisely for that case."""
    out = tmp_path / "empty.pdf"
    report.create_report(str(out), {})
    assert out.read_bytes()[:5] == b"%PDF-"


def test_ocr_preview_is_truncated_to_32_lines_not_paginated(tmp_path):
    """Documents a real limitation found while writing this test on Day 7.

    The first version of this test asserted that a very long ocr_preview
    paginates, because create_report() contains a `y < 60 -> showPage()`
    branch. It does not paginate: the preview is sliced `[:32]` before the
    drawing loop, and 32 lines at 13px cannot drive y below 60 from its
    starting position. So the page-break branch is unreachable through the
    only input that feeds it, and any OCR text beyond ~32 wrapped lines is
    silently dropped from the PDF.

    Asserting the truncation pins the behaviour as it exists so a future
    change is deliberate. The dead branch itself is recorded under Future
    Improvements in the README rather than fixed here: deciding whether a
    report should paginate or truncate is a product decision, not a test fix.
    """
    long_text = "word " * 4000
    assert len(report._wrap(long_text, 500, size=9)) > 32

    out = tmp_path / "long.pdf"
    report.create_report(str(out), dict(FULL_DATA, ocr_preview=long_text))
    assert _pages(out.read_bytes()) == 1
