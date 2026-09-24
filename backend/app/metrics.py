"""Prometheus metrics for the API gateway and both Kafka worker processes.

Audit finding fixed here: monitoring/prometheus.yml already existed in
this repository (and so did the `prometheus`/`grafana` services in
docker-compose.yml) and already targeted `api-gateway:8000`,
`worker-ocr:8000`, and `worker-forensics:8000` on Prometheus's default
`/metrics` scrape path -- a config pointing at HTTP endpoints that did not
exist anywhere in the code before this change. This module (plus its use
in app/api.py and both files under app/workers/) is that config's first
real implementation, for all three scrape targets, not just the API
gateway that's easiest to reach.

Deliberately not "instrument every possible thing" -- these metrics
answer the operational questions this system's own already-built
failure-mode handling cares about (see app/api.py's `_rate_limit_analyze`
dependency and the 503-on-storage/broker-outage paths): how many analyses
are requested vs. rejected vs. failed, how long the synchronous part of
/analyze takes, and whether the two async Kafka consumers are keeping up
or falling behind/erroring on their workload.
"""
from prometheus_client import Counter, Histogram

analyze_requests_total = Counter(
    "certifake_analyze_requests_total",
    "Total POST /analyze requests, labeled by outcome.",
    ["outcome"],  # accepted | rate_limited | storage_unavailable | broker_unavailable
)

analyze_request_duration_seconds = Histogram(
    "certifake_analyze_request_duration_seconds",
    "Time spent handling a single POST /analyze request (upload validation, "
    "S3 write, DB insert, Kafka publish) -- does NOT include the async "
    "OCR/forensics processing that happens afterwards in separate workers.",
)

worker_events_processed_total = Counter(
    "certifake_worker_events_processed_total",
    "Kafka events successfully processed by a worker.",
    ["worker"],  # ocr | forensics
)

worker_events_failed_total = Counter(
    "certifake_worker_events_failed_total",
    "Kafka events that raised an exception during processing (the "
    "analysis row is marked status=failed in the same code path).",
    ["worker"],
)
