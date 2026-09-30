from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CertiFake Pro"

    # No default on purpose (Day-7 remediation, finding S14). This used to read
    #   secret_key: str = "change_me_to_a_long_random_secret"
    # which meant a deployment that forgot SECRET_KEY did not fail -- it silently
    # signed every JWT with a string that is published in this repository, so
    # anyone could mint a valid token for any user. The sibling DevTrack and
    # Repay-Master projects made this field required on Day 3 for exactly this
    # reason; CertiFake was the one that still had the fallback.
    #
    # Making it required turns a forgotten variable into a loud startup failure
    # (pydantic-settings raises ValidationError at import). That is safe here
    # because every path that loads this module already supplies it: the API
    # service in docker-compose.yml uses `${SECRET_KEY:?...}`, the CI test job
    # sets it, and tests/conftest.py sets it before importing app/. The worker
    # containers deliberately do NOT set it -- verified by importing every worker
    # dependency and confirming app.config is never loaded -- because they never
    # touch auth.
    secret_key: str
    access_token_expire_minutes: int = 60
    jwt_algorithm: str = "HS256"

    upload_dir: str = "data/uploads"
    reports_dir: str = "data/reports"
    max_upload_mb: int = 10

    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000,http://localhost:5173"

    # Used by app/rate_limit.py. Matches the REDIS_URL env var already set
    # for every backend container in docker-compose.yml.
    redis_url: str = "redis://localhost:6379/0"
    analyze_rate_limit_per_minute: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
