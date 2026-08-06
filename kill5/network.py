from __future__ import annotations

from contextlib import contextmanager
import gzip
import random
import re
import shutil
import ssl
import subprocess
import threading
import time
from collections.abc import Callable, Hashable, Iterator
from typing import TypeVar
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .errors import CrawlError, ErrorCode, classify_exception


REQUEST_RETRIES = 5
HOST_MIN_INTERVAL = 0.7
PLACEHOLDER_PAGE_MARKERS = (
    "Welcome to OpenResty!",
    "Further configuration is required",
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST_REQUEST: dict[str, float] = {}
_HOST_LOCKS_GUARD = threading.Lock()

def create_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


SSL_CONTEXT = create_ssl_context()
T = TypeVar("T")


class ResponseCache:
    def __init__(self) -> None:
        self._values: dict[Hashable, object] = {}
        self._inflight: dict[Hashable, threading.Event] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: Hashable, loader: Callable[[], T]) -> T:
        while True:
            with self._lock:
                if key in self._values:
                    return self._values[key]  # type: ignore[return-value]
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    is_owner = True
                else:
                    is_owner = False

            if is_owner:
                try:
                    value = loader()
                except BaseException:
                    with self._lock:
                        self._inflight.pop(key, None)
                        event.set()
                    raise
                with self._lock:
                    self._values[key] = value
                    self._inflight.pop(key, None)
                    event.set()
                return value

            event.wait()


_REQUEST_SCOPE_LOCK = threading.Lock()
_ACTIVE_RESPONSE_CACHE: ResponseCache | None = None


@contextmanager
def request_scope(cache: ResponseCache | None = None) -> Iterator[ResponseCache]:
    global _ACTIVE_RESPONSE_CACHE

    with _REQUEST_SCOPE_LOCK:
        if _ACTIVE_RESPONSE_CACHE is not None:
            raise RuntimeError("同一进程不能同时开启多个抓取响应缓存范围")
        active = cache or ResponseCache()
        _ACTIVE_RESPONSE_CACHE = active
    try:
        yield active
    finally:
        with _REQUEST_SCOPE_LOCK:
            _ACTIVE_RESPONSE_CACHE = None


def host_key(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def host_lock(url: str) -> threading.Lock:
    key = host_key(url)
    with _HOST_LOCKS_GUARD:
        lock = _HOST_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _HOST_LOCKS[key] = lock
        return lock


def wait_for_host_slot(url: str) -> None:
    key = host_key(url)
    with host_lock(url):
        elapsed = time.monotonic() - _HOST_LAST_REQUEST.get(key, 0.0)
        wait_time = HOST_MIN_INTERVAL - elapsed
        if wait_time > 0:
            time.sleep(wait_time + random.uniform(0.05, 0.25))
        _HOST_LAST_REQUEST[key] = time.monotonic()


def is_retryable_network_error(exc: BaseException) -> bool:
    _code, retryable = classify_exception(exc)
    return retryable


def is_openresty_placeholder(data: bytes) -> bool:
    text = data.decode("utf-8", errors="ignore")
    return all(marker in text for marker in PLACEHOLDER_PAGE_MARKERS)


def is_openresty_placeholder_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, CrawlError)
        and exc.evidence.get("placeholder") is True
    )


def origin_key(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def ensure_same_origin(actual_url: str, expected_url: str) -> None:
    actual_key = origin_key(actual_url)
    expected_key = origin_key(expected_url)
    if not actual_key or not expected_key or actual_key != expected_key:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            f"请求最终来源与目标站点不一致：目标 {expected_url}，实际 {actual_url}",
            stage="network_redirect",
        )


def curl_fetch_bytes(
    url: str,
    timeout: int = 25,
    allow_insecure_tls: bool = False,
) -> bytes:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise CrawlError(
            ErrorCode.HTTP_FAILURE,
            "curl 不可用",
            stage="network_curl",
        )

    cmd = [
        curl,
        "--location",
        "--fail-with-body",
        "--silent",
        "--show-error",
        "--http1.1",
        "--ssl-no-revoke",
    ]
    if allow_insecure_tls:
        cmd.append("--insecure")
    cmd.extend([
        "--connect-timeout",
        str(min(15, timeout)),
        "--max-time",
        str(timeout),
        "--retry",
        "2",
        "--retry-delay",
        "2",
        "--retry-all-errors",
        "--write-out",
        "\n__CRAWLER_FINAL_URL__:%{url_effective}",
        "-A",
        HEADERS["User-Agent"],
        "-H",
        f"Accept: {HEADERS['Accept']}",
        "-H",
        f"Accept-Language: {HEADERS['Accept-Language']}",
        "-H",
        "Accept-Encoding: identity",
        url,
    ])
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if not stderr:
            stderr = proc.stdout.decode("utf-8", errors="replace").strip()[:300]
        if proc.returncode == 28:
            code = ErrorCode.NETWORK_TIMEOUT
        elif proc.returncode in {35, 51, 53, 58, 59, 60, 64, 66, 77, 80, 82, 83, 90, 91}:
            code = ErrorCode.SSL_FAILURE
        else:
            code = ErrorCode.HTTP_FAILURE
        raise CrawlError(
            code,
            f"curl exit {proc.returncode}: {stderr}",
            stage="network_curl",
            retryable=proc.returncode in {
                5, 6, 7, 18, 28, 35, 47, 52, 55, 56,
                51, 53, 58, 59, 60, 64, 66, 77, 80, 82, 83, 90, 91,
            },
            evidence={"curl_exit_code": proc.returncode},
        )
    marker = b"\n__CRAWLER_FINAL_URL__:"
    if marker not in proc.stdout:
        raise CrawlError(
            ErrorCode.DOCUMENT_BOUNDARY_ERROR,
            "curl 未返回最终来源 URL，已停止避免接受未知来源响应",
            stage="network_curl",
        )
    data, final_url = proc.stdout.rsplit(marker, 1)
    ensure_same_origin(final_url.decode("utf-8", errors="replace").strip(), url)
    return data


