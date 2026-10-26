from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOWBRIDGE_", env_file=".env", extra="ignore")

    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_introspection_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_ca_bundle: Path | None = None
    oidc_allowed_clients: list[str] = Field(default_factory=list)
    oidc_service_clients: list[str] = Field(default_factory=list)
    oidc_admin_url: str | None = None
    oidc_admin_client_id: str | None = None
    oidc_admin_client_secret: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    trusted_proxy_ips: list[str] = Field(default_factory=list)

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_trusted_proxy_ips(cls, values: list[str]) -> list[str]:
        for value in values:
            if "/" in value:
                if ip_network(value, strict=True).prefixlen == 0:
                    raise ValueError("Proxy trust must not include every address")
            else:
                ip_address(value)
        return values

    @field_validator(
        "oidc_ca_bundle", "oidc_client_secret", "oidc_admin_client_secret", mode="before"
    )
    @classmethod
    def empty_secret_or_path(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("oidc_allowed_clients", "oidc_service_clients")
    @classmethod
    def validate_client_registry(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Client IDs must be nonblank without surrounding whitespace")
        if len(values) != len(set(values)):
            raise ValueError("Client IDs must be unique")
        return values

    @field_validator(
        "oidc_issuer",
        "oidc_audience",
        "oidc_introspection_url",
        "oidc_client_id",
        "oidc_admin_url",
        "oidc_admin_client_id",
        mode="before",
    )
    @classmethod
    def empty_identity_setting(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("oidc_issuer", "oidc_introspection_url", "oidc_admin_url")
    @classmethod
    def validate_identity_url(cls, value: str | None) -> str | None:
        if value is not None:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.netloc
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Identity URLs must be HTTPS without credentials/query/fragment")
            if parts.scheme == "http" and parts.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "keycloak",
            }:
                raise ValueError("Plain HTTP identity URLs are limited to local deployment hosts")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.netloc
                or parts.username
                or parts.password
                or parts.path
                or parts.query
                or parts.fragment
                or "*" in value
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths")
        return values
