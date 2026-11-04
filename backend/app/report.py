from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def _wrap(text, width, font="Helvetica", size=10):
    words = (text or "").split()
    lines = []
    line = ""
    for w in words:
        test = f"{line} {w}".strip()
        if stringWidth(test, font, size) <= width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines

def create_report(path: str, data: dict):
    c = canvas.Canvas(path, pagesize=A4)
    w, h = A4

    c.setFillColor(HexColor("#0b1220"))
    c.rect(0, 0, w, h, fill=1, stroke=0)

    c.setFillColor(HexColor("#68e1fd"))
    c.setFont("Helvetica-Bold", 22)
    c.drawString(42, h - 52, "CertiFake Pro Report")

    c.setFillColor(HexColor("#eaf2ff"))
    c.setFont("Helvetica", 11)

    y = h - 90
    items = [
        ("File Name", data.get("file_name", "-")),
        ("User", data.get("user", "-")),
        ("Verdict", data.get("verdict", "-")),
        ("Signals", data.get("signals", "-")),
        ("Limit", "Signals do not prove authenticity or fraud."),
    ]

    for k, v in items:
        c.setFillColor(HexColor("#9fb2cc"))
        c.drawString(42, y, f"{k}:")
        c.setFillColor(HexColor("#eaf2ff"))
        c.drawString(130, y, str(v))
        y -= 24

    fields = data.get("fields") or {}
    for key in ("name", "certificate_no", "date", "institution"):
        value = str(fields.get(key, "Not extracted"))[:100]
        c.setFillColor(HexColor("#9fb2cc"))
        c.drawString(42, y, f"{key}:")
        c.setFillColor(HexColor("#eaf2ff"))
        c.drawString(155, y, value)
        y -= 18

    c.setFillColor(HexColor("#9fb2cc"))
    c.drawString(42, y - 4, "OCR Preview:")
    y -= 22

    c.setFont("Helvetica", 9)
    c.setFillColor(HexColor("#dce7f7"))
    for line in _wrap(data.get("ocr_preview", ""), 500, size=9)[:32]:
        c.drawString(42, y, line)
        y -= 13
        if y < 60:
            c.showPage()
            y = h - 50

    c.save()
