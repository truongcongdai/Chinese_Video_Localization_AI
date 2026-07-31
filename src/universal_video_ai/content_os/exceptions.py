"""
Content OS domain exceptions.

Custom exceptions for Content OS workflow and validation errors.
"""


class ContentOSException(Exception):
    """Base exception for Content OS errors."""
    pass


class InvalidTransitionError(ContentOSException):
    """Raised when an invalid workflow state transition is attempted."""
    pass


class ApprovalRequiredError(ContentOSException):
    """Raised when an operation requires approval but none exists."""
    pass


class ArtifactNotFoundError(ContentOSException):
    """Raised when a required artifact is missing."""
    pass


class ArtifactValidationError(ContentOSException):
    """Raised when artifact validation fails."""
    pass


class ProviderUnavailableError(ContentOSException):
    """Raised when a required provider is unavailable."""
    pass


class LLMOutputError(ContentOSException):
    """Raised when LLM output cannot be validated."""
    pass


class RevisionLimitExceededError(ContentOSException):
    """Raised when maximum auto-revision count is exceeded."""
    pass


class FeatureDisabledError(ContentOSException):
    """Raised when Content OS feature is disabled."""
    pass


class WorkflowError(ContentOSException):
    """Raised when workflow execution fails."""
    pass
