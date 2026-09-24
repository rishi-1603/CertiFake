# Base image PINNED to a specific Debian release (Day 3 fix). It used to be
# an unpinned `python:3.11-slim`, whose Debian base silently moved from
# bookworm to trixie -- and trixie renamed/removed three of the apt
# packages listed below, so this image stopped building at all:
#   libgl1-mesa-glx  -> libgl1            (removed in trixie)
#   libmagic1        -> libmagic1t64      (64-bit time_t transition)
#   libglib2.0-0     -> libglib2.0-0t64   (64-bit time_t transition)
# Verified against a real trixie host via `apt-cache policy`: all three old
# names have no installation candidate there. This was caught by the
# docker-build CI job added the same day -- the first time this Dockerfile
# had ever actually been built by anything.
#
# Pinning matters beyond this one incident: an unpinned `-slim` tag means a
# Debian release upgrade can break this build months from now with no
# change to this repo. Explicit release + names valid on it = reproducible.
FROM python:3.11-slim-trixie

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0t64 \
    poppler-utils \
    libmagic1t64 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

# docker-compose.yml overrides this per-service (api-gateway runs uvicorn,
# worker-ocr/worker-forensics run their respective `python -m app.workers.*`
# modules) -- this default is only used if the image is run standalone.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
