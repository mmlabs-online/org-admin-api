"""In-memory fake of the Drive v3 endpoints used by the attachment store (ADM-027).

Plugs into `httpx.MockTransport`. Failures are scripted per operation with `fake.queue(op, ...)`;
each outcome is an int status, an `httpx.Response`, or an exception, used once, in order.
"""

import json
import re
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import Settings

CLIENT_ID = "sentinel-client.apps.googleusercontent.com"
CLIENT_SECRET = "SENTINELclientSECRET"
REFRESH_TOKEN = "SENTINELrefreshTOKEN"
ACCESS_TOKEN = "SENTINELaccessTOKEN"
ROOT_ID = "SENTINELrootFOLDER1"
GOOGLE_TEXT = "Google-said-this-sentinel-text"

FOLDER_MIME = "application/vnd.google-apps.folder"


def drive_settings(**overrides: str | None) -> Settings:
    values: dict[str, Any] = {
        "google_drive_client_id": CLIENT_ID,
        "google_drive_client_secret": CLIENT_SECRET,
        "google_drive_refresh_token": REFRESH_TOKEN,
        "google_drive_root_folder_id": ROOT_ID,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def error_body(reason: str) -> dict[str, Any]:
    return {"error": {"code": 403, "message": GOOGLE_TEXT, "errors": [{"reason": reason}]}}


class FakeStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], owner: "FakeDrive") -> None:
        self._chunks = chunks
        self._owner = owner

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            self._owner.chunks_served += 1
            yield chunk

    async def aclose(self) -> None:
        self._owner.streams_closed += 1


class FakeDrive:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.token_posts = 0
        self.token_bodies: list[str] = []
        self.streams_closed = 0
        self.chunks_served = 0
        self.read_chunks: list[bytes] | None = None  # override the served body
        self._queues: dict[str, deque[Any]] = defaultdict(deque)
        self.files[ROOT_ID] = {
            "name": "root",
            "mimeType": FOLDER_MIME,
            "parents": [],
            "appProperties": {"mm_service": "org-admin-api", "kind": "root"},
            "createdTime": "2026-01-01T00:00:00.000Z",
            "data": b"",
        }
        self.transport = httpx.MockTransport(self._handle)

    def queue(self, op: str, *outcomes: Any) -> None:
        self._queues[op].extend(outcomes)

    def count(self, op: str) -> int:
        return sum(1 for r in self.requests if _op(r) == op)

    def add_file(
        self,
        *,
        parent: str,
        attachment_id: uuid.UUID | None,
        created: datetime,
        app_key: str = "ricotdin",
        report_id: uuid.UUID | None = None,
        props: dict[str, str] | None = None,
        data: bytes = b"x",
    ) -> str:
        file_id = f"F{uuid.uuid4().hex}"
        merged = {"mm_service": "org-admin-api", "kind": "attachment", "app_key": app_key}
        if attachment_id is not None:
            merged["attachment_id"] = str(attachment_id)
            merged["report_id"] = str(report_id or uuid.uuid4())
        self.files[file_id] = {
            "name": str(attachment_id),
            "mimeType": "image/png",
            "parents": [parent],
            "appProperties": props if props is not None else merged,
            "createdTime": created.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "data": data,
        }
        return file_id

    def add_folder(self, app_key: str, created: str = "2026-01-02T00:00:00.000Z") -> str:
        folder_id = f"D{uuid.uuid4().hex}"
        self.files[folder_id] = {
            "name": app_key,
            "mimeType": FOLDER_MIME,
            "parents": [ROOT_ID],
            "appProperties": {
                "mm_service": "org-admin-api",
                "kind": "app_folder",
                "app_key": app_key,
            },
            "createdTime": created,
            "data": b"",
        }
        return folder_id

    # --- request handling -------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        op = _op(request)
        if op == "token":
            self.token_posts += 1
            self.token_bodies.append(request.content.decode())
        scripted = self._scripted(op)
        if scripted is not None:
            return scripted
        if op == "token":
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})
        if request.headers.get("authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(401, json={"error": {"message": GOOGLE_TEXT}})
        if op == "create":
            return self._upload(request)
        if op == "list":
            return self._list(request)
        if op == "folder":
            return self._create_folder(request)
        file_id = unquote(urlparse(str(request.url)).path.rsplit("/", 1)[1])
        entry = self.files.get(file_id)
        if entry is None:
            return httpx.Response(404, json={"error": {"message": GOOGLE_TEXT}})
        if op == "delete":
            del self.files[file_id]
            return httpx.Response(204)
        chunks = self.read_chunks if self.read_chunks is not None else [entry["data"]]
        return httpx.Response(200, stream=FakeStream(chunks, self))

    def _scripted(self, op: str) -> httpx.Response | None:
        queue = self._queues[op]
        if not queue:
            return None
        outcome = queue.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(
            outcome,
            json={"error": {"message": GOOGLE_TEXT}},
            headers={"x-google-sentinel": GOOGLE_TEXT},
        )

    def _create_folder(self, request: httpx.Request) -> httpx.Response:
        meta = json.loads(request.content)
        folder_id = f"D{uuid.uuid4().hex}"
        self.files[folder_id] = {
            **meta,
            "parents": meta.get("parents", []),
            "createdTime": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "data": b"",
        }
        return httpx.Response(200, json={"id": folder_id})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        parts = body.split(b"--" + boundary)
        meta_raw = parts[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        media = parts[2].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        meta = json.loads(meta_raw)
        file_id = f"F{uuid.uuid4().hex}"
        self.files[file_id] = {
            **meta,
            "createdTime": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "data": media,
        }
        return httpx.Response(200, json={"id": file_id})

    def _list(self, request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        q = params["q"][0]
        parent = re.match(r"'([^']+)' in parents", q)
        assert parent
        want_folder = f"mimeType='{FOLDER_MIME}'" in q
        not_folder = f"mimeType!='{FOLDER_MIME}'" in q
        props = re.findall(r"appProperties has \{ key='([^']+)' and value='([^']+)' \}", q)
        rows = [
            (fid, f)
            for fid, f in self.files.items()
            if parent.group(1) in f.get("parents", [])
            and (not want_folder or f["mimeType"] == FOLDER_MIME)
            and (not not_folder or f["mimeType"] != FOLDER_MIME)
            and all(f.get("appProperties", {}).get(k) == v for k, v in props)
        ]
        rows.sort(key=lambda r: r[1]["createdTime"])
        size = 1 if "pageSize" in params and self.page_size else 100
        start = int(params.get("pageToken", ["0"])[0])
        page = rows[start : start + size]
        out: dict[str, Any] = {
            "files": [
                {"id": fid, "createdTime": f["createdTime"], "appProperties": f["appProperties"]}
                for fid, f in page
            ]
        }
        if start + size < len(rows):
            out["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=out)

    page_size = 0  # set to 1 to force paging


def _op(request: httpx.Request) -> str:
    url = urlparse(str(request.url))
    if url.netloc == "oauth2.googleapis.com":
        return "token"
    if url.path.startswith("/upload/"):
        return "create"
    is_collection = url.path.endswith("/files")
    if request.method == "GET" and is_collection:
        return "list"
    if request.method == "POST" and is_collection:
        return "folder"
    if request.method == "DELETE":
        return "delete"
    return "read"
