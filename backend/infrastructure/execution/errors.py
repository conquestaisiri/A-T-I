# backend/infrastructure/execution/errors.py
"""Execution-adapter specific exceptions.

These are infrastructure errors: the live trading guard uses them to fail
safe at the adapter boundary instead of letting a misconfiguration silently
reach a production venue (P0-014). Domain errors live in
``backend/domain/context/errors.py``.
"""


class LiveTradingNotAuthorizedError(RuntimeError):
    """Raised when a non-sandbox gateway would connect without authorization.

    Live execution is never a default. A gateway constructed with
    ``sandbox=False`` must be explicitly authorized by the operator; without
    that authorization the gateway refuses to start, so production capital is
    only reachable by deliberate choice.
    """


class LiveTradingCredentialError(RuntimeError):
    """Raised when a non-sandbox gateway lacks real venue credentials.

    Trading on a production venue with no API key/secret is a misconfiguration.
    Sandbox mode never needs credentials; live mode always does.
    """
