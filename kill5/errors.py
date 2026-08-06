from __future__ import annotations

from enum import Enum
from http.client import IncompleteRead, RemoteDisconnected
import socket
import ssl
from typing import Any
from urllib.error import HTTPError, URLError


class ErrorCode(str, Enum):
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    SSL_FAILURE = "SSL_FAILURE"
    HTTP_FAILURE = "HTTP_FAILURE"
    LOCAL_NETWORK_POLICY = "LOCAL_NETWORK_POLICY"
    CONTENT_NOT_PUBLISHED = "CONTENT_NOT_PUBLISHED"
    ARTICLE_ID_MISMATCH = "ARTICLE_ID_MISMATCH"
    ANCHOR_MISSING = "ANCHOR_MISSING"
    ISSUE_NOT_FOUND = "ISSUE_NOT_FOUND"
    KEYWORD_MISMATCH = "KEYWORD_MISMATCH"
    NUMBER_INVALID = "NUMBER_INVALID"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    CANDIDATE_CONFLICT = "CANDIDATE_CONFLICT"
    DOCUMENT_BOUNDARY_ERROR = "DOCUMENT_BOUNDARY_ERROR"
    ADAPTER_MISMATCH = "ADAPTER_MISMATCH"
    BROWSER_RENDER_FAILED = "BROWSER_RENDER_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    CACHE_COMMIT_FAILED = "CACHE_COMMIT_FAILED"


class CrawlError(ValueError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        stage: str | None = None,
        retryable: bool = False,
        evidence: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.evidence = dict(evidence or {})


def classify_exception(exc: BaseException) -> tuple[ErrorCode, bool]:
    if isinstance(exc, CrawlError):
        return exc.code, exc.retryable
    if isinstance(exc, HTTPError):
        return ErrorCode.HTTP_FAILURE, exc.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, ssl.SSLError):
        return ErrorCode.SSL_FAILURE, True
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ErrorCode.NETWORK_TIMEOUT, True
    if isinstance(
        exc,
        (
            ConnectionError,
            IncompleteRead,
            RemoteDisconnected,
            socket.gaierror,
        ),
    ):
        return ErrorCode.HTTP_FAILURE, True
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return classify_exception(reason)
        return ErrorCode.HTTP_FAILURE, True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 10013:
            return ErrorCode.LOCAL_NETWORK_POLICY, False
        return ErrorCode.HTTP_FAILURE, True
    return ErrorCode.OUTPUT_VALIDATION_FAILED, False
