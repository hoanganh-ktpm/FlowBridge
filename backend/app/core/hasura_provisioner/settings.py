from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HasuraSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_HASURA_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    endpoint: str = "http://127.0.0.1:8080"
    admin_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    timeout_seconds: float = Field(default=10, gt=0, le=120, allow_inf_nan=False)
    allowed_sources: frozenset[str] = frozenset()
    allowed_database_hosts: frozenset[str] = frozenset()
    secret_bindings: dict[str, str] = Field(default_factory=dict)
    select_limit: int = Field(default=1000, ge=1, le=10000)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
            if parsed.scheme == "http" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "hasura",
            }:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use an HTTPS origin or loopback HTTP origin") from None
        return value.rstrip("/")
