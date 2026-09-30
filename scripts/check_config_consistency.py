#!/usr/bin/env python3
"""Cross-file consistency checks for deployment configuration.

WHY THIS EXISTS
---------------
Every defect this guards against was actually found in these repositories, and
none of them is visible to a validator that looks at one file at a time:

  * CertiFake's k8s manifests deployed only ONE of the two workers while
    docker-compose.yml ran both. Each file was internally consistent; the pair
    was not. `kubeconform -strict` passed it.
  * CertiFake's workers set MINIO_* but had no `depends_on: minio`, so they
    could start before object storage accepted requests.
  * CertiFake's api-gateway set REDIS_URL with no redis edge at all.
  * monitoring/prometheus.yml scraped worker-ocr:8000 before that worker ran
    any HTTP server, so Prometheus silently scraped nothing and the
    "observable" claim was hollow.
  * k8s referenced a Secret whose keys were documented nowhere, so
    `kubectl apply -f k8s/` could not succeed even given a valid cluster.
  * A Deployment set `replicas: 3` while its HPA declared `minReplicas: 2` --
    two sources of truth, with the HPA winning and the field being dead config.

The common failure mode is DRIFT between files that must agree.

DESIGN NOTE: this script is deliberately generic and identical across the
CertiFake, DevTrack and RepayMaster repositories. Three divergent copies of a
config checker would be the same duplication problem it exists to prevent.
Dependency inference therefore reads the HOSTNAME out of each environment
value (``DATABASE_URL=postgresql://u:p@db:5432/x`` -> ``db``) rather than
assuming a service is called "postgres" -- DevTrack calls it "db" and sets
``REDIS_HOST`` instead of ``REDIS_URL``, and both must work unchanged.

Static analysis only: no Docker daemon, no cluster, no network.
Exit 0 = consistent, 1 = at least one violation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build"}

#: Strings that betray an unfilled template rather than a real image reference.
PLACEHOLDER_PATTERNS = (
    "your-dockerhub-user",
    "your-registry",
    "changeme",
    "example.com",
    "TODO",
    "FIXME",
    "placeholder",
    "xxx",
)

#: Env vars whose VALUE names a host this service must be able to reach.
#: Hostname is extracted from the value, so the service can be called anything.
HOST_BEARING_ENV = (
    "DATABASE_URL",
    "REDIS_URL",
    "REDIS_HOST",
    "KAFKA_BOOTSTRAP_SERVERS",
    "MINIO_ENDPOINT",
    "S3_ENDPOINT",
    "CELERY_BROKER_URL",
    "POSTGRES_HOST",
)

failures: list[str] = []
notes: list[str] = []


def fail(check: str, message: str) -> None:
    failures.append(f"[{check}] {message}")


def note(check: str, message: str) -> None:
    notes.append(f"[{check}] {message}")


def find_files(
    names: tuple[str, ...] = (),
    subdir: str | None = None,
    suffixes: tuple[str, ...] = (),
) -> list[Path]:
    """Locate config files by exact name and/or by extension.

    BUG THIS FIXED: the k8s lookup originally passed (".yaml", ".yml") as
    *names*, so it matched files literally named ".yaml" and found nothing.
    Every k8s check was then silently skipped while the script still printed
    PASS -- the exact failure mode this module's docstring warns about. Name
    and suffix matching are therefore separate parameters, and the caller must
    be explicit about which it wants.
    """
    hits: list[Path] = []
    base = ROOT / subdir if subdir else ROOT
    if not base.exists():
        return hits
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if names and p.name not in names:
            continue
        if suffixes and p.suffix not in suffixes:
            continue
        if not names and not suffixes:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        hits.append(p)
    return hits


def load_docs(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8") as fh:
            return [d for d in yaml.safe_load_all(fh) if isinstance(d, dict)]
    except Exception as exc:
        fail("parse", f"{path}: {type(exc).__name__}: {exc}")
        return []


def env_map(service: dict) -> dict[str, str]:
    """Normalise both compose `environment` forms into a dict."""
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items()}
    out: dict[str, str] = {}
    for item in env:
        s = str(item)
        if "=" in s:
            k, v = s.split("=", 1)
            out[k] = v
        else:
            out[s] = ""     # `- VAR` form: value comes from the host env
    return out


def depends_on_names(service: dict) -> set[str]:
    dep = service.get("depends_on") or {}
    if isinstance(dep, dict):
        return set(dep)
    return {str(d) for d in dep}


def hosts_in_value(value: str) -> set[str]:
    """Pull hostnames out of a connection string or bare host[:port]."""
    if not value:
        return set()
    # Interpolation placeholders resolve at deploy time; nothing to check.
    if "${" in value or value.startswith("$"):
        return set()
    if "://" in value:
        try:
            h = urlparse(value).hostname
        except ValueError:
            return set()
        return {h} if h else set()
    # KAFKA_BOOTSTRAP_SERVERS is host:port[,host:port] with no scheme.
    out = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        out.add(part.rsplit(":", 1)[0] if ":" in part else part)
    return out


def check_compose(path: Path, compose: dict) -> tuple[dict[str, dict], set[str]]:
    """Returns (app_services, all_service_names) after running compose checks."""
    services: dict[str, dict] = compose.get("services") or {}
    if not services:
        fail("compose", f"{path}: no services defined")
        return {}, set()

    if "version" in compose:
        fail("compose", f"{path}: top-level `version:` is obsolete in Compose v2 "
                        f"(it warns on every invocation); remove it")

    # App services are built from this repo; the rest are pulled infrastructure.
    app_services = {n: s for n, s in services.items() if s.get("build")}
    names = set(services)

    for name, svc in services.items():
        image = str(svc.get("image", ""))
        for pat in PLACEHOLDER_PATTERNS:
            if pat.lower() in image.lower():
                fail("placeholders", f"{path}: service {name!r} has placeholder image {image!r}")

    # --- env value names a host => there must be a depends_on edge for it ---
    for name, svc in app_services.items():
        declared = depends_on_names(svc)
        for var, value in env_map(svc).items():
            if var not in HOST_BEARING_ENV:
                continue
            for host in hosts_in_value(value):
                if host not in names:
                    # Points outside this stack (a managed cloud DB, an
                    # external broker). Not a violation -- just not our business.
                    note("depends_on", f"{path}: {name!r} {var} points at {host!r}, which is "
                                       f"not a service in this file (external dependency?)")
                    continue
                if host == name:
                    continue
                if host not in declared:
                    fail("depends_on", f"{path}: service {name!r} sets {var} pointing at "
                                       f"{host!r} but has no depends_on edge for it, so it can "
                                       f"start before that dependency accepts connections")

    # --- anything a healthcheck-gated dependent waits on should have one ---
    for name, svc in services.items():
        for dep, conf in (svc.get("depends_on") or {}).items() if isinstance(svc.get("depends_on"), dict) else []:
            if isinstance(conf, dict) and conf.get("condition") == "service_healthy":
                if dep in services and not services[dep].get("healthcheck"):
                    fail("healthcheck", f"{path}: {name!r} waits on {dep!r} with "
                                        f"condition: service_healthy, but {dep!r} defines no "
                                        f"healthcheck -- it can never become healthy and "
                                        f"{name!r} will never start")
    return app_services, names


def check_k8s(paths: list[Path], app_services: dict[str, dict], readme: str) -> None:
    deployments, hpas = [], []
    for path in paths:
        for doc in load_docs(path):
            kind = doc.get("kind")
            spec = doc.get("spec") or {}
            pod = ((spec.get("template") or {}).get("spec") or {})
            containers = pod.get("containers") or []

            if kind == "Deployment":
                deployments.append((path, doc))
            elif kind == "HorizontalPodAutoscaler":
                hpas.append((path, doc))

            for c in containers:
                image = str(c.get("image", ""))
                for pat in PLACEHOLDER_PATTERNS:
                    if pat.lower() in image.lower():
                        fail("placeholders", f"{path}: {kind} {doc.get('metadata', {}).get('name')!r} "
                                             f"has placeholder image {image!r}")

            # Every Secret key referenced must be documented, or applying the
            # manifests cannot succeed: pods sit in CreateContainerConfigError.
            refs: dict[str, set[str]] = {}
            for c in containers:
                for e in c.get("env") or []:
                    ref = ((e or {}).get("valueFrom") or {}).get("secretKeyRef")
                    if ref:
                        refs.setdefault(ref.get("name", "?"), set()).add(ref.get("key", "?"))
            for secret_name, keys in refs.items():
                if readme and secret_name not in readme:
                    fail("secret-docs", f"{path}: references Secret {secret_name!r} but the README "
                                        f"never mentions it -- nobody applying these manifests "
                                        f"would know to create it")
                    continue
                for key in sorted(keys):
                    if readme and key not in readme:
                        fail("secret-docs", f"{path}: Secret {secret_name!r} key {key!r} is "
                                            f"referenced but not documented in the README's "
                                            f"create-secret command")

    # --- HPA targets must exist and must not fight the Deployment ---
    dep_by_name = {d["metadata"]["name"]: (p, d) for p, d in deployments}
    for path, hpa in hpas:
        target = (hpa.get("spec", {}).get("scaleTargetRef") or {}).get("name")
        if target not in dep_by_name:
            fail("hpa", f"{path}: HPA {hpa['metadata']['name']!r} targets {target!r}, "
                        f"which is not a Deployment defined in these manifests")
            continue
        _, dep = dep_by_name[target]
        if "replicas" in (dep.get("spec") or {}):
            fail("hpa", f"{path}: Deployment {target!r} sets `replicas` while HPA "
                        f"{hpa['metadata']['name']!r} manages it -- every `kubectl apply` "
                        f"would reset the scaled count (two sources of truth)")
        mn = hpa["spec"].get("minReplicas")
        mx = hpa["spec"].get("maxReplicas")
        if mn is not None and mx is not None and mn > mx:
            fail("hpa", f"{path}: HPA {hpa['metadata']['name']!r} has minReplicas {mn} > maxReplicas {mx}")

    # --- k8s should deploy the same app workloads compose builds ---
    if app_services and deployments:
        k8s_roles = set()
        for _, dep in deployments:
            name = dep["metadata"]["name"]
            # strip a project prefix so certifake-worker-ocr -> worker-ocr
            role = re.sub(r"^[a-z0-9]+-(?=(api|worker|web|frontend))", "", name)
            k8s_roles.add(role)
        compose_roles = set(app_services)
        missing = compose_roles - k8s_roles
        if missing:
            fail("k8s-parity", f"compose builds/runs {sorted(missing)} but the k8s manifests have "
                               f"no Deployment for them -- they deploy a subset of the system")
        if not missing:
            note("k8s-parity", f"k8s deployments cover all {len(compose_roles)} compose app workloads")


def check_prometheus(paths: list[Path], services: dict[str, dict]) -> None:
    for path in paths:
        for doc in load_docs(path):
            for job in doc.get("scrape_configs") or []:
                job_name = job.get("job_name", "?")
                for sc in job.get("static_configs") or []:
                    for target in sc.get("targets") or []:
                        host, _, port = str(target).partition(":")
                        if host not in services:
                            fail("prometheus", f"{path}: job {job_name!r} scrapes {target!r} but "
                                               f"{host!r} is not a compose service -- the scrape "
                                               f"can never succeed")
                            continue
                        svc = services[host]
                        blob = yaml.safe_dump(svc, default_flow_style=False)
                        # Inside a compose network the port need not be
                        # published, but it must be something the container
                        # listens on: accept evidence from `ports:` or from the
                        # service's own healthcheck/command mentioning it.
                        if port and port not in blob:
                            fail("prometheus", f"{path}: job {job_name!r} scrapes {host}:{port} but "
                                               f"nothing in the {host!r} service definition "
                                               f"mentions port {port} -- likely nothing is "
                                               f"listening (this is exactly how CertiFake's "
                                               f"workers came to be scraped with no metrics "
                                               f"server at all)")


def check_python_parity() -> None:
    """CI's interpreter must match the interpreter the built images run.

    DRIFT THIS GUARDS AGAINST, from a real red build: numpy was pinned to a
    version that publishes wheels only for Python >=3.12 -- chosen from a local
    3.13 venv, where it installed happily -- while this repo's ci.yml and
    Dockerfile both build on 3.11. Nothing in the repo disagreed with itself, so
    the failure surfaced as `ERROR: No matching distribution found` in a CI test
    job that had passed on every previous commit. The pin was not wrong for the
    machine it was written on; it was wrong for the machine that runs it.

    The mirror-image case is worse and quieter: ci.yml on one minor version and
    the Dockerfile on another, so the suite validates code that production does
    not run. Both are one comparison away from being caught here.
    """
    ci = ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.exists():
        note("python", "no ci.yml -- interpreter parity check skipped")
        return

    ci_versions = set(re.findall(r"python-version:\s*[\"']?(\d+\.\d+)", ci.read_text(encoding="utf-8")))
    image_versions: dict[str, set[str]] = {}
    for dockerfile in find_files(names=("Dockerfile",)):
        found = set(re.findall(r"^\s*FROM\s+python:(\d+\.\d+)",
                               dockerfile.read_text(encoding="utf-8"), re.MULTILINE))
        if found:
            image_versions[str(dockerfile.relative_to(ROOT))] = found

    if not ci_versions:
        note("python", "ci.yml declares no python-version -- parity check skipped")
    if not image_versions:
        note("python", "no python-based Dockerfile -- parity check skipped")
    if not (ci_versions and image_versions):
        return

    every_image = set().union(*image_versions.values())
    if ci_versions != every_image:
        images = ", ".join(f"{k}={sorted(v)}" for k, v in sorted(image_versions.items()))
        fail("python", f"interpreter mismatch: CI runs Python {sorted(ci_versions)}, images run "
                       f"{sorted(every_image)} ({images}). Dependencies resolve differently across "
                       "minor versions, so every pin must be chosen against the interpreter CI and "
                       "the images actually use -- a wheel that exists for one may not exist for the "
                       "other, and the suite would then be validating code production does not run.")
    else:
        note("python", f"CI and every image run Python {sorted(ci_versions)} -- pins are chosen against it")


def main() -> int:
    compose_paths = find_files(("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"))
    k8s_paths = find_files(subdir="k8s", suffixes=(".yaml", ".yml"))
    prom_paths = find_files(("prometheus.yml", "prometheus.yaml"))
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

    if not compose_paths:
        note("files", "no compose file found -- compose checks skipped")

    app_services: dict[str, dict] = {}
    all_services: dict[str, dict] = {}
    for path in compose_paths:
        docs = load_docs(path)
        if not docs:
            continue
        apps, names = check_compose(path, docs[0])
        app_services.update(apps)
        all_services.update(docs[0].get("services") or {})

    if k8s_paths:
        check_k8s(k8s_paths, app_services, readme)
    elif (ROOT / "k8s").is_dir():
        # A k8s/ directory that yields no parseable manifests means the checks
        # silently did nothing -- which is how the suffix bug above went
        # unnoticed while still printing PASS. Fail loudly instead.
        fail("files", "k8s/ directory exists but contains no .yaml/.yml manifests -- "
                      "k8s checks would silently do nothing")
    else:
        note("files", "no k8s/ directory -- k8s checks skipped")

    if prom_paths:
        check_prometheus(prom_paths, all_services)
    else:
        note("files", "no prometheus config -- scrape-target checks skipped")

    check_python_parity()

    checked = [f"{len(compose_paths)} compose", f"{len(k8s_paths)} k8s", f"{len(prom_paths)} prometheus"]
    print(f"  scanned: {', '.join(checked)} file group(s)")
    for n in notes:
        print(f"  note: {n}")
    if failures:
        print()
        for f in failures:
            print(f"  FAIL {f}")
        print(f"\n{len(failures)} consistency violation(s) found")
        return 1
    print("\nPASS: deployment configuration is internally consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
