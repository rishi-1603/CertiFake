"""
CertiFake Pro - Streamlit Edition
Advanced AI Certificate Intelligence & Forensics
"""

import streamlit as st
import cv2
import numpy as np
from PIL import Image, ImageOps, ExifTags
import pytesseract
import io
import os
import re
import uuid
import tempfile
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

try:
    from pdf2image import convert_from_path
    PDF_AVAILABLE = True
except Exception:
    PDF_AVAILABLE = False

try:
    import pyzbar.pyzbar as pyzbar
    QR_AVAILABLE = True
except Exception:
    QR_AVAILABLE = False

st.set_page_config(
    page_title="CertiFake Pro - Certificate Forensics",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main-header { font-size: 2.5rem; font-weight: 800; background: linear-gradient(90deg, #00d4ff, #7b2cbf); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.2rem; }
    .sub-header { color: #8899aa; font-size: 1.05rem; margin-bottom: 1.5rem; }
    .score-card { background: linear-gradient(135deg, #1a1f2e 0%, #0f1724 100%); border-radius: 16px; padding: 1.5rem; border: 1px solid #2a3a4a; text-align: center; }
    .verdict-genuine { color: #00e676; font-weight: 700; font-size: 1.3rem; }
    .verdict-review { color: #ffab00; font-weight: 700; font-size: 1.3rem; }
    .verdict-fake { color: #ff5252; font-weight: 700; font-size: 1.3rem; }
    .signal-box { background: #0d1117; border-left: 3px solid #ff5252; padding: 0.6rem 1rem; margin: 0.4rem 0; border-radius: 0 8px 8px 0; font-size: 0.9rem; }
    .signal-box.positive { border-left-color: #00e676; }
    .metric-label { color: #8899aa; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; }
    .upload-box { border: 2px dashed #3a4a5a; border-radius: 16px; padding: 2rem; text-align: center; background: #0d1117; }
</style>
""", unsafe_allow_html=True)

class ForensicEngine:
    CERTIFICATE_KEYWORDS = [
        "certificate","certification","diploma","degree","transcript",
        "completion","achievement","award","license","credential",
        "university","college","institute","academy","school",
        "board","council","association","organization",
        "issued","date","valid","authorized","accredited",
        "signature","seal","stamp","registrar","principal",
        "chancellor","dean","director","president"
    ]
    SUSPICIOUS_KEYWORDS = [
        "sample","specimen","template","draft","preview",
        "mock","demo","test","fake","copy","replica",
        "unofficial","void","cancelled"
    ]

    def preprocess_image(self, img):
        img = img.convert("L")
        img = ImageOps.autocontrast(img)
        return img

    def run_ocr(self, img):
        return pytesseract.image_to_string(self.preprocess_image(img), config="--oem 3 --psm 6").strip()

    def extract_fields(self, text):
        text = text or ""
        fields = {}
        patterns = {
            "certificate_no": r"(cert(ificate)?\s*(no|num|#|id)?\s*[\:\-]?\s*([A-Z0-9\-\/]{3,}))",
            "name": r"((?:name|awarded\s*to|presented\s*to|recipient)\s*[\:\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}))",
            "date": r"((?:date|issued|dated|on|year)\s*[\:\-]?\s*(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\.,]+\d{1,2}[\s\.,]+\d{4}|\d{4}))",
            "institution": r"((?:university|college|institute|academy|school|board|organization|council)\s*(?:of\s*[A-Za-z]+)?\s*[\:\-]?\s*([A-Z][A-Za-z0-9\s,\.&\-]{3,50}))",
            "course": r"((?:course|program|degree\s*in|major|field)\s*[\:\-]?\s*([A-Z][A-Za-z0-9\s\-]{3,40}))",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                groups = [g for g in m.groups() if g]
                fields[key] = groups[-1].strip() if groups else m.group(0).strip()
        return fields

    def analyze_metadata(self, img):
        meta = {"has_exif": False, "software": None, "created": None, "modified": None, "warnings": []}
        try:
            exif = img._getexif()
            if exif:
                meta["has_exif"] = True
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    if tag == "Software":
                        meta["software"] = str(value)
                        sw = str(value).lower()
                        if any(x in sw for x in ["photoshop","gimp","paint","canva","figma"]):
                            meta["warnings"].append("Image edited with " + str(value))
                    if tag in ["DateTime","DateTimeOriginal"]:
                        meta["created"] = str(value)
            else:
                meta["warnings"].append("No EXIF metadata found -- common in screenshots or web downloads")
        except Exception:
            pass
        return meta

    def error_level_analysis(self, img):
        img_rgb = img.convert("RGB")
        img_rgb.thumbnail((800, 800), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img_rgb.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        comp = Image.open(buf).convert("RGB")
        orig = np.array(img_rgb).astype(np.int16)
        cmp = np.array(comp).astype(np.int16)
        diff = np.abs(orig - cmp).astype(np.uint8)
        gray = cv2.cvtColor(diff, cv2.COLOR_RGB2GRAY)
        ela_mean = float(np.mean(gray))
        ela_max = float(np.max(gray))
        norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        heatmap = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
        return ela_mean, ela_max, heatmap, gray

    def noise_analysis(self, img_cv):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY) if len(img_cv.shape) == 3 else img_cv
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        median = cv2.medianBlur(gray, 5)
        noise = cv2.absdiff(gray, median)
        noise_mean = float(np.mean(noise))
        noise_std = float(np.std(noise))
        h, w = gray.shape
        regions = []
        for i in range(2):
            for j in range(2):
                y1, y2 = i*h//2, (i+1)*h//2
                x1, x2 = j*w//2, (j+1)*w//2
                region = gray[y1:y2, x1:x2]
                reg_noise = float(np.std(cv2.absdiff(region, cv2.medianBlur(region, 5))))
                regions.append(reg_noise)
        return {"laplacian_variance": round(lap_var,2), "noise_mean": round(noise_mean,2),
                "noise_std": round(noise_std,2), "noise_consistency": round(np.std(regions),2),
                "regions": [round(r,2) for r in regions]}

    def edge_analysis(self, img_cv):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY) if len(img_cv.shape) == 3 else img_cv
        edges = cv2.Canny(gray, 80, 200)
        edge_density = float((edges > 0).mean())
        sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        magnitude = np.sqrt(sobelx**2 + sobely**2)
        direction = np.arctan2(sobely, sobelx) * 180 / np.pi
        dir_hist, _ = np.histogram(direction[magnitude > 50], bins=8)
        return {"edge_density": round(edge_density,4), "edge_uniformity": round(float(np.std(dir_hist)),2), "edges": edges}

    def qr_analysis(self, img_cv):
        if not QR_AVAILABLE:
            return {"detected": False, "data": None, "valid": None}
        decoded = pyzbar.decode(img_cv)
        if not decoded:
            return {"detected": False, "data": None, "valid": None}
        qr_data = decoded[0].data.decode("utf-8")
        is_url = qr_data.startswith(("http://","https://"))
        has_suspicious = any(x in qr_data.lower() for x in ["bit.ly","tinyurl","t.ly","short.link"])
        return {"detected": True, "data": qr_data[:200], "is_url": is_url,
                "suspicious_shortener": has_suspicious, "valid": is_url and not has_suspicious}

    def texture_analysis(self, img_cv):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY) if len(img_cv.shape) == 3 else img_cv
        dx = np.abs(np.diff(gray, axis=1))
        dy = np.abs(np.diff(gray, axis=0))
        return {"texture_score": round(float(np.mean(dx)+np.mean(dy))/2, 2),
                "texture_variance": round(float(np.var(dx)+np.var(dy))/2, 2)}

    def text_layout_analysis(self, img_cv, ocr_text):
        gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY) if len(img_cv.shape) == 3 else img_cv
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        text_contours = [c for c in contours if cv2.contourArea(c) > 100]
        if len(text_contours) < 3:
            return {"text_blocks": len(text_contours), "alignment_score": 0, "warnings": ["Too few text blocks detected"]}
        x_starts = sorted([cv2.boundingRect(c)[0] for c in text_contours])
        alignment_score = 20 if len(set(x_starts[:5])) < 3 else 0
        heights = [cv2.boundingRect(c)[3] for c in text_contours]
        warnings = []
        if np.var(heights) > 500:
            warnings.append("High variance in text sizes -- possible font inconsistency")
        return {"text_blocks": len(text_contours), "alignment_score": round(alignment_score,2),
                "height_variance": round(float(np.var(heights)),2), "warnings": warnings}

    def color_analysis(self, img_cv):
        if len(img_cv.shape) == 2:
            return {"color_channels": 1, "color_variance": 0}
        pixels = img_cv.reshape(-1, 3)
        unique_colors = len(np.unique(pixels, axis=0))
        total_pixels = pixels.shape[0]
        color_ratio = unique_colors / total_pixels
        hist_variance = [round(float(np.var(cv2.calcHist([img_cv], [i], None, [256], [0, 256]))), 2) for i in range(3)]
        return {"color_channels": 3, "unique_color_ratio": round(color_ratio, 6),
                "hist_variance": hist_variance,
                "warnings": ["Low color diversity -- possible digital manipulation"] if color_ratio < 0.001 else []}

    def analyze(self, file_bytes, filename, content_type):
        suffix = ".pdf" if content_type == "application/pdf" else ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            if content_type == "application/pdf" and PDF_AVAILABLE:
                pages = convert_from_path(tmp_path, dpi=200)
                pil_img = pages[0].convert("RGB") if pages else Image.new("RGB", (800, 600), "white")
            else:
                pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            pil_img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            img_cv = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            ocr_text = self.run_ocr(pil_img)
            extracted = self.extract_fields(ocr_text)
            metadata = self.analyze_metadata(pil_img)
            ela_mean, ela_max, ela_heatmap, ela_gray = self.error_level_analysis(pil_img)
            noise = self.noise_analysis(img_cv)
            edges = self.edge_analysis(img_cv)
            qr = self.qr_analysis(img_cv)
            texture = self.texture_analysis(img_cv)
            layout = self.text_layout_analysis(img_cv, ocr_text)
            color = self.color_analysis(img_cv)

            score = 50.0
            signals = []
            positive_signals = []
            h, w = img_cv.shape[:2]

            if h < 400 or w < 400:
                score -= 12
                signals.append("Low resolution -- possible screenshot or compressed copy")
            elif h > 1000 and w > 1000:
                score += 5
                positive_signals.append("High resolution document")

            if ela_mean > 20:
                score -= 20
                signals.append("High ELA value (" + str(round(ela_mean,1)) + ") -- significant compression/recompression artifacts")
            elif ela_mean > 12:
                score -= 10
                signals.append("Moderate ELA value (" + str(round(ela_mean,1)) + ") -- possible editing detected")
            elif ela_mean < 5:
                score += 8
                positive_signals.append("Low ELA -- consistent compression, likely original")
            if ela_max > 100:
                score -= 8
                signals.append("Extreme ELA peaks (" + str(round(ela_max,1)) + ") -- localized manipulation likely")

            if edges["edge_density"] < 0.015:
                score -= 10
                signals.append("Unusually low edge density -- possible digital rendering")
            elif edges["edge_density"] > 0.08:
                score += 5
                positive_signals.append("Rich edge detail consistent with scanned document")
            if edges["edge_uniformity"] < 5:
                score -= 6
                signals.append("Unnaturally uniform edge directions -- possible computer-generated")

            text_len = len(ocr_text.strip())
            if text_len < 20:
                score -= 20
                signals.append("Very poor OCR -- unreadable or non-text image")
            elif text_len < 80:
                score -= 10
                signals.append("Limited text extraction -- possible image quality issue")
            elif text_len > 200:
                score += 5
                positive_signals.append("Good text extraction")

            lower_text = ocr_text.lower()
            cert_matches = sum(1 for kw in self.CERTIFICATE_KEYWORDS if kw in lower_text)
            susp_matches = sum(1 for kw in self.SUSPICIOUS_KEYWORDS if kw in lower_text)
            if cert_matches < 3:
                score -= 12
                signals.append("Only " + str(cert_matches) + " certificate keywords found -- may not be a valid certificate")
            elif cert_matches >= 6:
                score += 8
                positive_signals.append("Rich certificate vocabulary (" + str(cert_matches) + " keywords)")
            if susp_matches > 0:
                score -= 15
                signals.append("Suspicious keywords detected: " + str([k for k in self.SUSPICIOUS_KEYWORDS if k in lower_text]))

            if not metadata["has_exif"]:
                score -= 5
                signals.append("No EXIF metadata -- common in edited or web-downloaded images")
            else:
                score += 3
                positive_signals.append("EXIF metadata present")
            for w in metadata["warnings"]:
                score -= 8
                signals.append(w)

            if noise["noise_consistency"] > 15:
                score -= 10
                signals.append("Inconsistent noise patterns across regions -- possible composite image")
            elif noise["noise_consistency"] < 3:
                score += 5
                positive_signals.append("Consistent noise patterns")
            if noise["laplacian_variance"] < 50:
                score -= 8
                signals.append("Image appears heavily blurred or smoothed")

            if qr["detected"]:
                if qr["suspicious_shortener"]:
                    score -= 15
                    signals.append("QR code uses suspicious URL shortener")
                elif qr["valid"]:
                    score += 5
                    positive_signals.append("Valid QR code with direct URL detected")
                else:
                    score -= 3
                    signals.append("QR code detected but content is unclear")

            for w in layout["warnings"]:
                score -= 5
                signals.append(w)
            if layout["text_blocks"] < 5:
                score -= 5
                signals.append("Very few text regions -- unusual for a certificate")
            elif layout["text_blocks"] > 15:
                score += 3
                positive_signals.append("Complex layout with many text regions")

            for w in color["warnings"]:
                score -= 6
                signals.append(w)
            if texture["texture_variance"] < 10:
                score -= 5
                signals.append("Abnormally uniform texture -- possible digital generation")
            if content_type == "application/pdf":
                score += 3
                positive_signals.append("PDF format -- higher authenticity probability")

            score = max(0, min(100, score))
            if score >= 75:
                verdict, verdict_class = "Likely Genuine", "verdict-genuine"
            elif score >= 50:
                verdict, verdict_class = "Needs Review", "verdict-review"
            else:
                verdict, verdict_class = "Likely Fake", "verdict-fake"

            return {
                "file_name": filename, "content_type": content_type,
                "authenticity_score": round(score, 2), "verdict": verdict,
                "verdict_class": verdict_class, "confidence": round(score/100.0, 2),
                "ocr_text": ocr_text[:3000], "extracted_fields": extracted,
                "suspicious_signals": signals, "positive_signals": positive_signals,
                "metadata": metadata,
                "ela": {"mean": ela_mean, "max": ela_max, "heatmap": ela_heatmap, "gray": ela_gray},
                "noise": noise, "edges": edges, "qr": qr, "texture": texture,
                "layout": layout, "color": color,
                "dimensions": {"width": w, "height": h},
                "analysis_timestamp": datetime.now().isoformat(),
            }
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    def generate_pdf_report(self, result):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        c.setFillColor(HexColor("#0b1220"))
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColor(HexColor("#68e1fd"))
        c.setFont("Helvetica-Bold", 22)
        c.drawString(42, h-52, "CertiFake Pro -- Forensic Report")
        c.setFillColor(HexColor("#8899aa"))
        c.setFont("Helvetica", 9)
        c.drawString(42, h-68, "Generated: " + str(result["analysis_timestamp"]))
        c.drawString(42, h-80, "File: " + str(result["file_name"]))
        score = result["authenticity_score"]
        score_color = "#00e676" if score >= 75 else "#ffab00" if score >= 50 else "#ff5252"
        c.setFillColor(HexColor(score_color))
        c.setFont("Helvetica-Bold", 36)
        c.drawString(42, h-130, str(score) + "/100")
        c.setFillColor(HexColor("#eaf2ff"))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(160, h-122, result["verdict"])
        c.setFont("Helvetica", 10)
        y = h - 170
        sections = [("Dimensions", str(result["dimensions"]["width"]) + " x " + str(result["dimensions"]["height"]) + " px"),
                    ("OCR Length", str(len(result["ocr_text"])) + " chars"),
                    ("ELA Mean", str(round(result["ela"]["mean"], 2))),
                    ("ELA Max", str(round(result["ela"]["max"], 2))),
                    ("Edge Density", str(round(result["edges"]["edge_density"], 4))),
                    ("Noise Consistency", str(round(result["noise"]["noise_consistency"], 2))),
                    ("Text Blocks", str(result["layout"]["text_blocks"])),
                    ("QR Detected", "Yes" if result["qr"]["detected"] else "No")]
        for label, value in sections:
            c.setFillColor(HexColor("#9fb2cc"))
            c.drawString(42, y, label + ":")
            c.setFillColor(HexColor("#eaf2ff"))
            c.drawString(160, y, str(value))
            y -= 18
        y -= 10
        c.setFillColor(HexColor("#68e1fd"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(42, y, "Extracted Fields")
        y -= 18
        c.setFont("Helvetica", 10)
        for key, value in result["extracted_fields"].items():
            c.setFillColor(HexColor("#9fb2cc"))
            c.drawString(42, y, key.title() + ":")
            c.setFillColor(HexColor("#eaf2ff"))
            c.drawString(160, y, str(value)[:60])
            y -= 16
        y -= 10
        c.setFillColor(HexColor("#ff5252"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(42, y, "Suspicious Signals")
        y -= 18
        c.setFont("Helvetica", 9)
        c.setFillColor(HexColor("#dce7f7"))
        for sig in result["suspicious_signals"]:
            c.drawString(42, y, "- " + str(sig)[:90])
            y -= 13
        if result["positive_signals"]:
            y -= 10
            c.setFillColor(HexColor("#00e676"))
            c.setFont("Helvetica-Bold", 12)
            c.drawString(42, y, "Positive Indicators")
            y -= 18
            c.setFont("Helvetica", 9)
            c.setFillColor(HexColor("#dce7f7"))
            for sig in result["positive_signals"]:
                c.drawString(42, y, "+ " + str(sig)[:90])
                y -= 13
        y -= 10
        c.setFillColor(HexColor("#8899aa"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(42, y, "OCR Preview")
        y -= 18
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor("#b0c4de"))
        for line in result["ocr_text"].split("\n")[:40]:
            c.drawString(42, y, line[:100])
            y -= 11
        c.save()
        buf.seek(0)
        return buf.getvalue()


def render_header():
    st.markdown("""<div class="main-header">CertiFake Pro</div>""", unsafe_allow_html=True)
    st.markdown("""<div class="sub-header">Advanced AI Certificate Intelligence &amp; Forensics -- Detect counterfeit credentials with pixel-level analysis</div>""", unsafe_allow_html=True)
    st.markdown("---")


def render_sidebar():
    with st.sidebar:
        st.markdown("### Analysis Settings")
        st.markdown("**Detection Sensitivity**")
        sensitivity = st.slider("Sensitivity", 0.5, 1.5, 1.0, 0.1, help="Higher values make detection more strict")
        st.markdown("**Analysis Modules**")
        modules = {
            "ela": st.checkbox("Error Level Analysis", value=True),
            "noise": st.checkbox("Noise Pattern Analysis", value=True),
            "edge": st.checkbox("Edge Detection", value=True),
            "qr": st.checkbox("QR Code Verification", value=True),
            "metadata": st.checkbox("EXIF Metadata", value=True),
            "texture": st.checkbox("Texture Analysis", value=True),
        }
        st.markdown("---")
        st.markdown("### About")
        st.info("CertiFake Pro uses forensic image analysis to detect compression artifacts, noise inconsistencies, edge pattern anomalies, metadata tampering, and text/layout irregularities. Note: This tool provides probabilistic assessment, not legal proof.")
    return sensitivity, modules


def render_upload():
    st.markdown("""<div class="upload-box">""", unsafe_allow_html=True)
    uploaded = st.file_uploader("Drop certificate image or PDF here", type=["png","jpg","jpeg","webp","pdf"], help="Supported: PNG, JPG, WEBP, PDF (first page)", label_visibility="collapsed")
    st.markdown("""</div>""", unsafe_allow_html=True)
    return uploaded


def render_score_card(result):
    score = result["authenticity_score"]
    verdict = result["verdict"]
    vclass = result["verdict_class"]
    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col1:
        color = "#00e676" if score >= 75 else "#ffab00" if score >= 50 else "#ff5252"
        html = f'<div class="score-card"><div class="metric-label">Authenticity Score</div><div style="font-size: 3rem; font-weight: 800; color: {color};">{score}</div><div style="font-size: 0.9rem; color: #8899aa;">out of 100</div></div>'
        st.markdown(html, unsafe_allow_html=True)
    with col2:
        html = f'<div class="score-card"><div class="metric-label">Verdict</div><div class="{vclass}">{verdict}</div><div style="font-size: 0.85rem; color: #8899aa; margin-top: 0.5rem;">Confidence: {round(result["confidence"]*100, 0)}%</div></div>'
        st.markdown(html, unsafe_allow_html=True)
    with col3:
        fname = result["file_name"][:25] + ("..." if len(result["file_name"]) > 25 else "")
        ctype = result["content_type"].split("/")[-1].upper()
        html = f'<div class="score-card"><div class="metric-label">File Info</div><div style="font-size: 0.9rem; color: #eaf2ff;">{fname}</div><div style="font-size: 0.8rem; color: #8899aa; margin-top: 0.3rem;">{result["dimensions"]["width"]} x {result["dimensions"]["height"]} px<br>{ctype}</div></div>'
        st.markdown(html, unsafe_allow_html=True)


def render_signals(result):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Suspicious Signals")
        if result["suspicious_signals"]:
            for sig in result["suspicious_signals"]:
                st.markdown(f'<div class="signal-box">{sig}</div>', unsafe_allow_html=True)
        else:
            st.success("No suspicious signals detected!")
    with col2:
        st.markdown("### Positive Indicators")
        if result["positive_signals"]:
            for sig in result["positive_signals"]:
                st.markdown(f'<div class="signal-box positive">{sig}</div>', unsafe_allow_html=True)
        else:
            st.info("No strong positive indicators found.")


def render_visualizations(result):
    st.markdown("### Forensic Visualizations")
    tabs = st.tabs(["ELA Heatmap", "Edge Map", "Noise Map", "Original"])
    with tabs[0]:
        if result["ela"]["heatmap"] is not None:
            st.image(cv2.cvtColor(result["ela"]["heatmap"], cv2.COLOR_BGR2RGB), caption=f"ELA (mean: {round(result['ela']['mean'], 2)}, max: {round(result['ela']['max'], 2)})", use_container_width=True)
            st.caption("Red/Yellow = potential manipulation")
        else:
            st.info("ELA heatmap not available")
    with tabs[1]:
        if result["edges"]["edges"] is not None:
            st.image(result["edges"]["edges"], caption=f"Edge Detection (density: {round(result['edges']['edge_density'], 4)})", use_container_width=True)
        else:
            st.info("Edge map not available")
    with tabs[2]:
        if result["ela"]["gray"] is not None:
            st.image(result["ela"]["gray"], caption="Noise/ELA Gray Map", use_container_width=True)
        else:
            st.info("Noise map not available")
    with tabs[3]:
        st.image(st.session_state.get("uploaded_image"), caption="Original Upload", use_container_width=True)


def render_extracted_fields(result):
    st.markdown("### Extracted Fields")
    if result["extracted_fields"]:
        cols = st.columns(min(len(result["extracted_fields"]), 4))
        for idx, (key, value) in enumerate(result["extracted_fields"].items()):
            with cols[idx % len(cols)]:
                st.metric(label=key.replace("_", " ").title(), value=str(value)[:30])
    else:
        st.warning("No structured fields could be extracted from the document.")


def render_technical_details(result):
    with st.expander("Technical Analysis Details"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**Noise Analysis**")
            st.json({"Laplacian Variance": result["noise"]["laplacian_variance"], "Noise Mean": result["noise"]["noise_mean"], "Noise Std": result["noise"]["noise_std"], "Consistency": result["noise"]["noise_consistency"]})
        with col2:
            st.markdown("**Edge & Texture**")
            st.json({"Edge Density": result["edges"]["edge_density"], "Edge Uniformity": result["edges"]["edge_uniformity"], "Texture Score": result["texture"]["texture_score"], "Texture Variance": result["texture"]["texture_variance"]})
        with col3:
            st.markdown("**Color & Layout**")
            st.json({"Color Channels": result["color"]["color_channels"], "Unique Color Ratio": result["color"]["unique_color_ratio"], "Text Blocks": result["layout"]["text_blocks"], "Alignment Score": result["layout"]["alignment_score"]})
        st.markdown("**QR Code Analysis**")
        st.json(result["qr"])
        st.markdown("**Metadata**")
        st.json(result["metadata"])


def render_ocr_preview(result):
    with st.expander("OCR Text Preview"):
        st.text_area("Extracted Text", result["ocr_text"], height=200, label_visibility="collapsed")


def main():
    render_header()
    sensitivity, modules = render_sidebar()
    uploaded_file = render_upload()
    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        filename = uploaded_file.name
        content_type = uploaded_file.type or "application/octet-stream"
        if content_type == "application/pdf" and PDF_AVAILABLE:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            pages = convert_from_path(tmp_path, dpi=150)
            display_img = pages[0].convert("RGB") if pages else Image.new("RGB", (600, 800), "white")
            os.remove(tmp_path)
        else:
            display_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        st.session_state["uploaded_image"] = display_img
        with st.spinner("Running forensic analysis... This may take 10-30 seconds"):
            engine = ForensicEngine()
            result = engine.analyze(file_bytes, filename, content_type)
        st.success("Analysis complete!")
        st.markdown("---")
        render_score_card(result)
        st.markdown("<br>", unsafe_allow_html=True)
        render_signals(result)
        st.markdown("<br>", unsafe_allow_html=True)
        render_extracted_fields(result)
        st.markdown("<br>", unsafe_allow_html=True)
        render_visualizations(result)
        st.markdown("<br>", unsafe_allow_html=True)
        render_technical_details(result)
        render_ocr_preview(result)
        st.markdown("---")
        st.markdown("### Download Report")
        col1, col2 = st.columns(2)
        with col1:
            pdf_bytes = engine.generate_pdf_report(result)
            st.download_button(label="Download PDF Report", data=pdf_bytes,
                               file_name="certifake_report_" + uuid.uuid4().hex[:8] + ".pdf",
                               mime="application/pdf", use_container_width=True)
        with col2:
            import json
            json_data = {k: v for k, v in result.items() if k not in ["ela", "edges"]}
            json_data["ela"] = {"mean": result["ela"]["mean"], "max": result["ela"]["max"]}
            st.download_button(label="Download JSON Data",
                               data=json.dumps(json_data, indent=2, default=str),
                               file_name="certifake_data_" + uuid.uuid4().hex[:8] + ".json",
                               mime="application/json", use_container_width=True)


if __name__ == "__main__":
    main()
