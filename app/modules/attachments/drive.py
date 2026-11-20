"""Google Drive v3 over plain HTTP (ADM-027, ADR-0018). No Google SDK.

Hygiene rules (R5/R11): errors are `DriveError(code)` only, raised outside any `except` block
with no cause or context; Google's response text, ids, tokens and URLs are never logged or
copied. Hosts are constants, so the bearer token cannot be sent elsewhere; redirects are refused.
"""

import asyncio
import json
import logging
import math
import random
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings
from app.modules.attachments.errors import DriveError, DriveErrorCode
from app.modules.attachments.store import MAX_ATTACHMENT_BYTES, ImageType, StoredFile

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SERVICE_MARKER = "org-admin-api"
ROOT_FOLDER_NAME = "org-admin-api attachments"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_MAX_ATTEMPTS = 3
_MAX_CONCURRENT_UPLOADS = 4
_REQUEST_TIMEOUT_SECONDS = 60
_CALL_DEADLINE_SECONDS = 45.0  # one Drive call incl. retries and backoff (ADM-027 review)
_TOKEN_DEADLINE_SECONDS = 20.0  # token refresh incl. retries, while holding the lock
_STREAM_DEADLINE_SECONDS = 60.0  # whole body of one streamed read
_MAX_TOKEN_LIFETIME_SECONDS = 3600.0
_BACKOFF_BASE_SECONDS = 0.5
_RETRY_AFTER_CAP_SECONDS = 5.0
_TOKEN_SKEW_SECONDS = 60

_FILE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_APP_KEY_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
_QUOTA_REASONS = frozenset({"storageQuotaExceeded"})
_RATE_REASONS = frozenset({"userRateLimitExceeded", "rateLimitExceeded"})


def _check_file_id(file_id: str) -> None:
    if not isinstance(file_id, str) or _FILE_ID_RE.fullmatch(file_id) is None:
        raise DriveError(DriveErrorCode.INVALID_INPUT)


def _check_app_key(app_key: str) -> None:
    if not isinstance(app_key, str) or _APP_KEY_RE.fullmatch(app_key) is None:
        raise DriveError(DriveErrorCode.INVALID_INPUT)


def _q_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _json_body(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise DriveError(DriveErrorCode.UNAVAILABLE)
    return body


def _error_reasons(resp: httpx.Response) -> set[str]:
    """Reasons from Google's error body; the text is only ever compared, never copied."""
    try:
        body = resp.json()
        errors = body["error"]["errors"]
        return {e["reason"] for e in errors if isinstance(e.get("reason"), str)}
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()


def _classify(resp: httpx.Response) -> DriveErrorCode | None:
    status = resp.status_code
    if 200 <= status < 300:
        return None
    if status == 401:
        return DriveErrorCode.AUTH_FAILED
    if status == 403:
        reasons = _error_reasons(resp)
        if reasons & _QUOTA_REASONS:
            return DriveErrorCode.QUOTA_EXCEEDED
        if reasons & _RATE_REASONS:
            return DriveErrorCode.RATE_LIMITED
        return DriveErrorCode.REJECTED
    if status == 429:
        return DriveErrorCode.RATE_LIMITED
    if status == 404:
        return DriveErrorCode.NOT_FOUND
    if status >= 500:
        return DriveErrorCode.UNAVAILABLE
    return DriveErrorCode.REJECTED  # other 4xx and 3xx (redirects are never followed)


def _backoff(attempt: int, resp: httpx.Response | None) -> float:
    delay: float = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) * (0.5 + random.random())  # noqa: S311
    if resp is not None:
        raw = resp.headers.get("retry-after", "")
        if re.fullmatch(r"[0-9]{1,3}", raw):
            delay = max(delay, min(float(int(raw)), _RETRY_AFTER_CAP_SECONDS))
    return delay


def _log_failure(op: str, code: DriveErrorCode, status: int | None, attempt: int) -> None:
    logger.warning(
        "drive_request_failed op=%s code=%s status=%s attempt=%s", op, code.value, status, attempt
    )


