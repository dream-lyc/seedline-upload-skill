"""Atomic durable operation facts; no inferred success and no regenerated identities."""

import os
import secrets
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from question_content_contracts.validation import load_json

from .config import atomic_private_json, canonical_origin
from .local_files import lock_exclusive


def uuid7():
    timestamp = int(time.time() * 1000)
    return str(
        UUID(
            int=(timestamp << 80)
            | (7 << 76)
            | (secrets.randbits(12) << 64)
            | (2 << 62)
            | secrets.randbits(62)
        )
    )


class BatchLedger:
    def __init__(self, root, data):
        self.root = Path(root)
        self.data = data

    @classmethod
    def create(cls, root, *, origin, user_id, config_ref, contract, objects, source_manifest):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        prepared = deepcopy(objects)
        refs = {}
        for obj in prepared:
            obj.setdefault("id", uuid7())
            if "ref" in obj:
                refs["@object:" + obj.pop("ref")] = obj["id"]

        def replace(value):
            if isinstance(value, str):
                return refs.get(value, value)
            if isinstance(value, list):
                return [replace(v) for v in value]
            if isinstance(value, dict):
                return {k: replace(v) for k, v in value.items()}
            return value

        prepared = replace(prepared)
        source_manifest = replace(deepcopy(source_manifest))
        data = {
            "ledger_version": 1,
            "batch_id": uuid7(),
            "origin": canonical_origin(origin),
            "user_id": user_id,
            "config_ref": str(config_ref),
            "contract": deepcopy(contract),
            "objects": prepared,
            "source_manifest": source_manifest,
            "media": [],
            "media_metadata": {},
            "dependencies": {},
            "operations": {},
            "state": "prepared",
            "issues": [],
        }
        ledger = cls(root, data)
        ledger.save()
        return ledger

    @classmethod
    def open(cls, root, *, origin, user_id):
        root = Path(root)
        data = load_json((root / "ledger.json").read_bytes())
        if data["origin"] != canonical_origin(origin):
            raise ValueError("批次服务地址不匹配，不能跨环境恢复")
        if data["user_id"] != user_id:
            raise ValueError("批次管理员账号不匹配，不能跨账号恢复")
        if data.get("ledger_version") != 1:
            raise ValueError("账本版本不支持")
        return cls(root, data)

    def save(self):
        atomic_private_json(self.root / "ledger.json", self.data)

    @contextmanager
    def lock(self):
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                lock_exclusive(fd)
            except BlockingIOError as error:
                raise ValueError("此批次正在由另一进程操作") from error
            self.data = load_json((self.root / "ledger.json").read_bytes())
            yield
        finally:
            os.close(fd)

    def prepare_operation(self, name, operation, facts):
        old = self.data["operations"].get(name)
        if old:
            if old["operation"] != operation or old["facts"] != facts:
                raise ValueError("已有操作请求事实不可变更，请为修改内容建立新批次")
            return old
        value = {
            "operation": operation,
            "idempotency_key": uuid7(),
            "facts": deepcopy(facts),
            "state": "prepared",
            "result": None,
            "error": None,
            "request_id": None,
            "attempts": 0,
            "newly_created": False,
        }
        self.data["operations"][name] = value
        self.save()
        return value

    def update_operation(self, name, **changes):
        self.data["operations"][name].update(changes)
        self.save()
