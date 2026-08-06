import os
import re
from pathlib import Path
from PIL import Image, ImageOps, ImageFilter
import pytesseract

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
    img = img.filter(ImageFilter.MedianFilter(size=3))
    return img

def _ocr_image(img: Image.Image) -> str:
    config = "--oem 3 --psm 6"
    return pytesseract.image_to_string(img, config=config)

def run_ocr(path: str, content_type: str) -> str:
    p = Path(path)
    if content_type == "application/pdf" and PDF_AVAILABLE:
        pages = convert_from_path(str(p), dpi=220)
        texts = []
        for page in pages[:3]:
            texts.append(_ocr_image(_preprocess_image(page)))
        return "\n".join(texts).strip()
    else:
        img = Image.open(str(p))
        return _ocr_image(_preprocess_image(img)).strip()

def extract_fields(text: str):
    text = text or ""
    fields = {}
    
    patterns = {
        "certificate_no": r"(certificate\s*(no|number|id)\s*[:\-]?\s*([A-Z0-9\-\/]+))",
        "name": r"(name\s*[:\-]?\s*([A-Za-z ,.'-]{3,}))",
        "date": r"(date\s*[:\-]?\s*([0-9A-Za-z,\-/ ]{4,}))",
        "institution": r"(university|college|institute|academy|school|board|organization\s*[:\-]?\s*([A-Za-z0-9 ,.\-]{3,}))"
    }
    
    for key, pat in patterns.items():
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            fields[key] = m.group(m.lastindex).strip() if m.lastindex else m.group(0).strip()
            
    return fields
