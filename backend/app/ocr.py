import os
import re
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps

try:
    from pdf2image import convert_from_path
    PDF_AVAILABLE = True
except Exception:
    convert_from_path = None
    PDF_AVAILABLE = False

if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def _preprocess_image(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    # Removed MedianFilter for performance
    return img

def _ocr_image(img: Image.Image) -> str:
    config = "--oem 3 --psm 6"
    return pytesseract.image_to_string(img, config=config, timeout=30)

def run_ocr(path: str, content_type: str) -> str:
    p = Path(path)
    if content_type == "application/pdf":
        if not PDF_AVAILABLE:
            raise RuntimeError("PDF rendering is not available")
        pages = convert_from_path(str(p), dpi=150, first_page=1, last_page=1, timeout=30)
        if not pages:
            raise ValueError("PDF first page could not be rendered")
        img = pages[0]
    else:
        with Image.open(str(p)) as source:
            img = source.convert("RGB")
    img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    prepared = _preprocess_image(img)
    initial = _ocr_image(prepared).strip()
    # Extra work only when the first orientation recovers fewer than two
    # supported labels. This is a heuristic, not orientation certainty.
    if len(extract_fields(initial)) >= 2:
        return initial
    candidates = [initial]
    for angle in (90, 270):
        candidates.append(_ocr_image(prepared.rotate(angle, expand=True)).strip())
    return max(candidates, key=lambda text: (len(extract_fields(text)), len(text)))


def extract_fields(text: str):
    """Extract four explicitly labelled, single-line values; no inferred truth."""
    labels = {
        "certificate_no": r"certificate[ \t]*(?:no\.?|number|id)",
        "name": r"name",
        "date": r"date",
        "institution": r"(?:institution|university|college|institute|academy|school|board|organization)",
    }
    fields = {}
    for key, label in labels.items():
        pattern = rf"^[ \t]*{label}[ \t]*[:\-][ \t]*([^\r\n]{{1,200}})"
        match = re.search(pattern, text or "", re.IGNORECASE | re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    return fields
