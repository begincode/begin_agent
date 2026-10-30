class ModelGatewayError(Exception):
    pass


class ProviderNotConfiguredError(ModelGatewayError):
    pass


class ProviderRequestError(ModelGatewayError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        code: str = "PROVIDER_ERROR",
        provider_code: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.provider_code = provider_code
        self.retryable = retryable


class InvalidModelRequest(ProviderRequestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, 422, code="INVALID_REQUEST")