def fetch_bytes(
    url: str,
    timeout: int = 25,
    allow_insecure_tls: bool = False,
) -> bytes:
    last_error = None
    use_insecure_tls = allow_insecure_tls and urlparse(url).scheme.lower() == "https"
    context = ssl._create_unverified_context() if use_insecure_tls else SSL_CONTEXT
    for attempt in range(REQUEST_RETRIES):
        try:
            wait_for_host_slot(url)
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=timeout, context=context) as resp:
                ensure_same_origin(resp.geturl(), url)
                data = resp.read()
                if is_openresty_placeholder(data):
                    raise CrawlError(
                        ErrorCode.HTTP_FAILURE,
                        "服务器返回 OpenResty 默认占位页",
                        stage="network_response",
                        retryable=True,
                        evidence={"placeholder": True},
                    )
                return data
        except Exception as exc:
            last_error = exc
            if not is_retryable_network_error(exc):
                break
            if attempt < REQUEST_RETRIES - 1:
                delay = min(8.0, 0.9 * (2 ** attempt)) + random.uniform(0.2, 0.8)
                time.sleep(delay)
    if last_error and is_retryable_network_error(last_error) and not is_openresty_placeholder_error(last_error):
        try:
            wait_for_host_slot(url)
            data = curl_fetch_bytes(
                url,
                timeout=timeout,
                allow_insecure_tls=use_insecure_tls,
            )
            if is_openresty_placeholder(data):
                raise CrawlError(
                    ErrorCode.HTTP_FAILURE,
                    "服务器返回 OpenResty 默认占位页",
                    stage="network_response",
                    retryable=True,
                    evidence={"placeholder": True},
                )
            return data
        except Exception as curl_exc:
            original_code, original_retryable = classify_exception(last_error)
            curl_code, curl_retryable = classify_exception(curl_exc)
            code = (
                ErrorCode.SSL_FAILURE
                if ErrorCode.SSL_FAILURE in {original_code, curl_code}
                else ErrorCode.NETWORK_TIMEOUT
                if ErrorCode.NETWORK_TIMEOUT in {original_code, curl_code}
                else original_code
            )
            raise CrawlError(
                code,
                f"{last_error}; curl 连接失败: {curl_exc}",
                stage="network_fallback",
                retryable=original_retryable or curl_retryable,
                evidence={"curl_error_code": curl_code.value},
            ) from curl_exc
    if last_error is not None:
        raise last_error
    raise CrawlError(
        ErrorCode.HTTP_FAILURE,
        "网络请求没有返回结果",
        stage="network_request",
    )


def decode_response(data: bytes, forced_encoding: str | None = None) -> str:
    if not data:
        return ""
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)

    if forced_encoding:
        return data.decode(forced_encoding, errors="replace")

    head = data[:3000].decode("ascii", errors="ignore")
    meta = re.search(r"charset=[\"']?([A-Za-z0-9_-]+)", head, re.I)
    encodings = []
    if meta:
        encodings.append(meta.group(1))
    encodings += ["utf-8", "gb18030", "big5", "latin1"]

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
        except LookupError:
            continue
    return data.decode("utf-8", errors="ignore")


def fetch_text(
    url: str,
    timeout: int = 25,
    encoding: str | None = None,
    allow_insecure_tls: bool = False,
) -> str:
    def load() -> str:
        return decode_response(
            fetch_bytes(
                url,
                timeout=timeout,
                allow_insecure_tls=allow_insecure_tls,
            ),
            forced_encoding=encoding,
        )

    cache = _ACTIVE_RESPONSE_CACHE
    if cache is None:
        return load()
    key = ("text", url, timeout, encoding, allow_insecure_tls)
    return cache.get_or_load(key, load)


__all__ = [
    'REQUEST_RETRIES',
    'HOST_MIN_INTERVAL',
    'PLACEHOLDER_PAGE_MARKERS',
    'HEADERS',
    'create_ssl_context',
    'host_key',
    'host_lock',
    'wait_for_host_slot',
    'is_retryable_network_error',
    'is_openresty_placeholder',
    'is_openresty_placeholder_error',
    'origin_key',
    'ensure_same_origin',
    'curl_fetch_bytes',
    'fetch_bytes',
    'decode_response',
    'fetch_text',
    'ResponseCache',
    'request_scope',
]
