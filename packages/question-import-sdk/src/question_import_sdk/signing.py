"""RFC 9421 fixed HMAC profile and RFC 9530 complete-body SHA-256."""

import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit


def decode_secret(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("SK必须是32随机字节的无填充base64url编码")
    raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise ValueError("SK编码无效")
    return raw


def digest_bytes(body):
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha-256=:" + base64.b64encode(digest.digest()).decode() + ":"


def sign_headers(
    method,
    uri,
    access_key,
    secret_key,
    *,
    body=None,
    content_type=None,
    idempotency_key=None,
    created=None,
    nonce=None,
    content_digest=None,
):
    parsed = urlsplit(uri)
    if parsed.fragment or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("签名目标URI无效")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", access_key):
        raise ValueError("AK格式无效")
    key = decode_secret(secret_key)
    created = int(time.time()) if created is None else created
    nonce = secrets.token_urlsafe(16) if nonce is None else nonce
    try:
        nonce_bytes = base64.urlsafe_b64decode(nonce + "=" * ((-len(nonce)) % 4))
    except Exception as error:
        raise ValueError("nonce无效") from error
    if len(nonce_bytes) < 16 or not re.fullmatch(r"[A-Za-z0-9_-]+", nonce):
        raise ValueError("nonce至少128bit")
    fields = [("@method", method.upper()), ("@target-uri", uri)]
    headers = {}
    if body is not None or content_digest is not None:
        if not content_type or any(c in content_type for c in "\r\n"):
            raise ValueError("正文须有实际Content-Type")
        digest = content_digest or digest_bytes(body)
        headers.update({"Content-Digest": digest, "Content-Type": content_type})
        fields.extend([("content-digest", digest), ("content-type", content_type)])
    if idempotency_key is not None:
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", idempotency_key):
            raise ValueError("幂等键须为1–128可见ASCII字符")
        headers["Idempotency-Key"] = idempotency_key
        fields.append(("idempotency-key", idempotency_key))
    components = " ".join('"' + k + '"' for k, _ in fields)
    params = (
        f'({components});created={created};expires={created + 300};nonce="{nonce}";'
        f'keyid="{access_key}";alg="hmac-sha256"'
    )
    base = "\n".join(
        [f'"{name}": {value}' for name, value in fields] + ['"@signature-params": ' + params]
    )
    signature = base64.b64encode(hmac.digest(key, base.encode(), "sha256")).decode()
    headers.update({"Signature-Input": "sig1=" + params, "Signature": "sig1=:" + signature + ":"})
    return headers
