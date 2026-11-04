# Benchmarks

**NOT MEASURED:** API p50/p95/p99, end-to-end latency distributions, throughput, concurrent users, worker scaling, database query plans, memory under workload and performance improvements. A single smoke-test completion time is not a benchmark. Run controlled warm-up and repeated workloads with fixed input corpus, concurrency, tool/runtime versions and environment before publishing claims.

The only numerical OCR exercise is documented in [OCR_PIPELINE.md](OCR_PIPELINE.md); it consists of three synthetic variants of one template and is not an accuracy benchmark for real documents. Run `python scripts/evaluate_ocr.py` to repeat on a provisioned Tesseract/Pillow environment.
