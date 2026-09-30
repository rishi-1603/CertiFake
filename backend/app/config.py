from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 signs with the secret as a raw HMAC key. RFC 7518 section 3.2 requires
# the key to be at least as long as the hash output -- 32 bytes for SHA-256 --
# and PyJWT warns below that. A short key is not a style problem: it makes the
# signature brute-forceable offline from any single captured token.
MIN_SECRET_KEY_BYTES = 32


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

    @field_validator("secret_key")
    @classmethod
    def _secret_key_must_be_long_enough(cls, value: str) -> str:
        """Refuse to start with a signing key short enough to brute-force (S15).

        Requiring the field (above) stops a *missing* secret; this stops a *weak*
        one, which is the failure mode that survives a checklist because
        everything appears to work.

        Unlike the sibling DevTrack and Repay-Master, this check is
        UNCONDITIONAL rather than production-only. That asymmetry is deliberate:
        both siblings are (or are intended to be) deployed, and DevTrack is live
        on Render right now, so a rule that fails unconditionally there could
        take a running service down over a development key. CertiFake is not
        deployed anywhere -- no cluster has ever run these manifests -- so the
        stricter rule costs nothing today and is the right one the day it is
        deployed. Every path that loads this module already supplies a long
        enough key: CI uses a 37-character throwaway, conftest a 40-character
        one, and .env.example documents generating a 64-character hex string.
        """
        length = len(value.encode("utf-8"))
        if length < MIN_SECRET_KEY_BYTES:
            raise ValueError(
                f"secret_key is {length} bytes; at least {MIN_SECRET_KEY_BYTES} are required, "
                "because HS256 uses it directly as an HMAC key (RFC 7518 3.2) and a shorter key "
                "can be brute-forced offline from any single captured token. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return value

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
