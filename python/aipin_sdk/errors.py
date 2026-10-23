# -*- coding: utf-8 -*-
"""Exception hierarchy."""


class XinliqiuError(Exception):
    """Base error for the SDK."""


class NotAuthenticated(XinliqiuError):
    """Credentials are missing or unusable."""


class ProbeFailed(XinliqiuError):
    """The underlying transport could not complete the operation."""

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class Unsupported(XinliqiuError):
    """The requested capability is not reachable on this device/firmware."""


class ContainerError(XinliqiuError):
    """A CMPT container or enpt envelope is malformed."""


class DecryptError(XinliqiuError):
    """Owner-key decryption failed (wrong key or corrupted payload)."""