class DriveApi:
    """Access-token cache plus one request wrapper (the only place that talks to Google)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # httpx/httpcore log request URLs at INFO/DEBUG; Drive ids are in them (AC3).
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10),
            follow_redirects=False,
            transport=transport,
        )
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    def invalidate_token(self) -> None:
        self._token = None

    async def _fetch_token(self) -> tuple[DriveErrorCode | None, str | None, float]:
        code: DriveErrorCode | None = None
        resp: httpx.Response | None = None
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
                resp = await self._client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                    },
                )
        except (httpx.HTTPError, TimeoutError):
            code = DriveErrorCode.UNAVAILABLE
        if resp is not None:
            status = resp.status_code
            if status in (400, 401):
                logger.error("drive_auth_failed status=%s", status)
                return DriveErrorCode.AUTH_FAILED, None, 0.0
            if status >= 500 or status == 429:
                code = DriveErrorCode.UNAVAILABLE
            elif status != 200:
                return DriveErrorCode.REJECTED, None, 0.0
            else:
                try:
                    body = resp.json()
                    token = body["access_token"]
                    expires_in = float(body.get("expires_in", 3600))
                except (ValueError, KeyError, TypeError, AttributeError):
                    return DriveErrorCode.UNAVAILABLE, None, 0.0
                if not math.isfinite(expires_in):
                    return DriveErrorCode.UNAVAILABLE, None, 0.0
                expires_in = min(max(expires_in, 0.0), _MAX_TOKEN_LIFETIME_SECONDS)
                if not isinstance(token, str) or not token:
                    return DriveErrorCode.UNAVAILABLE, None, 0.0
                return None, token, expires_in
        return code, None, 0.0

    async def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        async with self._lock:
            if self._token is not None and time.monotonic() < self._expires_at:
                return self._token
            code: DriveErrorCode | None = DriveErrorCode.UNAVAILABLE
            timed_out = False
            try:
                async with asyncio.timeout(_TOKEN_DEADLINE_SECONDS):
                    code, token = await self._refresh_with_retries()
            except TimeoutError:
                timed_out = True
            if timed_out:
                raise DriveError(DriveErrorCode.UNAVAILABLE)
            if token is not None:
                return token
        raise DriveError(code or DriveErrorCode.UNAVAILABLE)

    async def _refresh_with_retries(self) -> tuple[DriveErrorCode | None, str | None]:
        code: DriveErrorCode | None = DriveErrorCode.UNAVAILABLE
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            code, token, expires_in = await self._fetch_token()
            if code is None and token is not None:
                self._token = token
                self._expires_at = time.monotonic() + max(expires_in - _TOKEN_SKEW_SECONDS, 0)
                return None, token
            if code != DriveErrorCode.UNAVAILABLE:
                break
            _log_failure("token", code, None, attempt)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_backoff(attempt, None))
        return code, None

    async def _attempt(
        self, method: str, url: str, headers: dict[str, str], kw: dict[str, Any], *, stream: bool
    ) -> tuple[httpx.Response | None, DriveErrorCode | None]:
        token = await self._access_token()
        resp: httpx.Response | None = None
        code: DriveErrorCode | None = None
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT_SECONDS):
                request = self._client.build_request(
                    method, url, headers={"Authorization": f"Bearer {token}", **headers}, **kw
                )
                resp = await self._client.send(request, stream=stream)
        except (httpx.HTTPError, TimeoutError):
            code = DriveErrorCode.UNAVAILABLE
        if resp is not None:
            code = _classify(resp)
        return resp, code

    async def _run(
        self,
        op: str,
        method: str,
        url: str,
        *,
        idempotent: bool,
        stream: bool,
        headers: dict[str, str] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        """Return a successful response. Only idempotent calls are retried (creates never are).

        The whole call, retries and backoff included, is bounded by `_CALL_DEADLINE_SECONDS`."""
        timed_out = False
        resp: httpx.Response | None = None
        try:
            async with asyncio.timeout(_CALL_DEADLINE_SECONDS):
                resp = await self._run_loop(
                    op, method, url, idempotent=idempotent, stream=stream, headers=headers, kw=kw
                )
        except TimeoutError:
            timed_out = True
        if timed_out or resp is None:
            _log_failure(op, DriveErrorCode.UNAVAILABLE, None, 0)
            raise DriveError(DriveErrorCode.UNAVAILABLE)
        return resp

    async def _run_loop(
        self,
        op: str,
        method: str,
        url: str,
        *,
        idempotent: bool,
        stream: bool,
        headers: dict[str, str] | None,
        kw: dict[str, Any],
    ) -> httpx.Response:
        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            resp, code = await self._attempt(method, url, headers or {}, kw, stream=stream)
            if code is None and resp is not None:
                return resp
            code = code or DriveErrorCode.UNAVAILABLE
            status = resp.status_code if resp is not None else None
            retry_after = resp
            if resp is not None and stream:
                await resp.aclose()
            if code == DriveErrorCode.AUTH_FAILED:
                self.invalidate_token()
                if idempotent and not refreshed:
                    refreshed = True
                    attempt -= 1
                    continue
            elif (
                idempotent
                and code in (DriveErrorCode.UNAVAILABLE, DriveErrorCode.RATE_LIMITED)
                and attempt < _MAX_ATTEMPTS
            ):
                _log_failure(op, code, status, attempt)
                await asyncio.sleep(_backoff(attempt, retry_after))
                continue
            _log_failure(op, code, status, attempt)
            raise DriveError(code)

    async def request(
        self,
        op: str,
        method: str,
        url: str,
        *,
        idempotent: bool,
        headers: dict[str, str] | None = None,
        **kw: Any,
    ) -> httpx.Response:
        return await self._run(
            op, method, url, idempotent=idempotent, stream=False, headers=headers, **kw
        )

    @asynccontextmanager
    async def stream(self, op: str, url: str, **kw: Any) -> AsyncIterator[httpx.Response]:
        """GET as a stream; retries apply only before the first byte. Always closes."""
        resp = await self._run(op, "GET", url, idempotent=True, stream=True, **kw)
        try:
            yield resp
        finally:
            await resp.aclose()


async def _capped_stream(resp: httpx.Response) -> AsyncIterator[bytes]:
    total = 0
    code: DriveErrorCode | None = None
    deadline = time.monotonic() + _STREAM_DEADLINE_SECONDS
    try:
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > MAX_ATTACHMENT_BYTES:
                code = DriveErrorCode.TOO_LARGE
                break
            if time.monotonic() > deadline:
                code = DriveErrorCode.UNAVAILABLE
                break
            yield chunk
    except (httpx.HTTPError, TimeoutError):
        code = DriveErrorCode.UNAVAILABLE
    if code is not None:
        raise DriveError(code)


def _multipart(metadata: dict[str, Any], content_type: str, data: bytes) -> tuple[str, bytes]:
    boundary = secrets.token_hex(16)
    head = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n"
    ).encode()
    return f"multipart/related; boundary={boundary}", head + data + f"\r\n--{boundary}--".encode()


def _parse_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


class GoogleDriveStore:
    configured = True

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        client_id = settings.google_drive_client_id
        secret = settings.google_drive_client_secret
        refresh = settings.google_drive_refresh_token
        root = settings.google_drive_root_folder_id
        if not (client_id and secret and refresh and root):
            raise DriveError(DriveErrorCode.NOT_CONFIGURED)
        self._api = DriveApi(
            client_id, secret.get_secret_value(), refresh.get_secret_value(), transport
        )
        self._root = root.get_secret_value()
        self._folders: dict[str, str] = {}
        self._folder_locks: dict[str, asyncio.Lock] = {}
        self._uploads = asyncio.Semaphore(_MAX_CONCURRENT_UPLOADS)

    async def aclose(self) -> None:
        await self._api.aclose()

    @property
    def api(self) -> DriveApi:
        return self._api

    async def _paged(
        self, op: str, q: str, fields: str, order_by: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            params = {
                "q": q,
                "fields": f"nextPageToken,files({fields})",
                "pageSize": "100",
            }
            if order_by:
                params["orderBy"] = order_by
            if page_token:
                params["pageToken"] = page_token
            resp = await self._api.request(op, "GET", FILES_URL, idempotent=True, params=params)
            body = _json_body(resp)
            files = body.get("files", [])
            if not isinstance(files, list):
                raise DriveError(DriveErrorCode.UNAVAILABLE)
            for item in files:
                if isinstance(item, dict):
                    yield item
            token = body.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return
            page_token = token

    def _folder_q(self, extra: str) -> str:
        return (
            f"'{_q_literal(self._root)}' in parents and mimeType='{_FOLDER_MIME}'"
            f" and trashed=false and appProperties has {{ key='mm_service' and"
            f" value='{SERVICE_MARKER}' }}{extra}"
        )

    async def _find_folder(self, app_key: str) -> str | None:
        q = self._folder_q(
            f" and appProperties has {{ key='app_key' and value='{_q_literal(app_key)}' }}"
        )
        async for item in self._paged("folder", q, "id", order_by="createdTime"):
            file_id = item.get("id")
            if isinstance(file_id, str) and _FILE_ID_RE.fullmatch(file_id):
                return file_id
        return None

    async def ensure_app_folder(self, app_key: str) -> str:
        _check_app_key(app_key)
        cached = self._folders.get(app_key)
        if cached:
            return cached
        lock = self._folder_locks.setdefault(app_key, asyncio.Lock())
        async with lock:
            cached = self._folders.get(app_key)
            if cached:
                return cached
            folder_id = await self._find_folder(app_key)
            if folder_id is None:
                await self._api.request(
                    "folder",
                    "POST",
                    FILES_URL,
                    idempotent=False,
                    params={"fields": "id"},
                    json={
                        "name": app_key,
                        "mimeType": _FOLDER_MIME,
                        "parents": [self._root],
                        "appProperties": {
                            "mm_service": SERVICE_MARKER,
                            "kind": "app_folder",
                            "app_key": app_key,
                        },
                    },
                )
                # Re-list and take the oldest: absorbs a race with another process.
                folder_id = await self._find_folder(app_key)
                if folder_id is None:
                    raise DriveError(DriveErrorCode.UNAVAILABLE)
            self._folders[app_key] = folder_id
            return folder_id

    async def create(
        self,
        *,
        app_key: str,
        report_id: UUID,
        attachment_id: UUID,
        content_type: ImageType,
        data: bytes,
    ) -> str:
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise DriveError(DriveErrorCode.TOO_LARGE)
        _check_app_key(app_key)
        async with self._uploads:
            folder_id = await self.ensure_app_folder(app_key)
            metadata = {
                "name": str(attachment_id),
                "parents": [folder_id],
                "mimeType": ImageType(content_type).value,
                "appProperties": {
                    "mm_service": SERVICE_MARKER,
                    "kind": "attachment",
                    "app_key": app_key,
                    "report_id": str(report_id),
                    "attachment_id": str(attachment_id),
                },
            }
            ctype, body = _multipart(metadata, ImageType(content_type).value, data)
            resp = await self._api.request(
                "create",
                "POST",
                UPLOAD_URL,
                idempotent=False,
                params={"uploadType": "multipart", "fields": "id"},
                headers={"Content-Type": ctype},
                content=body,
            )
        file_id = _json_body(resp).get("id")
        if not isinstance(file_id, str) or _FILE_ID_RE.fullmatch(file_id) is None:
            raise DriveError(DriveErrorCode.UNAVAILABLE)
        return file_id

    @asynccontextmanager
    async def _open_read(self, file_id: str) -> AsyncIterator[AsyncIterator[bytes]]:
        async with self._api.stream("read", f"{FILES_URL}/{file_id}", params={"alt": "media"}) as r:
            yield _capped_stream(r)

    def open_read(self, file_id: str) -> AbstractAsyncContextManager[AsyncIterator[bytes]]:
        _check_file_id(file_id)
        return self._open_read(file_id)

    async def delete(self, file_id: str) -> None:
        _check_file_id(file_id)
        try:
            await self._api.request("delete", "DELETE", f"{FILES_URL}/{file_id}", idempotent=True)
        except DriveError as exc:
            if exc.code != DriveErrorCode.NOT_FOUND:  # a 404 means already gone: success
                raise

    async def list_files(self) -> AsyncIterator[StoredFile]:
        folder_ids = [
            item["id"]
            async for item in self._paged("list", self._folder_q(""), "id")
            if isinstance(item.get("id"), str) and _FILE_ID_RE.fullmatch(item["id"])
        ]
        for folder_id in folder_ids:
            q = (
                f"'{folder_id}' in parents and trashed=false and mimeType!='{_FOLDER_MIME}'"
                f" and appProperties has {{ key='mm_service' and value='{SERVICE_MARKER}' }}"
            )
            async for item in self._paged("list", q, "id,createdTime,appProperties"):
                file_id = item.get("id")
                if not isinstance(file_id, str) or _FILE_ID_RE.fullmatch(file_id) is None:
                    continue
                props = item.get("appProperties")
                if not isinstance(props, dict) or props.get("kind") != "attachment":
                    props = {}
                app_key = props.get("app_key")
                yield StoredFile(
                    file_id=file_id,
                    app_key=app_key if isinstance(app_key, str) else None,
                    attachment_id=_parse_uuid(props.get("attachment_id")),
                    report_id=_parse_uuid(props.get("report_id")),
                    created_at=_parse_time(item.get("createdTime")),
                )


def _parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.now(UTC)  # unparsable = looks young = kept by the sweep
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def create_root_folder(api: DriveApi) -> str:
    """One-time setup (`attachments-init-root`): `drive.file` only sees folders it created."""
    resp = await api.request(
        "folder",
        "POST",
        FILES_URL,
        idempotent=False,
        params={"fields": "id"},
        json={
            "name": ROOT_FOLDER_NAME,
            "mimeType": _FOLDER_MIME,
            "appProperties": {"mm_service": SERVICE_MARKER, "kind": "root"},
        },
    )
    file_id = _json_body(resp).get("id")
    if not isinstance(file_id, str) or _FILE_ID_RE.fullmatch(file_id) is None:
        raise DriveError(DriveErrorCode.UNAVAILABLE)
    return file_id
