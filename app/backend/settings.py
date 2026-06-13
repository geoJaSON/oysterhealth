from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"

    # --- DB components ---
    # URLs are constructed from these so passwords with special chars
    # (%, &, @, #) don't need to be URL-encoded by hand in .env.
    POSTGRES_USER: str = "oyster_user"
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "oyster_health"
    POSTGRES_HOST: str = "db"        # docker network default; override to "localhost" on host dev
    POSTGRES_PORT: int = 5432        # container-internal default; override to 5433 on host dev

    REDIS_URL: str = "redis://redis:6379/0"

    # Comma-separated; parsed below
    CORS_ORIGINS: str = "http://localhost:5173"

    # Auth
    AUTH_REQUIRED: bool = False
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_SERVICE_KEY: str | None = None

    # Copernicus Marine
    CMEMS_USERNAME: str | None = None
    CMEMS_PASSWORD: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def _dsn(self, prefix: str) -> str:
        user = quote(self.POSTGRES_USER, safe="")
        pw = quote(self.POSTGRES_PASSWORD, safe="")
        return (
            f"{prefix}://{user}:{pw}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def database_url(self) -> str:
        """Async URL for SQLAlchemy + asyncpg in the FastAPI app."""
        return self._dsn("postgresql+asyncpg")

    @property
    def database_dsn(self) -> str:
        """Plain libpq DSN for psycopg.connect()."""
        return self._dsn("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
