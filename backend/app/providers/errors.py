"""Provider errors, importable without the optional SDKs installed."""


class ClaudeError(RuntimeError):
    pass


class ClaudeAccountError(ClaudeError):
    """The key, model or account cannot be used: every later call fails too, so the video must stop."""
