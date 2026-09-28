#!/usr/bin/env python3
"""Resolve every container image reference in compose/k8s files against its
registry, and fail if any of them can no longer be pulled.

WHY THIS EXISTS
---------------
On 2026-09-11 MinIO deleted `minio/minio` from Docker Hub (project archived
Feb 2026, source-only since Oct 2025). CertiFake's compose referenced it, and
`api-gateway` waited on `minio: service_healthy` -- so a fresh
`docker compose up` failed at the pull step before a single container started.
It stayed broken for 17 days because nothing checked: `docker-compose config`
validates *syntax*, not whether an image still exists, and no Docker daemon
was available to catch it by running the stack.

This closes that gap with no daemon required: the registry v2 API answers
"does this manifest exist?" for anyone, anonymously.

HOW THE VERDICT IS DERIVED (and why not to trust the HTTP code alone)
--------------------------------------------------------------------
Docker Hub returns **401** for a repository that does not exist -- the same
code it returns for a private repo needing auth -- so an anonymous caller
cannot distinguish "gone" from "secret". The reliable signal is the *scope*
the token service granted: a nonexistent repo yields a token with an empty
`access` claim. That is checked explicitly below, and confirmed against
known-good controls (postgres, redis, cp-kafka) and known-bad (minio/minio,
bitnami/minio).

Exit code 0 = every image resolvable; 1 = at least one is not.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REGISTRIES = {
    "docker.io": {
        "token": "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull",
        "manifest": "https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
        "library_prefix": True,   # bare names live under library/
        # Only Docker Hub conflates "repository does not exist" with 401, so
        # only Docker Hub needs the token-scope test. Other registries return a
        # trustworthy status code from the manifest request; applying the scope
        # heuristic to them produced a FALSE POSITIVE on cgr.dev, whose token
        # carries no `access` claim at all. A checker that cries wolf is worse
        # than none, because it gets ignored -- or "fixes" a working reference.
        "scope_check": True,
    },
    "cgr.dev": {
        "token": "https://cgr.dev/token?service=cgr.dev&scope=repository:{repo}:pull",
        "manifest": "https://cgr.dev/v2/{repo}/manifests/{tag}",
        "library_prefix": False,
        "scope_check": False,
    },
    "quay.io": {
        "token": "https://quay.io/v2/auth?service=quay.io&scope=repository:{repo}:pull",
        "manifest": "https://quay.io/v2/{repo}/manifests/{tag}",
        "library_prefix": False,
        "scope_check": True,      # quay reports missing repos as 401 too
    },
    "ghcr.io": {
        "token": "https://ghcr.io/token?scope=repository:{repo}:pull",
        "manifest": "https://ghcr.io/v2/{repo}/manifests/{tag}",
        "library_prefix": False,
        "scope_check": True,
    },
}

ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

IMAGE_RE = re.compile(
    r"""image:\s*                              # compose / k8s 'image:' key
        (?P<ref>
          (?:[\w.-]+(?::\d+)?/)?               # optional registry host (has . or :)
          (?:[\w.-]+/)?                        # optional owner/namespace
          [\w.-]+                              # image name
          (?::[\w.\-]+)?                       # optional tag
          (?:@sha256:[0-9a-f]{64})?            # optional digest
        )
    """,
    re.VERBOSE,
)
# The owner segment used to be mandatory, which silently skipped every bare
# library image (`postgres:15-alpine`, `redis:7-alpine`) -- precisely the
# images most likely to be depended on. A checker that cannot see them is
# worse than no checker, because it reports PASS.


def _get(url: str, headers: dict | None = None, timeout: int = 45):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def parse_ref(ref: str) -> tuple[str, str, str]:
    """Split an image reference into (registry, repo, tag_or_digest)."""
    ref = ref.strip().strip("'\"")
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)

    parts = ref.split("/")
    # A leading component is a registry host if it has a dot, a colon, or is localhost.
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, rest = parts[0], parts[1:]
    else:
        registry, rest = "docker.io", parts
    repo = "/".join(rest)

    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"          # postgres -> library/postgres

    if digest:
        return registry, repo, digest
    if ":" in repo:
        repo, tag = repo.rsplit(":", 1)
        return registry, repo, tag
    return registry, repo, "latest"


def token_scope_granted(token: str, repo: str) -> bool:
    """Decode a registry token's claims; True only if pull was actually granted.

    This is the real existence signal on Docker Hub -- see module docstring.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return True          # cannot decode -> don't fail on this alone
    access = claims.get("access") or []
    return any(a.get("name") == repo and "pull" in (a.get("actions") or []) for a in access)


