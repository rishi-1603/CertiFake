from PIL import Image
import io
import base64

try:
    import cv2
    import numpy as np
    CV_AVAILABLE = True
except Exception:
    cv2 = None
    np = None
    CV_AVAILABLE = False

def _fallback_score(ocr_text: str, content_type: str):
    score = 45.0
    signals = []
    t = (ocr_text or "").lower()
    if len((ocr_text or "").strip()) < 30:
        score -= 12
        signals.append("Weak OCR text")
    if not any(k in t for k in ["certificate", "degree", "diploma", "university", "college", "issued"]):
        score -= 8
        signals.append("Missing expected certificate keywords")
    if content_type == "application/pdf":
        score += 5
    score = max(0, min(100, score))
    return score, signals, None

def ela_map_from_path(path: str):
    if not CV_AVAILABLE:
        return 0.0, None
    img = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    buf.seek(0)
    comp = Image.open(buf).convert("RGB")
    orig = np.array(img).astype(np.int16)
    cmp = np.array(comp).astype(np.int16)
    diff = np.abs(orig - cmp).astype(np.uint8)
    gray = cv2.cvtColor(diff, cv2.COLOR_RGB2GRAY)
    return float(np.mean(gray)), gray

def score_document(path: str, ocr_text: str, content_type: str):
    if not CV_AVAILABLE:
        return _fallback_score(ocr_text, content_type)

    img = cv2.imread(path)
    if img is None:
        return _fallback_score(ocr_text, content_type)

    signals = []
    score = 50.0

    h, w = img.shape[:2]
    if h < 600 or w < 600:
        score -= 10
        signals.append("Low-resolution upload")

    ela_mean, gray = ela_map_from_path(path)
    if ela_mean > 15:
        score -= 18
        signals.append("Compression inconsistency")

    edges = cv2.Canny(img, 80, 200)
    edge_density = float((edges > 0).mean())
    if edge_density < 0.02:
        score -= 6
        signals.append("Unusual edge pattern")

    text_len = len((ocr_text or "").strip())
    if text_len < 30:
        score -= 15
        signals.append("Weak OCR text")

    lower = (ocr_text or "").lower()
    if not any(k in lower for k in ["certificate", "degree", "diploma", "issued", "university", "college"]):
        score -= 7
        signals.append("Missing expected certificate keywords")

    if content_type == "application/pdf":
        score += 5

    score = max(0, min(100, score))
    return score, signals, gray

def heatmap_b64(gray):
    if not CV_AVAILABLE or gray is None:
        return None
    norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    color = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
    _, buf = cv2.imencode(".png", color)
    return base64.b64encode(buf.tobytes()).decode("utf-8")