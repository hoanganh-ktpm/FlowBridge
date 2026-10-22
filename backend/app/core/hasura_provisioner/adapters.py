import os
from collections.abc import Mapping

from pydantic import ValidationError

from .errors import InvalidConfiguration
from .schemas import PostgresConnection


class EnvironmentSecretResolver:
    def __init__(
        self, bindings: Mapping[str, str], environ: Mapping[str, str] | None = None
    ) -> None:
        self._bindings = dict(bindings)
        self._environ = os.environ if environ is None else environ

    async def resolve_postgres(self, secret_ref: str) -> PostgresConnection:
        variable = self._bindings.get(secret_ref)
        if variable is None or not self._environ.get(variable):
            raise InvalidConfiguration()
        try:
            return PostgresConnection(
                environment_name=variable, database_url=self._environ[variable]
            )
        except ValidationError:
            raise InvalidConfiguration() from None
