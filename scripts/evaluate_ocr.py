"""Tiny synthetic OCR field smoke evaluation; not a general accuracy benchmark.

Run from repository root: python scripts/evaluate_ocr.py
Requires the backend requirements and the Tesseract executable. No external
certificate or personal data is used. Printed per-case results are reproducible
only for the same OCR/font/runtime versions; variants are intentionally small.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.ocr import extract_fields, run_ocr  # noqa: E402

EXPECTED = {
    'name': 'Jane A Doe',
    'certificate_no': 'CF-2026-000123',
    'date': '2026-09-28',
    'institution': 'Global Institute of Technology',
}
LINES = ['CERTIFICATE OF COMPLETION', 'Name: Jane A Doe',
         'Certificate ID: CF-2026-000123', 'Date: 2026-09-28',
         'Institution: Global Institute of Technology']


def make_image():
    image = Image.new('RGB', (1500, 700), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(LINES):
        draw.text((80, 75 + i * 70), line, font=font, fill='black')
    return image


def main():
    total_match = total_missing = total_error = 0
    variants = [('upright', lambda i: i),
                ('rotated_90', lambda i: i.rotate(90, expand=True, fillcolor='white')),
                ('rotated_3', lambda i: i.rotate(3, expand=True, fillcolor='white'))]
    with tempfile.TemporaryDirectory() as directory:
        for name, transform in variants:
            path = Path(directory) / f'{name}.png'
            transform(make_image()).save(path)
            try:
                observed = extract_fields(run_ocr(str(path), 'image/png'))
                matched = sum(observed.get(k) == v for k, v in EXPECTED.items())
                missing = sum(not observed.get(k) for k in EXPECTED)
                print(f'{name}: exact={matched}/4; missing={missing}/4')
                total_match += matched
                total_missing += missing
            except Exception as exc:
                total_error += 1
                total_missing += 4
                print(f'{name}: processing error={type(exc).__name__}')
    print(f'total: documents={len(variants)}, fields={len(variants) * len(EXPECTED)}, exact={total_match}, missing={total_missing}, failed_documents={total_error}')
    print('Synthetic, three-document demonstration; no confidence calibration or real-world accuracy inference.')


if __name__ == '__main__':
    main()