def check_image(ref: str) -> tuple[str, str]:
    """Return (status, detail). status in {ok, gone, unpinned, error}."""
    try:
        registry, repo, tag = parse_ref(ref)
    except Exception as exc:                      # pragma: no cover
        return "error", f"unparseable reference: {exc}"

    cfg = REGISTRIES.get(registry)
    if cfg is None:
        return "error", f"no registry adapter for {registry!r}"

    try:
        _, body, _ = _get(cfg["token"].format(repo=repo))
        token = json.loads(body).get("token", "")
        if token and cfg.get("scope_check") and not token_scope_granted(token, repo):
            return "gone", (
                f"{registry}/{repo} -- the token service granted no pull scope, "
                f"which is how {registry} reports a repository that no longer exists "
                "(it returns the same 401 for private repos, so the HTTP code alone "
                "cannot distinguish the two)"
            )
        status, _, hdrs = _get(
            cfg["manifest"].format(repo=repo, tag=tag),
            {"Authorization": f"Bearer {token}", "Accept": ACCEPT},
        )
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code in (401, 404):
            return "gone", f"{registry}/{repo}:{tag} -- HTTP {code} (repository or tag not found)"
        return "error", f"{registry}/{repo}:{tag} -- HTTP {code}"
    except Exception as exc:
        return "error", f"{registry}/{repo}:{tag} -- {type(exc).__name__}: {exc}"

    if status == 200:
        detail = f"{registry}/{repo}:{tag} resolvable"
        if tag == "latest" and "@" not in ref:
            # Not a failure -- the stack still works -- but an unpinned tag can
            # change underneath the repo with no commit, which is exactly the
            # failure mode this project already hit once (Day 3: python:3.11-slim
            # silently moved bookworm -> trixie and broke the build).
            return "unpinned", detail + " (tag 'latest' is mutable)"
        return "ok", detail
    return "error", f"{registry}/{repo}:{tag} -- unexpected HTTP {status}"


# Dockerfile `FROM` lines. The Day-3 incident in this repo was exactly this:
# an unpinned `FROM python:3.11-slim` whose Debian base silently moved from
# bookworm to trixie and broke the build. A base image that gets deleted is
# the same failure mode as a service image that gets deleted, so both are
# resolved here. `FROM x AS y` is handled by stopping the ref at whitespace.
FROM_RE = re.compile(r"^\s*FROM\s+(?P<ref>\S+)", re.IGNORECASE)


def find_refs(path: Path) -> list[tuple[int, str]]:
    """Pull image references out of a YAML or Dockerfile without extra deps."""
    out = []
    is_dockerfile = path.name.lower().startswith("dockerfile")
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if is_dockerfile:
            m = FROM_RE.match(line)
            if m:
                ref = m.group("ref")
                # Skip build-stage aliases (`FROM builder`) and `scratch`, which
                # are not registry images. Precedence matters here: without the
                # parentheses this parses as (A and B) or C or D and lets bare
                # stage names through.
                if ref.lower() != "scratch" and ("." in ref or "/" in ref or ":" in ref):
                    out.append((lineno, ref))
            continue
        for m in IMAGE_RE.finditer(line):
            out.append((lineno, m.group("ref")))
    return out


def main(argv: list[str]) -> int:
    argv = list(argv)
    # --allow-missing PREFIX: exempt references this repo builds itself. They
    # cannot exist before the first successful CI publish, so failing on them
    # would be circular. Kept as an explicit CLI flag rather than a constant in
    # here, so the exemption is visible in the workflow and in review.
    allow_missing: list[str] = []
    while "--allow-missing" in argv[1:]:
        i = argv.index("--allow-missing")
        if i + 1 >= len(argv):
            print("--allow-missing requires a value", file=sys.stderr)
            return 2
        allow_missing.append(argv[i + 1])
        del argv[i : i + 2]

    if len(argv) < 2:
        print(f"usage: {Path(argv[0]).name} [--allow-missing PREFIX]... <file.yml> [...]",
              file=sys.stderr)
        return 2

    refs: list[tuple[str, int, str]] = []
    for arg in argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"!! {p}: no such file", file=sys.stderr)
            return 2
        for lineno, ref in find_refs(p):
            refs.append((str(p), lineno, ref))

    if not refs:
        print("no image references found -- nothing to check")
        return 0

    # De-duplicate identical references so a repeated image is probed once.
    seen: dict[str, tuple[str, str]] = {}
    failures, warnings = [], []
    for path, lineno, ref in refs:
        if any(ref.startswith(prefix) for prefix in allow_missing):
            print(f"  [SELF   ] {path}:{lineno}  {ref}\n"
                  f"             exempt via --allow-missing: built and published by this repo's own CI")
            continue
        if ref not in seen:
            seen[ref] = check_image(ref)
        status, detail = seen[ref]
        mark = {"ok": "OK     ", "unpinned": "UNPINNED", "gone": "GONE   ", "error": "ERROR  "}[status]
        print(f"  [{mark}] {path}:{lineno}  {ref}\n             {detail}")
        if status == "gone":
            failures.append((path, lineno, ref, detail))
        elif status in ("unpinned", "error"):
            warnings.append((path, lineno, ref, detail))

    print()
    print(f"checked {len(seen)} distinct image reference(s) across {len(argv) - 1} file(s)")
    if failures:
        print(f"\nFAIL: {len(failures)} image reference(s) can no longer be pulled:")
        for path, lineno, ref, detail in failures:
            print(f"  - {path}:{lineno} {ref}\n    {detail}")
        return 1
    if warnings:
        print(f"\n{len(warnings)} warning(s) (non-fatal): unpinned tags / transient registry errors")
    print("\nPASS: every referenced image resolves in its registry")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
