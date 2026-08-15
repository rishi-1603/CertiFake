# 🔍 CertiFake Pro — Streamlit Edition

**Advanced AI Certificate Intelligence & Forensics**

CertiFake Pro is a forensic analysis tool that detects counterfeit professional certificates and credentials using computer vision, OCR, and pixel-level image analysis.

> **🚀 Live Demo**: Deploy instantly on [Streamlit Cloud](https://streamlit.io/cloud)

---

## ✨ Features

| Module | Description |
|--------|-------------|
| **Error Level Analysis (ELA)** | Detects compression artifacts and re-editing |
| **Noise Pattern Analysis** | Identifies inconsistent noise across image regions |
| **Edge Detection** | Analyzes edge density and directional uniformity |
| **OCR Text Extraction** | Extracts and validates certificate text content |
| **EXIF Metadata** | Checks for editing software signatures |
| **QR Code Verification** | Detects and validates embedded QR codes |
| **Texture Analysis** | Identifies unnatural texture uniformity |
| **Layout Analysis** | Checks text block alignment and consistency |
| **PDF Report Generation** | Exports professional forensic reports |

---

## 🚀 Quick Deploy (Streamlit Cloud)

1. **Fork this repo** or push to your GitHub account
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Set **Main file path** to `streamlit_app.py`
5. Click **Deploy!**

> Streamlit Cloud will automatically install system dependencies from `packages.txt` and Python packages from `requirements.txt`.

---

## 🖥️ Local Development

```bash
# Clone the repo
git clone https://github.com/rishi-1603/CertiFake.git
cd CertiFake

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Install system dependencies (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y tesseract-ocr poppler-utils libzbar0 libgl1

# Run the app
streamlit run streamlit_app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 📊 How It Works

1. **Upload** a certificate image (PNG, JPG, WEBP) or PDF
2. The engine runs **12+ forensic checks** simultaneously:
   - Resolution & quality assessment
   - Error Level Analysis for tampering detection
   - Noise consistency across quadrants
   - Edge pattern analysis
   - OCR text extraction & keyword validation
   - EXIF metadata forensics
   - QR code validation
   - Texture & color analysis
   - Layout & alignment checks
3. Get an **Authenticity Score (0-100)** with detailed reasoning
4. Download a **professional PDF report**

---

## 🏗️ Scoring Logic

| Score | Verdict | Meaning |
|-------|---------|---------|
| 75-100 | **Likely Genuine** | Strong positive indicators, low risk |
| 50-74 | **Needs Review** | Some anomalies detected, manual verification recommended |
| 0-49 | **Likely Fake** | Multiple suspicious signals, high tampering probability |

---

## 📁 File Structure

```
CertiFake/
├── streamlit_app.py          # Main Streamlit application
├── requirements.txt          # Python dependencies
├── packages.txt              # System dependencies (Streamlit Cloud)
├── .streamlit/
│   └── config.toml           # UI theme & server config
├── backend/                  # Original FastAPI backend (legacy)
├── frontend/                 # Original React frontend (legacy)
├── docker-compose.yml        # Full microservices stack (legacy)
└── README.md
```

---

## ⚠️ Disclaimer

This tool provides **probabilistic forensic assessment** based on image analysis heuristics. It is **not** a substitute for:
- Official verification by issuing institutions
- Legal document authentication
- Professional forensic investigation

Always verify certificates directly with the issuing authority.

---

## 📝 License

MIT License — Built with ❤️ for maintaining professional credential integrity.

---

## 🙏 Acknowledgments

- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
- [OpenCV](https://opencv.org/)
- [Streamlit](https://streamlit.io/)
