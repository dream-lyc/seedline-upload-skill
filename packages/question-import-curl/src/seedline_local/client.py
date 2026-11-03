"""Adapt the shared workflow API to the Shell/OpenSSL/curl transport.

Only a permission-restricted profile path crosses the subprocess boundary.
The profile remains in memory for the shared environment binding checks.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from question_content_contracts.validation import load_json
from question_import_sdk.client import PREFIX, ApiError, SignedClient
from question_import_sdk.config import load_config
from question_import_sdk.local_files import protect_file


def request_helper():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "scripts" / ("request.ps1" if os.name == "nt" else "request.sh")
    return Path(__file__).resolve().parents[2] / "scripts" / ("request.ps1" if os.name == "nt" else "request.sh")


def helper_command(helper):
    if str(helper).endswith('.ps1'):
        return ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(helper)]
    return [str(helper)]


class CurlClient(SignedClient):
    """Reuse pure operation helpers without constructing an HTTPX client."""

    def __init__(self, profile_path, *, profile=None, helper=None):
        supplied_path = Path(profile_path).expanduser()
        checked_profile = load_config(supplied_path)
        if profile is not None and profile != checked_profile:
            raise ValueError("凭据配置在读取后发生变化，请检查当前配置后继续")
        self.profile_path = supplied_path.absolute()
        self.profile = checked_profile
        self.helper = Path(helper) if helper else request_helper()
        self.last_status = None
        self.last_request_id = None

    def close(self):
        pass

    def request(
        self, method, path, *, body=None, content_type=None,
        idempotency_key=None, body_file=None,
    ):
        if (
            not path.startswith(PREFIX + "/")
            or any(char in path for char in ("#", "\r", "\n", "\\"))
        ):
            raise ValueError("只允许调用当前绑定origin的集成接口")
        if body is not None and body_file is not None:
            raise ValueError("正文和文件不能同时指定")
        self.last_status = None
        self.last_request_id = None
        if not self.helper.is_file():
            raise ValueError("缺少Shell/curl请求工具，请重新获取完整本地工具包")
        with tempfile.TemporaryDirectory(prefix="seedline-request-") as directory:
            root = Path(directory)
            status_file = root / "status"
            args = [*helper_command(self.helper), method.upper(), path, "--profile", str(self.profile_path),
                    "--status-file", str(status_file)]
            if body is not None:
                target = root / "body"
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                protect_file(fd, target)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(body)
                body_file = target
            if body_file is not None:
                args += ["--body", str(Path(body_file).resolve())]
            if content_type is not None:
                args += ["--content-type", content_type]
            if idempotency_key is not None:
                args += ["--idempotency-key", idempotency_key]
            try:
                result = subprocess.run(args, capture_output=True, timeout=45 if method.upper() == "GET" and path == PREFIX+"/me" else 1930, check=False)
            except subprocess.TimeoutExpired:
                raise httpx.ReadTimeout("网络连接超时，请按原账本查询写操作结果") from None
            status = status_file.read_text().strip() if status_file.is_file() else "000"
        if len(status) != 3 or not status.isascii() or not status.isdigit():
            raise ValueError("请求工具未返回有效HTTP状态，请检查完整工具包")
        if result.returncode == 2:
            raise ValueError("请求工具拒绝本地配置或参数，请检查profile权限及curl/OpenSSL/jq环境")
        if status == "000" or result.returncode not in {0, 22}:
            raise httpx.ConnectError("网络连接失败，请按原账本查询写操作结果")
        self.last_status = int(status)
        if 300 <= self.last_status < 400:
            raise ApiError(self.last_status, "redirect_rejected", "服务地址发生重定向，请核对配置")
        try:
            payload = load_json(result.stdout)
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise self._invalid_response()
        request_id = payload.get("request_id")
        self.last_request_id = request_id if isinstance(request_id, str) else None
        if not 200 <= self.last_status < 300:
            error = payload.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("code"), str)
                or not isinstance(error.get("message"), str)
                or not isinstance(error.get("retryable", False), bool)
            ):
                raise self._invalid_response()
            raise ApiError(
                self.last_status, error["code"], error["message"],
                retryable=error.get("retryable", False), request_id=self.last_request_id,
            )
        if result.returncode or not isinstance(payload.get("data"), dict):
            raise self._invalid_response()
        return payload["data"]

    def _invalid_response(self):
        return ApiError(
            self.last_status, "invalid_response", "服务返回无效响应，写请求结果待确认。",
            retryable=True, request_id=self.last_request_id,
        )
