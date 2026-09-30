"""Direct unit tests for the forensic scoring core (app/forensics.py).

WHY THESE EXIST (Day 7)
-----------------------
When coverage was finally measured in CI on Day 7, the result exposed the most
uncomfortable fact in this repository: the modules that ARE the product were
the least tested. app/forensics.py sat at 13% statement coverage and
app/report.py at 12%, while auth and API plumbing were near-complete. The
compose smoke test does exercise scoring end to end against real tesseract and
real MinIO, but it measures nothing per-branch -- it proves the pipeline runs,
not that the scoring logic behaves.

These tests therefore target the scoring FUNCTIONS directly, with synthetic
images, no tesseract and no network:

  * `_fallback_score` -- every branch (weak OCR, missing keywords, PDF bonus,
    clamping), because it is what runs when cv2 is unavailable or the image
    cannot be read, i.e. the degraded path a real outage hits.
  * `score_document` -- the signal set it emits for each condition, the
    unreadable-image fallback, and the PDF bonus measured as an exact delta
    against the same image and text with a different content type (so the
    assertion isolates that one branch instead of betting on a total).
  * `ela_map_from_path` -- a RELATIVE assertion: an image with a pasted region
    re-saved at low JPEG quality must produce a higher ELA mean than the clean
    original. Absolute ELA thresholds would depend on the JPEG encoder build,
    which is exactly the kind of environment-sensitive assertion that flakes in
    CI; the relative form tests what ELA is actually for.
  * `heatmap_b64` -- that the returned string is base64 of a real PNG.

WHAT THESE TESTS DO NOT CLAIM
-----------------------------
They do not verify that the weights (50 base, -18 for compression
inconsistency, etc.) are *correct* forensics. They are heuristic weights with
their rationale in code; no labelled corpus of real forgeries exists here, and
the README says so. These tests pin the BEHAVIOUR of the heuristics so a future
change cannot silently alter them -- which is a different, weaker, and honest
claim.
"""
import base64

import numpy as np
from PIL import Image

from app import forensics

GOOD_OCR = (
    "CERTIFICATE OF COMPLETION This is to certify that Jane A Doe has "
    "successfully completed the course in Distributed Systems Engineering"
)


def _save_png(img: Image.Image, tmp_path, name="doc.png") -> str:
    p = tmp_path / name
    img.save(p, format="PNG")
    return str(p)


def _clean_image() -> Image.Image:
    """A plausible certificate-like page: white ground, dark text-ish blocks."""
    img = Image.new("RGB", (640, 480), "white")
    px = img.load()
    for y in range(60, 420, 40):
        for x in range(60, 580, 4):
            px[x, y] = (20, 20, 20)
    for x in range(40, 600):
        px[x, 40] = (20, 20, 20)
        px[x, 440] = (20, 20, 20)
    return img


# ---------------------------------------------------------------------------
# _fallback_score: the degraded path
# ---------------------------------------------------------------------------
def test_fallback_penalties_and_signals():
    score, signals, gray = forensics._fallback_score("too short", "image/png")
    assert gray is None
    assert "Weak OCR text" in signals
    assert "Missing expected certificate keywords" in signals
    # 45 base, -12 weak OCR, -8 missing keywords
    assert score == 25.0


def test_fallback_rewards_keywords_and_pdf():
    score, signals, _ = forensics._fallback_score(GOOD_OCR, "application/pdf")
    assert signals == []
    # 45 base, +5 PDF, nothing deducted (text is long and keyworded)
    assert score == 50.0


def test_fallback_clamps_to_valid_range():
    score, _, _ = forensics._fallback_score("", "application/pdf")
    assert 0.0 <= score <= 100.0
    score, _, _ = forensics._fallback_score("certificate " * 50, "application/pdf")
    assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# score_document: signal behaviour on real synthetic images
# ---------------------------------------------------------------------------
def test_unreadable_image_falls_back_identically(tmp_path):
    missing = str(tmp_path / "does-not-exist.png")
    assert forensics.score_document(missing, GOOD_OCR, "image/png") == \
        forensics._fallback_score(GOOD_OCR, "image/png")


def test_clean_certificate_image_produces_no_weak_or_missing_signals(tmp_path):
    path = _save_png(_clean_image(), tmp_path)
    score, signals, gray = forensics.score_document(path, GOOD_OCR, "image/png")
    assert "Weak OCR text" not in signals
    assert "Missing expected certificate keywords" not in signals
    assert "Low-resolution upload" not in signals
    assert gray is not None
    assert 0.0 <= score <= 100.0


def test_small_image_is_flagged_low_resolution(tmp_path):
    path = _save_png(Image.new("RGB", (120, 120), "white"), tmp_path)
    _, signals, _ = forensics.score_document(path, GOOD_OCR, "image/png")
    assert "Low-resolution upload" in signals


def test_weak_ocr_text_is_flagged_on_a_real_image(tmp_path):
    path = _save_png(_clean_image(), tmp_path)
    _, signals, _ = forensics.score_document(path, "ok", "image/png")
    assert "Weak OCR text" in signals


def test_missing_keywords_is_flagged_on_a_real_image(tmp_path):
    path = _save_png(_clean_image(), tmp_path)
    long_but_off_topic = "invoice receipt payment statement ledger " * 3
    _, signals, _ = forensics.score_document(path, long_but_off_topic, "image/png")
    assert "Missing expected certificate keywords" in signals


def test_pdf_bonus_is_exactly_five_points(tmp_path):
    """Same image, same text, only the declared content type differs, so the
    delta isolates the PDF branch from every other signal."""
    path = _save_png(_clean_image(), tmp_path)
    as_png, _, _ = forensics.score_document(path, GOOD_OCR, "image/png")
    as_pdf, _, _ = forensics.score_document(path, GOOD_OCR, "application/pdf")
    assert as_pdf - as_png == 5.0


# ---------------------------------------------------------------------------
# ELA: relative, not absolute
# ---------------------------------------------------------------------------
def test_ela_detects_a_pasted_region_relative_to_clean(tmp_path):
    clean = _clean_image()
    clean_mean, clean_gray = forensics.ela_map_from_path(_save_png(clean, tmp_path, "clean.png"))
    assert clean_gray is not None
    assert clean_mean >= 0.0

    tampered = clean.copy()
    # Paste a flat block, then re-save lossily: the pasted region carries a
    # different compression history, which is precisely what ELA measures.
    tampered.paste(Image.new("RGB", (200, 150), (200, 30, 30)), (220, 160))
    tampered_path = tmp_path / "tampered.jpg"
    tampered.save(tampered_path, format="JPEG", quality=70)
    tampered_mean, _ = forensics.ela_map_from_path(str(tampered_path))

    assert tampered_mean > clean_mean


# ---------------------------------------------------------------------------
# heatmap_b64
# ---------------------------------------------------------------------------
def test_heatmap_b64_returns_a_real_png():
    gray = np.zeros((64, 64), dtype=np.uint8)
    gray[10:20, 10:20] = 255
    encoded = forensics.heatmap_b64(gray)
    assert encoded is not None
    raw = base64.b64decode(encoded)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


def test_heatmap_b64_none_input():
    assert forensics.heatmap_b64(None) is None


def test_score_and_heatmap_roundtrip(tmp_path):
    """The pair the API actually uses: score returns gray, heatmap consumes it."""
    path = _save_png(_clean_image(), tmp_path)
    _, _, gray = forensics.score_document(path, GOOD_OCR, "image/png")
    assert forensics.heatmap_b64(gray) is not None
