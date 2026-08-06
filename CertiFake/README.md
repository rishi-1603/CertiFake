# CertiFake

Secure certificate authenticity checker with OCR, tamper scoring, and report generation.

## Features
- JWT login
- Secure file upload
- OCR extraction
- Tamper scoring
- Explainable result
- PDF report download
- Docker deployment

## Run locally
1. Copy `.env.example` to `.env`
2. Set secret values
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Install Tesseract OCR on your system
5. Start app:
   ```bash
   uvicorn main:app --reload
   ```

## Docker
```bash
docker compose up --build
```

## Login
Use credentials from `.env`:
- username: `ADMIN_USERNAME`
- password: `ADMIN_PASSWORD`

## Notes
- This version is image-first.
- PDF upload support can be added next.
- For real trust, issuer-side QR or signed verification is recommended.