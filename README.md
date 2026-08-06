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

## Run Locally

1. Copy `.env.example` to `.env`
2. Set your secret values in `.env`
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Install **Tesseract OCR** on your system.
5. Start the application:

```bash
uvicorn main:app --reload
```

## Docker

```bash
docker compose up --build
```

## Login

Use the credentials defined in your `.env` file:

- **Username:** `ADMIN_USERNAME`
- **Password:** `ADMIN_PASSWORD`

## Notes

- This version is image-first.
- PDF upload support can be added in future versions.
- For stronger certificate verification, issuer-side QR codes or digital signatures are recommended.