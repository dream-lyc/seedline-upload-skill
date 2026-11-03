"""Origin-bound credentials in a permission-restricted local file, never a batch."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .local_files import check_private, protect_file, sync_directory
from .signing import decode_secret


def default_home(home=None):
    """New installs use Seedline; reuse existing authorization without moving secrets."""
    home = Path(home) if home is not None else Path.home()
    preferred = home / '.seedline-import'
    legacy = home / '.acebro-import'
    for directory in (preferred, legacy):
        profile = directory / 'profile.json'
        pending = directory / '.profile.json.connection' / 'state.json'
        if profile.exists() or profile.is_symlink() or pending.exists() or pending.is_symlink():
            return directory
    return preferred


def canonical_origin(value):
    error = "服务地址须为HTTPS origin；本地开发仅支持http://127.0.0.1或http://[::1]及有效端口"
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError(error)
    p = urlsplit(value)
    local_http = p.scheme == "http" and p.hostname in {"127.0.0.1", "::1"}
    if (
        (p.scheme != "https" and not local_http)
        or not p.hostname
        or p.path not in {"", "/"}
        or "?" in value
        or "#" in value
        or p.username is not None
        or p.password is not None
        or p.netloc.endswith(":")
    ):
        raise ValueError(error)
    port = p.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(error)
    host = p.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    default_port = 80 if p.scheme == "http" else 443
    return p.scheme + "://" + host + (f":{port}" if port and port != default_port else "")


@dataclass(frozen=True)
class Profile:
    origin: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "origin", canonical_origin(self.origin))
        decode_secret(self.secret_key)


def atomic_private_json(path, data):
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        protect_file(fd, name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_config(path, origin, access_key, secret_key):
    profile = Profile(origin, access_key, secret_key)
    atomic_private_json(
        path,
        {"origin": profile.origin, "access_key": access_key, "secret_key": secret_key},
    )
    return profile


def load_config(path):
    path = Path(path)
    if not path.exists():
        raise ValueError("尚未配置凭据，请由用户在本机交互终端运行 seedline-import configure")
    check_private(path)
    from question_content_contracts.validation import load_json

    value = load_json(path.read_bytes())
    return Profile(value["origin"], value["access_key"], value["secret_key"])
