FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1-mesa-glx \
    libglib2.0-0 \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

# docker-compose.yml overrides this per-service (api-gateway runs uvicorn,
# worker-ocr/worker-forensics run their respective `python -m app.workers.*`
# modules) -- this default is only used if the image is run standalone.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
