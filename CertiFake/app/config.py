from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "CertiFake Pro"
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 60
    upload_dir: str = "data/uploads"
    reports_dir: str = "data/reports"
    max_upload_mb: int = 10
    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    admin_username: str = "admin"
    admin_password: str = "password"
    jwt_algorithm: str = "HS256"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()