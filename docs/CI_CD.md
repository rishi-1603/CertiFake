# CI and delivery

`.github/workflows/ci.yml` defines Python tests/coverage, Ruff, pip-audit, configuration/image checks, Docker image build/security checks, full Compose smoke, React build/lint and container publication on main. The smoke exercises a synthetic upload and two-user access check. This is CI plus image publishing, not a verified deployment of the whole live stack. Exact-commit workflow status must be checked on GitHub. CI's SQLite tests and one Compose smoke do not prove all crash windows or clinical/forensic validity.
