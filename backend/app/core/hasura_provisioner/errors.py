class HasuraError(Exception):
    code = "hasura_error"

    def __init__(self) -> None:
        super().__init__(self.code)


class HasuraUnavailable(HasuraError):
    code = "hasura_unavailable"


class HasuraTimeout(HasuraUnavailable):
    code = "hasura_timeout"


class HasuraRejected(HasuraError):
    code = "hasura_rejected"


class MetadataConflict(HasuraError):
    code = "metadata_conflict"


class InvalidMetadata(HasuraError):
    code = "invalid_metadata"


class ProvisioningDenied(HasuraError):
    code = "provisioning_denied"


class InvalidConfiguration(HasuraError):
    code = "invalid_configuration"


class ContractRejected(HasuraError):
    code = "contract_rejected"
