class ProviderError(Exception):
    pass


class ValidationError(Exception):
    pass


class RouterError(ProviderError):
    """A distance/duration matrix request failed.

    ``retryable`` mirrors the address-search distinction: ``True`` means a
    temporary condition (timeout, rate limit, provider outage), so retrying the
    same points later can succeed; ``False`` means the request itself was
    rejected and a retry will not help.
    """

    retryable: bool = True

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def kind(self) -> str:
        return "retryable" if self.retryable else "setup"


class RouterUnavailableError(RouterError):
    """Provider unreachable or answered with a server error (5xx, network)."""

    retryable = True


class RouterTimeoutError(RouterError):
    """The provider did not answer within the configured timeout."""

    retryable = True


class RouterRateLimitedError(RouterError):
    """HTTP 429. Carries the Retry-After value when the provider sent one."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class RouterInvalidResponseError(RouterError):
    """The provider answered, but the body could not be trusted.

    Malformed JSON, a non-``Ok`` status code, a non-square matrix or non-numeric
    cells all land here. Remaining retryable because a bad reply is usually a
    transient proxy/provider condition.
    """

    retryable = True


class RouterTooManyPointsError(RouterError):
    """The provider refused the request because there are too many points."""

    retryable = False


class RouterSetupError(RouterError):
    """The provider rejected the request itself (HTTP 401/403)."""

    retryable = False