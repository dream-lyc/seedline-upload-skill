"""Loopback-only read-only viewer; one explicit batch and opaque media mappings."""

import hashlib
import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .local_files import open_regular, sync_directory


class PreviewServer:
    def __init__(self, batch, *, assets_root=None, client=None, bind=True):
        self.batch = batch
        version = batch.data["contract"]["preview_version"]
        self.assets = Path(
            assets_root or Path(__file__).parent / "assets" / "preview" / version
        ).resolve()
        manifest = json.loads((self.assets / "manifest.json").read_text())
        if manifest.get("preview_version") != version:
            raise ValueError("预览组件版本不匹配，请更新SDK")
        self.client = client
        self.version = version
        self.refresh_lock = threading.Lock()
        self.files = {}
        self.payload = {}
        self.refresh()
        self.allowed_assets = {"manifest.json"}
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("预览资源清单缺失，请更新完整SDK")
        for entry in files:
            path = (self.assets / entry["path"]).resolve()
            if (
                not path.is_relative_to(self.assets)
                or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]
            ):
                raise ValueError("预览资源缺失、越界或摘要不匹配，请更新完整SDK")
            self.allowed_assets.add(entry["path"])
        if not bind:
            return
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def allowed(self):
                host = self.headers.get("Host", "")
                return host == f"127.0.0.1:{outer.http.server_port}"

            def security_headers(self):
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' https: data:; media-src 'self' https:; connect-src 'self'; "
                    "frame-ancestors 'none'; object-src 'none'; "
                    "base-uri 'none'; form-action 'none'",
                )

            def send_bytes(self, status, body, mime, *, head=False, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.security_headers()
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def fail(self, status, *, head=False):
                self.send_bytes(status, b"", "text/plain", head=head)

            def do_POST(self):
                self.fail(405)

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST
            do_OPTIONS = do_POST

            def do_HEAD(self):
                self.do_GET(head=True)

            def do_GET(self, head=False):
                if not self.allowed():
                    return self.fail(403, head=head)
                path = unquote(urlsplit(self.path).path)
                if (
                    "\\" in path
                    or "\x00" in path
                    or any(part in {".", ".."} for part in path.split("/"))
                ):
                    return self.fail(404, head=head)
                if path == "/batch.json":
                    try:
                        outer.refresh()
                    except (ValueError, OSError):
                        return self.fail(503, head=head)
                    return self.send_bytes(
                        200,
                        json.dumps(outer.payload, ensure_ascii=False, allow_nan=False).encode(),
                        "application/json; charset=utf-8",
                        head=head,
                    )
                if path.startswith("/media/"):
                    value = outer.files.get(path[len("/media/") :])
                    if not value:
                        return self.fail(404, head=head)
                    file, mime = value
                else:
                    rel = "index.html" if path == "/" else path.lstrip("/")
                    file = (outer.assets / rel).resolve()
                    if (
                        rel not in outer.allowed_assets
                        or not file.is_relative_to(outer.assets)
                        or not file.is_file()
                    ):
                        return self.fail(404, head=head)
                    mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                size = file.stat().st_size
                low = 0
                high = size - 1
                status = 200
                extra = {"Accept-Ranges": "bytes"}
                if self.headers.get("Range"):
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers["Range"])
                    if not match or (not match[1] and not match[2]):
                        return self.fail(416, head=head)
                    if match[1]:
                        low = int(match[1])
                        high = min(size - 1, int(match[2]) if match[2] else size - 1)
                    else:
                        low = max(0, size - int(match[2]))
                    if low > high or low >= size:
                        return self.fail(416, head=head)
                    status = 206
                    extra["Content-Range"] = f"bytes {low}-{high}/{size}"
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(max(0, high - low + 1)))
                self.security_headers()
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                if not head:
                    with file.open("rb") as stream:
                        stream.seek(low)
                        remaining = high - low + 1
                        while remaining > 0:
                            block = stream.read(min(1024 * 1024, remaining))
                            if not block:
                                break
                            self.wfile.write(block)
                            remaining -= len(block)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.http.server_port}/"
        self.thread = None

    def refresh(self):
        import httpx
        from question_content_contracts.validation import load_json

        from .client import ApiError

        with self.refresh_lock:
            data = load_json((self.batch.root / "ledger.json").read_bytes())
            if (
                data["origin"] != self.batch.data["origin"]
                or data["user_id"] != self.batch.data["user_id"]
            ):
                raise ValueError("预览批次身份发生变化，请重新打开")
            if data["contract"]["preview_version"] != self.version:
                raise ValueError("预览版本发生变化，请更新SDK并重新打开")
            files = {}
            media = {}
            for entry in data["media"]:
                opaque = hashlib.sha256(entry["ref"].encode()).hexdigest()
                source = (self.batch.root / entry["source_file"]).resolve()
                if not source.is_relative_to(self.batch.root.resolve()) or not source.is_file():
                    raise ValueError("批次媒体路径越界或文件缺失")
                files[opaque] = (source, entry["content_type"])
                value = {"url": "/media/" + opaque, "content_type": entry["content_type"]}
                media[entry["ref"]] = value
                op = data["operations"].get("media:" + entry["ref"])
                if op and op["state"] == "succeeded":
                    media[op["result"]["media_id"]] = value
            if self.client:
                for identity, metadata in data["media_metadata"].items():
                    if identity in media:
                        continue
                    try:
                        result = self.client.access_url(identity)
                    except (ApiError, httpx.TransportError) as error:
                        raise ValueError(
                            "临时媒体地址获取失败，请检查权限或稍后重新加载"
                        ) from error
                    parsed = urlsplit(result["url"])
                    if parsed.scheme != "https" or not parsed.netloc:
                        raise ValueError("平台临时媒体地址无效")
                    media[identity] = {
                        "url": result["url"],
                        "content_type": metadata.get("content_type"),
                    }
            from .review import fingerprint, rendered_objects

            self.snapshot = data
            self.files = files
            self.payload = {
                "preview_version": self.version,
                "objects": rendered_objects(data),
                "media": media,
                "publication_review": {
                    "batch_id": data["batch_id"],
                    "fingerprint": fingerprint(data, self.batch.root),
                },
            }

    def __enter__(self):
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)


