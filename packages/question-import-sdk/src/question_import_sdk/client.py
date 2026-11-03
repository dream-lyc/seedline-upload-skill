"""No redirects, no write retry loops, no cookie/session emulation."""

from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from .signing import digest_file, sign_headers

PREFIX = "/api/integration/v1"
ERROR_HELP = {
    "credential_invalid": "请登录平台检查凭据有效期，必要时创建新凭据并更新本机配置。",
    "signature_invalid": "请检查服务地址和SDK版本，不能修改签名后的正文。",
    "signature_expired": "请同步本机时间后由用户选择重试。",
    "signature_replayed": "重试须生成新nonce并保留原幂等键。",
    "permission_denied": "请检查管理员角色及此操作所需scope。",
    "contract_version_unsupported": "获取新契约并重新校验，为修改后的内容建立新操作。",
    "idempotency_conflict": "原键已绑定不同内容，保留原账本并为新内容建立新批次。",
    "submission_in_progress": "操作仍在执行，结果待确认；可查询或由用户稍后重试。",
    "rate_limited": "请等待所示Retry-After，由用户选择重试。",
    "media_processing_failed": "请核对原文件格式与完整性，修改原文件须建立新上传操作。",
    "dependency_conflict": "请检查引用ID当前是否存在并可用。",
    "resource_conflict": "请查询已有对象，不能直接重新分配ID后重复导入。",
}


class ApiError(Exception):
    def __init__(
        self,
        status,
        code,
        message,
        *,
        retryable=False,
        request_id=None,
        retry_after=None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after = retry_after

    def public(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": self.request_id,
            "retry_after": self.retry_after,
            "help": ERROR_HELP.get(
                self.code, "保留原账本和request_id，查询结果后由用户决定下一步。"
            ),
        }


class SignedClient:
    def __init__(self, profile, *, transport=None, timeout=1900):
        self.profile = profile
        self.http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=30),
            follow_redirects=False,
            trust_env=False,
        )
        self.last_status = None
        self.last_request_id = None

    def close(self):
        self.http.close()

    def request(
        self,
        method,
        path,
        *,
        body=None,
        content_type=None,
        idempotency_key=None,
        body_file=None,
    ):
        if (
            not path.startswith(PREFIX + "/")
            or "#" in path
            or "\r" in path
            or "\n" in path
            or "\\" in path
        ):
            raise ValueError("只允许调用当前绑定origin的集成接口")
        url = self.profile.origin + path
        request = self.http.build_request(method, url)
        # HTTPX determines the exact transmitted URL, retaining raw percent escapes and query order.
        uri = str(request.url)
        digest = digest_file(body_file) if body_file else None
        headers = sign_headers(
            method,
            uri,
            self.profile.access_key,
            self.profile.secret_key,
            body=body,
            content_type=content_type,
            idempotency_key=idempotency_key,
            content_digest=digest,
        )
        if body_file:
            size = Path(body_file).stat().st_size
            headers["Content-Length"] = str(size)

            def chunks():
                with open(body_file, "rb") as stream:
                    while value := stream.read(1024 * 1024):
                        yield value

            content = chunks()
        else:
            content = body
        response = self.http.request(method, url, headers=headers, content=content)
        self.last_status = response.status_code
        self.last_request_id = response.headers.get("X-Request-ID")
        if 300 <= response.status_code < 400:
            raise ApiError(
                response.status_code,
                "redirect_rejected",
                "服务地址发生重定向，请核对配置；SDK不会转发签名。",
            )
        try:
            payload = response.json()
        except ValueError:
            raise ApiError(
                response.status_code,
                "invalid_response",
                "服务返回无效响应，写请求结果待确认。",
                retryable=True,
            ) from None
        if not isinstance(payload, dict):
            raise ApiError(
                response.status_code,
                "invalid_response",
                "服务返回非对象响应，写请求结果待确认。",
                retryable=True,
            )
        self.last_request_id = payload.get("request_id", self.last_request_id)
        if not 200 <= response.status_code < 300:
            error = payload.get("error", {})
            if not isinstance(error, dict):
                raise ApiError(
                    response.status_code,
                    "invalid_response",
                    "服务返回无效错误响应，写请求结果待确认。",
                    retryable=True,
                )
            raise ApiError(
                response.status_code,
                error.get("code", "invalid_response"),
                error.get("message", "请求失败"),
                retryable=error.get("retryable", False),
                request_id=self.last_request_id,
                retry_after=response.headers.get("Retry-After"),
            )
        if not isinstance(payload.get("data"), dict):
            raise ApiError(
                response.status_code,
                "invalid_response",
                "服务响应缺少data，写请求结果待确认。",
                retryable=True,
            )
        return payload["data"]

    def me(self):
        return self.request("GET", PREFIX + "/me")

    def contract(self, exam, version=None):
        if exam not in {"ielts", "toefl"}:
            raise ValueError("考试体系不支持")
        suffix = "/versions/" + quote(version, safe="") if version else "/latest"
        return self.request("GET", PREFIX + "/contracts/" + exam + suffix)

    def media(self, identity):
        return self.request("GET", PREFIX + "/media/" + quote(identity, safe=""))

    def access_url(self, identity):
        return self.request("GET", PREFIX + "/media/" + quote(identity, safe="") + "/access-url")

    def submission(self, identity):
        return self.request("GET", PREFIX + "/submissions/" + quote(identity, safe=""))

    def find_submission(self, operation, key):
        return self.request(
            "GET",
            PREFIX + "/submissions?" + urlencode({"operation": operation, "idempotency_key": key}),
        )

    def content(self, kind, identity=None, **query):
        if kind not in {"ielts_question", "ielts_paper", "toefl_item", "toefl_form"}:
            raise ValueError("对象类型不支持")
        path = PREFIX + "/content/" + kind + ("/" + quote(identity, safe="") if identity else "")
        return self.request("GET", path + ("?" + urlencode(query) if query else ""))

    def submit(self, body, key):
        return self.request(
            "POST",
            PREFIX + "/submissions",
            body=body,
            content_type="application/json; charset=utf-8",
            idempotency_key=key,
        )

    def upload(self, path, content_type, key):
        return self.request(
            "POST",
            PREFIX + "/media",
            body_file=path,
            content_type=content_type,
            idempotency_key=key,
        )
