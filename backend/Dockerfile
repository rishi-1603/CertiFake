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
# The base image ships whatever pip and setuptools Debian packaged, and NEITHER
# appears in requirements.txt -- so no pin in that file can reach them, and a
# dependency scan run against the CI runner's Python cannot see them either.
# This is measured, not guessed: scanning this image from the inside in CI (the
# docker-build job) found pip 24.0 carrying 7 advisories (PYSEC-2026-1795,
# -1796, -196, -2875, -2876, -3721) and setuptools 79.0.1 carrying
# PYSEC-2026-3447. Nothing in requirements.txt was vulnerable; the artifact was.
#
# Upgrading BEFORE the install below is not cosmetics. Several of those
# advisories concern how pip extracts wheels and tarballs and how it resolves
# console_script paths -- i.e. they apply to the very next command, which pulls
# dependencies from the network at build time.
#
# Python 3.11's ensurepip bundles setuptools; 3.12's does not, which is why the
# two sibling Dockerfiles (both 3.12-based) upgrade pip only.
RUN pip install --no-cache-dir --upgrade "pip>=26.2.0" "setuptools>=83.0.0"
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

# docker-compose.yml overrides this per-service (api-gateway runs uvicorn,
# worker-ocr/worker-forensics run their respective `python -m app.workers.*`
# modules) -- this default is only used if the image is run standalone.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