def export_preview(batch, output, *, assets_root=None, client=None):
    """Freeze the shared renderer and current material into a durable local page."""
    import os
    import shutil
    import subprocess
    import tempfile

    from .media import sha256_file

    output = Path(output).expanduser().absolute()
    if output.suffix.lower() != ".html" or output.is_symlink():
        raise ValueError("请为试题预览选择一个新的网页文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    view = PreviewServer(batch, assets_root=assets_root, bind=False)
    media_root = output.parent / "preview-media"
    if media_root.is_symlink():
        raise ValueError("预览材料保存位置不可用，请换一个文件夹")
    media_root.mkdir(mode=0o700, exist_ok=True)
    payload = view.payload

    def save_material(source, mime):
        suffixes = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp3": ".mp3",
            "audio/aac": ".aac",
            "audio/mp4": ".m4a",
            "audio/x-m4a": ".m4a",
            "audio/ogg": ".ogg",
            "audio/webm": ".webm",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
        }
        if mime not in suffixes:
            raise ValueError("这份材料暂时无法保存到预览，请提供常用格式的图片、音频或视频")
        name = sha256_file(source) + suffixes[mime]
        target = media_root / name
        if target.is_symlink():
            raise ValueError("预览材料保存位置不可用，请换一个文件夹")
        if not target.exists() or sha256_file(target) != name.split(".")[0]:
            fd, temporary = tempfile.mkstemp(dir=media_root, prefix=".material-")
            try:
                with os.fdopen(fd, "wb") as dest, source.open("rb") as original:
                    shutil.copyfileobj(original, dest, length=1024 * 1024)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {"url": "./preview-media/" + name, "content_type": mime}

    for identity, material in list(payload["media"].items()):
        opaque = material["url"].rsplit("/", 1)[-1]
        source, mime = view.files[opaque]
        payload["media"][identity] = save_material(source, mime)
    for identity, metadata in view.snapshot["media_metadata"].items():
        if identity in payload["media"]:
            continue
        if client is None:
            raise ValueError("还有题目材料未保存，请连接题库后重新生成预览")
        url = client.access_url(identity)["url"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(char) < 32 for char in url)
        ):
            raise ValueError("这份题目材料暂时无法取得，请稍后重新生成预览")
        # A short-lived URL stays on stdin, never argv, HTML, diagnostics or disk.
        with tempfile.TemporaryDirectory(prefix=".download-", dir=media_root) as directory:
            source = Path(directory) / "material"
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.lower()
                not in {
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                    "no_proxy",
                    "curl_ca_bundle",
                    "ssl_cert_file",
                    "ssl_cert_dir",
                }
            }
            limit = metadata.get("byte_size") or 1024 * 1024 * 1024
            if not isinstance(limit, int) or not 0 < limit <= 1024 * 1024 * 1024:
                raise ValueError("材料大小无法确认，请重新取得原始材料")
            try:
                result = subprocess.run(
                    [
                        "curl",
                        "-q",
                        "--silent",
                        "--fail",
                        "--proto",
                        "=https",
                        "--noproxy",
                        "*",
                        "--proxy",
                        "",
                        "--connect-timeout",
                        "30",
                        "--max-time",
                        "1900",
                        "--max-filesize",
                        str(limit),
                        "--write-out",
                        "%{http_code}",
                        "--output",
                        str(source),
                        "--config",
                        "-",
                    ],
                    input=("url = " + json.dumps(url) + "\n").encode(),
                    capture_output=True,
                    env=environment,
                    timeout=1930,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                raise ValueError("题目材料暂时未能保存，请稍后重新生成预览") from None
            if (
                result.returncode
                or result.stdout != b"200"
                or not source.is_file()
                or not 0 < source.stat().st_size <= limit
            ):
                raise ValueError("题目材料暂时未能保存，请稍后重新生成预览")
            payload["media"][identity] = save_material(source, metadata.get("content_type"))

    document = render_preview_document(view, payload)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=".preview-", suffix=".html")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(document.encode("utf-8"))
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def render_preview_document(view, payload):
    """Pure trusted template rendering shared by export and confirmation verification."""
    import base64

    def embedded_asset(relative):
        relative = relative.lstrip("/")
        if relative not in view.allowed_assets or relative == "manifest.json":
            raise ValueError("预览页面文件不完整，请重新取得录题工具")
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        return (
            "data:"
            + mime
            + ";base64,"
            + base64.b64encode((view.assets / relative).read_bytes()).decode()
        )

    scripts, styles = [], []
    for relative in sorted(view.allowed_assets):
        path = view.assets / relative
        if path.suffix == ".js":
            script = path.read_text()
            for asset in view.allowed_assets:
                if asset.startswith("toefl-mock/"):
                    uri = "/" + asset
                    if uri in script:
                        value = json.dumps(embedded_asset(asset))
                        script = script.replace(json.dumps(uri), value).replace(
                            "'" + uri + "'", value
                        )
            scripts.append(re.sub(r"</script", r"<\\/script", script, flags=re.I))
        elif path.suffix == ".css":
            from posixpath import normpath

            def css_url(match, relative=relative):
                reference = match[1].strip("\"' ")
                if reference.startswith("data:"):
                    return match[0]
                relative_asset = (
                    reference.lstrip("/")
                    if reference.startswith("/")
                    else normpath(str(Path(relative).parent / reference))
                )
                return 'url("' + embedded_asset(relative_asset) + '")'

            styles.append(re.sub(r"url\(([^)]+)\)", css_url, path.read_text()))
    if not scripts:
        raise ValueError("预览页面文件不完整，请重新取得录题工具")
    script_hashes = " ".join(
        "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
        for script in scripts
    )
    csp = (
        "default-src 'none'; script-src " + script_hashes + "; style-src 'unsafe-inline'; "
        "img-src 'self' data:; media-src 'self' data:; font-src data:; connect-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    document = (view.assets / "index.html").read_text()
    document = re.sub(
        r'<meta\b[^>]*http-equiv="Content-Security-Policy"[^>]*>', "", document, flags=re.I
    )
    document = re.sub(r"<script\b[^>]*src=[^>]*>\s*</script>", "", document, flags=re.I)
    document = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', "", document, flags=re.I)
    document = document.replace(
        "</head>",
        '<meta http-equiv="Content-Security-Policy" content="'
        + csp
        + '"><style>'
        + "\n".join(styles).replace("</style", "<\\/style")
        + "</style></head>",
    )
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    for char, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        serialized = serialized.replace(char, escaped)
    content = '<script id="acebro-preview-data" type="application/json">' + serialized + "</script>"
    content += "".join('<script type="module">' + script + "</script>" for script in scripts)
    document = document.replace("</body>", content + "</body>")
    return document


def export_preview_for_open(batch, output, *, assets_root=None, client=None):
    """Keep the stable export and publish an immutable full copy for a fresh tab.

    An already-open static page cannot observe an atomic replacement on disk.
    Changing the filename only when its complete bytes change gives the caller
    a new document URL without a server, file watcher or network polling.
    """
    import os
    import stat
    import tempfile

    saved = export_preview(batch, output, assets_root=assets_root, client=client)

    def read_regular(path):
        try:
            descriptor = open_regular(path)
        except OSError as error:
            if path.is_symlink():
                raise ValueError("预览快照路径不能为符号链接，请选择其他保存位置") from None
            raise error
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("预览快照保存位置必须是普通文件")
            return stream.read()

    content = read_regular(saved)
    digest = hashlib.sha256(content).hexdigest()
    opened = saved.with_name("preview-" + digest + ".html")

    def existing_matches():
        try:
            previous = read_regular(opened)
        except FileNotFoundError:
            return False
        if previous != content:
            raise ValueError("同名预览快照内容不一致，未覆盖原文件，请选择其他保存位置")
        return True

    if existing_matches():
        return saved, opened
    descriptor, temporary = tempfile.mkstemp(
        dir=saved.parent, prefix=".preview-open-", suffix=".html"
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Atomic create-if-absent: unlike replace, this cannot clobber an
            # existing snapshot or a symlink introduced by a concurrent writer.
            os.link(temporary, opened, follow_symlinks=False)
        except FileExistsError:
            if not existing_matches():
                raise ValueError("预览保存位置发生变化，请重新导出") from None
        sync_directory(saved.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return saved, opened
