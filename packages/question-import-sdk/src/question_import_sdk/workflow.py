"""Version-pinned local validation → media → final bytes → direct publish → video."""

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import httpx
from question_content_contracts.validation import (
    compatibility_errors,
    expand_defaults,
    issue,
    load_json,
    validate_submission,
)

from .client import ApiError
from .config import atomic_private_json
from .ledger import BatchLedger, uuid7
from .local_files import open_regular
from .media import multipart_file, sha256_file, snapshot_media
from .review import canonical_bytes, fingerprint, media_aliases, rendered_objects, replace

REQUIRED_SCOPES = {
    "contracts:read",
    "content:read",
    "content:publish",
    "media:read",
    "media:write",
    "submissions:read",
}


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def replace_refs(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_refs(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: replace_refs(v, mapping) for k, v in value.items()}
    return value


class ContractCache:
    def __init__(self, root):
        self.root = Path(root)

    def pin(self, origin, contract):
        errors = compatibility_errors(contract)
        if errors:
            raise ValueError("; ".join(e["message"] for e in errors))
        import re

        version = contract["contract_version"]
        exam = contract["exam"]
        if exam not in {"ielts", "toefl"} or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("契约标识不合法")
        path = (
            self.root
            / hashlib.sha256(origin.encode()).hexdigest()
            / exam
            / version
            / "contract.json"
        )
        if path.exists() and load_json(path.read_bytes()) != contract:
            raise ValueError("同一服务同一契约版本发生漂移，已停止")
        if not path.exists():
            atomic_private_json(path, contract)
        return deepcopy(contract)


class ImportWorkflow:
    def __init__(self, client, cache_dir):
        self.client = client
        self.cache = ContractCache(cache_dir)
        self.identity = None
        self.created_this_run = set()

    def identify(self):
        me = self.client.me()
        if me.get("role") != "admin":
            raise ValueError("此工作流仅供有效平台管理员使用")
        missing = REQUIRED_SCOPES - set(me.get("scopes", []))
        if missing:
            raise ValueError("凭据缺少权限：" + ", ".join(sorted(missing)))
        self.identity = me
        return me

    def create_batch(
        self,
        root,
        exam,
        objects,
        source_manifest,
        *,
        media=None,
        config_ref="",
        dependencies=None,
    ):
        me = self.identify()
        contract = self.cache.pin(self.client.profile.origin, self.client.contract(exam))
        batch = BatchLedger.create(
            root,
            origin=self.client.profile.origin,
            user_id=me["user_id"],
            config_ref=config_ref,
            contract=contract,
            objects=objects,
            source_manifest=source_manifest,
        )
        object_refs = {
            "@object:" + source["ref"]: prepared["id"]
            for source, prepared in zip(objects, batch.data["objects"], strict=True)
            if "ref" in source
        }
        try:
            batch.data["objects"] = expand_defaults(batch.data["objects"], contract)
            batch.data["dependencies"] = deepcopy(dependencies or {})
            for entry in media or []:
                item = snapshot_media(batch, replace_refs(entry, object_refs))
                batch.data["media"].append(item)
                batch.data["media_metadata"][item["ref"]] = item["metadata"]
            batch.save()
        except (ValueError, OSError, KeyError) as error:
            batch.data.update(
                state="local_correction_required",
                preparation_errors=[issue("local_media_invalid", str(error))],
                issues=[issue("local_media_invalid", str(error))],
            )
            batch.save()
        return batch

    def revise_batch(self, batch, candidate):
        with batch.lock():
            if "content" in batch.data["operations"]:
                raise ValueError("内容已冻结，不能修改原操作；请保留账本并为新内容建立新批次")
            if candidate.get("exam") != batch.data["contract"]["exam"]:
                raise ValueError("批次考试体系不能改变")
            new = candidate.get("objects", [])
            if {o.get("id") for o in new} != {o["id"] for o in batch.data["objects"]}:
                raise ValueError("修正须保留原对象ID，不能重建身份")
            batch.data["objects"] = expand_defaults(new, batch.data["contract"])
            if "source_manifest" in candidate:
                batch.data["source_manifest"] = deepcopy(candidate["source_manifest"])
            existing = {entry["ref"]: index for index, entry in enumerate(batch.data["media"])}
            for entry in candidate.get("media", []):
                if entry.get("ref") in existing:
                    op = batch.data["operations"].get("media:" + entry["ref"])
                    if op:
                        candidate_facts = {
                            "file_sha256": sha256_file(entry["path"]),
                            "purpose": entry["purpose"],
                            "content_type": entry["content_type"].split(";", 1)[0].strip().lower(),
                            "context": entry.get("context", {}),
                        }
                        if candidate_facts != op["facts"]:
                            raise ValueError(
                                "媒体操作事实已冻结，不能修改源文件或用途；请建立新的媒体操作"
                            )
                        continue
                    item = snapshot_media(batch, entry)
                    batch.data["media"][existing[entry["ref"]]] = item
                else:
                    item = snapshot_media(batch, entry)
                    batch.data["media"].append(item)
                batch.data["media_metadata"][item["ref"]] = item["metadata"]
            batch.data.update(state="prepared", issues=[], preparation_errors=[])
            batch.save()

    def replace_media(self, batch, reference, candidate):
        with batch.lock():
            entries = batch.data["media"]
            index = next((i for i, item in enumerate(entries) if item["ref"] == reference), None)
            if index is None:
                raise ValueError("原媒体引用不存在")
            old = entries[index]
            old_op = batch.data["operations"].get("media:" + reference)
            if old_op and old_op["state"] not in {"failed", "prepared"}:
                raise ValueError("请先查询原媒体结果；已成功或结果未知的操作不能直接替换")
            if "content" in batch.data["operations"] and not old["after_content"]:
                raise ValueError("内容已冻结，必需媒体不能改动；可选视频可单独建立新操作")
            entry = deepcopy(candidate)
            entry["ref"] = uuid7()
            entry["purpose"] = old["purpose"]
            entry["after_content"] = old["after_content"]
            item = snapshot_media(batch, entry)
            entries[index] = item
            if not old["after_content"]:
                batch.data["objects"] = replace_refs(
                    batch.data["objects"], {reference: item["ref"]}
                )
            batch.data["media_metadata"][item["ref"]] = item["metadata"]
            if old_op:
                old_op.update(state="superseded", replacement_reference=item["ref"])
            batch.data.update(state="prepared", issues=[], preparation_errors=[])
            batch.save()
            return item

    def open_batch(self, root):
        me = self.identify()
        batch = BatchLedger.open(root, origin=self.client.profile.origin, user_id=me["user_id"])
        self.cache.pin(batch.data["origin"], batch.data["contract"])
        return batch

    def _dependencies(self, batch):
        local = {o["id"] for o in batch.data["objects"]}
        references = {}
        for obj in batch.data["objects"]:
            if obj["kind"] == "ielts_paper":
                references.update(
                    {
                        p["question_id"]: "ielts_question"
                        for p in obj["data"].get("content", {}).get("parts", [])
                    }
                )
            elif obj["kind"] == "toefl_form":
                references.update(
                    {
                        ref: "toefl_item"
                        for refs in obj["data"].get("modules", {}).values()
                        for ref in refs
                    }
                )
        for ref, kind in references.items():
            if ref not in local:
                value = self.client.content(kind, ref)
                batch.data["dependencies"][ref] = value["data"] | {"status": value["status"]}
        media_refs = set()

        def collect(value):
            if isinstance(value, dict):
                if value.get("file"):
                    media_refs.add(value["file"])
                for v in value.values():
                    collect(v)
            elif isinstance(value, list):
                for v in value:
                    collect(v)

        collect(batch.data["objects"])
        local_media = {m["ref"] for m in batch.data["media"]}
        for ref in media_refs - local_media:
            if ref not in batch.data["media_metadata"]:
                batch.data["media_metadata"][ref] = self.client.media(ref)
        batch.save()

    def validate(self, batch):
        payload = {
            "exam": batch.data["contract"]["exam"],
            "contract_version": batch.data["contract"]["contract_version"],
            "objects": batch.data["objects"],
            "source_manifest": batch.data["source_manifest"],
        }
        return validate_submission(
            payload,
            batch.data["contract"],
            media=batch.data["media_metadata"],
            dependencies=batch.data["dependencies"],
            limits=(self.identity or {}).get("limits", {}),
        )

    def confirmation(self, batch):
        try:
            current = fingerprint(batch.data, batch.root)
        except (ValueError, OSError, KeyError, TypeError):
            return {"state": "unavailable"}
        saved = batch.data.get("publication_confirmation")
        return {
            "state": (
                "missing"
                if not saved
                else "confirmed"
                if saved.get("fingerprint") == current
                else "stale"
            ),
            "fingerprint": current,
        }

    def confirm(self, batch, preview_path):
        import os
        import re
        import stat
        from datetime import UTC, datetime

        me = self.identify()
        with batch.lock():
            if (
                batch.data["origin"] != self.client.profile.origin
                or batch.data["user_id"] != me["user_id"]
            ):
                raise ValueError("批次站点或管理员不匹配，不能确认发布")
            self.cache.pin(batch.data["origin"], batch.data["contract"])
            self._dependencies(batch)
            issues = self.validate(batch)
            if issues or batch.data.get("preparation_errors"):
                raise ValueError("本地校验尚未通过，请先补齐试题并展示最新预览")
            path = Path(preview_path).expanduser().absolute()
            fd = open_regular(path)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("确认必须指向本轮完整预览文件")
                content = stream.read()
            digest = hashlib.sha256(content).hexdigest()
            if path.name != "preview-" + digest + ".html":
                raise ValueError("请确认本轮新链接对应的完整预览，不能使用已改变的快照")
            match = re.search(
                rb'<script id="acebro-preview-data" type="application/json">(.*?)</script>',
                content,
                re.S,
            )
            if not match:
                raise ValueError("预览缺少可核对的批次版本，请重新导出")
            preview = load_json(match[1])
            if (
                not isinstance(preview, dict)
                or set(preview) != {"preview_version", "objects", "media", "publication_review"}
                or preview["preview_version"] != batch.data["contract"]["preview_version"]
                or not isinstance(preview["objects"], list)
                or not isinstance(preview["media"], dict)
                or not all(isinstance(value, dict) for value in preview["media"].values())
            ):
                raise ValueError("预览格式与当前工具不一致，请重新导出")
            current = fingerprint(batch.data, batch.root)
            if preview.get("publication_review") != {
                "batch_id": batch.data["batch_id"],
                "fingerprint": current,
            }:
                raise ValueError("预览已过期或属于另一批次，请展示最新预览后重新确认")
            aliases = media_aliases(batch.data)
            if canonical_bytes(replace(preview.get("objects"), aliases)) != canonical_bytes(
                replace(rendered_objects(batch.data), aliases)
            ):
                raise ValueError("预览内容与当前批次不一致，请重新导出")
            references = set()

            def collect_files(value):
                if isinstance(value, dict):
                    if value.get("file"):
                        references.add(value["file"])
                    for child in value.values():
                        collect_files(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_files(child)

            collect_files(preview["objects"])
            if not references.issubset(preview.get("media", {})):
                raise ValueError("预览缺少题目材料，请重新导出完整预览")
            for reference, material in preview.get("media", {}).items():
                url = material.get("url", "")
                if not re.fullmatch(r"\./preview-media/[0-9a-f]{64}\.[a-z0-9]+", url):
                    raise ValueError("预览材料路径无效，请重新导出")
                local = path.parent / url
                if local.is_symlink() or sha256_file(local) != local.stem:
                    raise ValueError("预览材料已改变，请重新导出并确认")
                original_ref = aliases.get(reference, reference)
                entry = next(
                    (item for item in batch.data["media"] if item["ref"] == original_ref), None
                )
                if entry and sha256_file(local) != sha256_file(batch.root / entry["source_file"]):
                    raise ValueError("预览材料与当前批次不同，请重新导出并确认")
            from .preview import PreviewServer, render_preview_document

            trusted = PreviewServer(batch, bind=False)
            if render_preview_document(trusted, preview).encode() != content:
                raise ValueError("预览页面已改变或不是当前可信模板，请重新导出后确认")
            frozen = batch.data["operations"].get("content")
            if frozen:
                body = base64.b64decode(frozen["facts"]["body_b64"])
                if hashlib.sha256(body).hexdigest() != frozen["facts"]["sha256"]:
                    raise ValueError("冻结请求损坏，不能重新确认")
                original = load_json(body)
                expected = {
                    "exam": batch.data["contract"]["exam"],
                    "contract_version": batch.data["contract"]["contract_version"],
                    "objects": batch.data["objects"],
                    "source_manifest": batch.data["source_manifest"],
                }
                if canonical_bytes(replace(original, aliases)) != canonical_bytes(
                    replace(expected, aliases)
                ):
                    raise ValueError("当前内容与已冻结原请求不同，请为新内容建立新批次")
            batch.data["publication_confirmation"] = {
                "fingerprint": current,
                "preview_sha256": digest,
                "confirmed_at": datetime.now(UTC).isoformat(),
            }
            batch.data["confirmation_required"] = False
            batch.save()
            return self.report(batch)

    def query_operation(self, batch, name):
        op = batch.data["operations"][name]
        try:
            records = self.client.find_submission(op["operation"], op["idempotency_key"])
        except (ApiError, httpx.TransportError):
            return False
        if records.get("total") == 1:
            record = records["records"][0]
            if record["state"] == "succeeded":
                batch.update_operation(
                    name,
                    state="succeeded",
                    result=record["result"],
                    error=None,
                    newly_created=False,
                )
                return True
            if record["state"] == "failed":
                batch.update_operation(
                    name, state="failed", error=record["error"], newly_created=False
                )
            else:
                batch.update_operation(name, state="unknown", newly_created=False)
        return False

    def _execute(self, batch, name, send, *, retry=False):
        op = batch.data["operations"][name]
        if op["state"] == "succeeded":
            return True
        if op["state"] in {"inflight", "unknown"}:
            if self.query_operation(batch, name):
                return True
            if not retry:
                return False
        if op["state"] == "failed":
            if not retry or not op.get("error", {}).get("retryable", False):
                return False
        batch.update_operation(name, state="inflight", attempts=op["attempts"] + 1, error=None)
        try:
            result = send(op["idempotency_key"])
            # Only an actual confirmed/final state is success; accepted-only is unknown.
            expected = "confirmed" if op["operation"] == "media.upload" else "succeeded"
            if result.get("state") != expected or self.client.last_status not in {
                200,
                201,
            }:
                raise ApiError(
                    502,
                    "invalid_response",
                    "写请求未返回确认成功，结果待确认",
                    retryable=True,
                )
            created = self.client.last_status == 201 and not result.get("replayed", False)
            if created:
                self.created_this_run.add(name)
            batch.update_operation(
                name,
                state="succeeded",
                result=result,
                error=None,
                request_id=self.client.last_request_id,
                newly_created=created,
            )
            return True
        except httpx.TransportError:
            batch.update_operation(
                name,
                state="unknown",
                error={
                    "code": "result_unknown",
                    "message": "网络中断或超时，结果待确认；查询后由用户决定是否重试",
                    "retryable": True,
                },
            )
            return self.query_operation(batch, name)
        except ApiError as error:
            unknown = (
                error.code in {"invalid_response", "submission_in_progress"} or error.status >= 500
            )
            batch.update_operation(
                name,
                state="unknown" if unknown else "failed",
                error=error.public(),
                request_id=error.request_id,
            )
            if unknown:
                return self.query_operation(batch, name)
            return False

    def _upload(self, batch, entry, *, retry=False):
        name = "media:" + entry["ref"]
        facts = {key: entry[key] for key in ("file_sha256", "purpose", "content_type", "context")}
        op = batch.prepare_operation(name, "media.upload", facts)

        def send(key):
            file, content_type = multipart_file(batch, entry)
            try:
                return self.client.upload(file, content_type, key)
            finally:
                file.unlink(missing_ok=True)

        if not self._execute(batch, name, send, retry=retry):
            return False
        result = op["result"]
        if (
            result.get("source_sha256") != entry["file_sha256"]
            or result.get("purpose") != entry["purpose"]
            or not result.get("media_id")
        ):
            raise ValueError("媒体成功回执与本地原操作事实不匹配")
        batch.data["media_metadata"][result["media_id"]] = result
        batch.data["objects"] = replace_refs(
            batch.data["objects"], {entry["ref"]: result["media_id"]}
        )
        batch.save()
        return True

    def run(self, batch, *, retry_operations=None):
        self.created_this_run = set()
        retry_operations = set(retry_operations or [])
        me = self.identify()
        if (
            batch.data["origin"] != self.client.profile.origin
            or batch.data["user_id"] != me["user_id"]
        ):
            raise ValueError("批次服务地址或管理员账号不匹配")
        with batch.lock():
            self.cache.pin(batch.data["origin"], batch.data["contract"])
            try:
                if batch.data.get("preparation_errors"):
                    return self.report(batch)
                self._dependencies(batch)
                # Recovery may query existing unknown operations without permission to write.
                for name, pending in list(batch.data["operations"].items()):
                    if pending["state"] in {"unknown", "inflight"}:
                        self.query_operation(batch, name)
                confirmation = self.confirmation(batch)
                if confirmation["state"] != "confirmed":
                    frozen_content = batch.data["operations"].get("content")
                    issues = [] if frozen_content else self.validate(batch)
                    if confirmation["state"] == "unavailable" and not frozen_content:
                        issues = issues + [
                            issue(
                                "local_review_unavailable",
                                "本地材料或批次版本无法核对，请修正后重新预览",
                            )
                        ]
                    if issues:
                        batch.data.update(issues=issues, state="local_correction_required")
                        batch.save()
                        return self.report(batch)
                    # Already-created objects are historical success; never upload new video here.
                    no_writes_left = bool(
                        frozen_content
                        and frozen_content["state"] == "succeeded"
                        and all(
                            batch.data["operations"].get("media:" + entry["ref"], {}).get("state")
                            == "succeeded"
                            for entry in batch.data["media"]
                        )
                    )
                    batch.data["confirmation_required"] = not no_writes_left
                    batch.save()
                    return self.report(batch)
                batch.data["confirmation_required"] = False
                # Frozen content remains exactly the original request; never rebuild it for a retry.
                frozen = batch.data["operations"].get("content")
                if not frozen:
                    issues = self.validate(batch)
                    if issues:
                        batch.data.update(state="local_correction_required", issues=issues)
                        batch.save()
                        return self.report(batch)
                    for entry in batch.data["media"]:
                        if not entry["after_content"] and not self._upload(
                            batch,
                            entry,
                            retry="media:" + entry["ref"] in retry_operations,
                        ):
                            return self.report(batch)
                    issues = self.validate(batch)
                    if issues:
                        batch.data.update(state="local_correction_required", issues=issues)
                        batch.save()
                        return self.report(batch)

                    def unresolved(value):
                        if isinstance(value, dict):
                            reference = value.get("file")
                            if reference and batch.data["media_metadata"].get(reference, {}).get(
                                "local_reference"
                            ):
                                return True
                            return any(unresolved(child) for child in value.values())
                        return isinstance(value, list) and any(unresolved(child) for child in value)

                    if unresolved(batch.data["objects"]):
                        batch.data.update(
                            state="local_correction_required",
                            issues=[
                                issue(
                                    "local_media_unresolved",
                                    "必需媒体尚未回填真实媒体ID，不能冻结内容",
                                )
                            ],
                        )
                        batch.save()
                        return self.report(batch)
                    if self.confirmation(batch)["state"] != "confirmed":
                        batch.data["confirmation_required"] = True
                        batch.save()
                        return self.report(batch)
                    body = json_bytes(
                        {
                            "exam": batch.data["contract"]["exam"],
                            "contract_version": batch.data["contract"]["contract_version"],
                            "objects": batch.data["objects"],
                            "source_manifest": batch.data["source_manifest"],
                        }
                    )
                    batch.prepare_operation(
                        "content",
                        "content.submit",
                        {
                            "body_b64": base64.b64encode(body).decode(),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        },
                    )
                op = batch.data["operations"]["content"]
                body = base64.b64decode(op["facts"]["body_b64"])
                if hashlib.sha256(body).hexdigest() != op["facts"]["sha256"]:
                    raise ValueError("冻结内容字节损坏，已停止")
                original = load_json(body)
                expected = {
                    "exam": batch.data["contract"]["exam"],
                    "contract_version": batch.data["contract"]["contract_version"],
                    "objects": batch.data["objects"],
                    "source_manifest": batch.data["source_manifest"],
                }
                aliases = media_aliases(batch.data)
                if canonical_bytes(replace(original, aliases)) != canonical_bytes(
                    replace(expected, aliases)
                ):
                    raise ValueError("冻结请求与当前已确认内容不一致，已停止且保留原请求事实")
                if not self._execute(
                    batch,
                    "content",
                    lambda key: self.client.submit(body, key),
                    retry="content" in retry_operations,
                ):
                    return self.report(batch)
                for entry in batch.data["media"]:
                    if entry["after_content"]:
                        self._upload(
                            batch,
                            entry,
                            retry="media:" + entry["ref"] in retry_operations,
                        )
                batch.data["issues"] = []
                batch.data["state"] = self.batch_state(batch)
                batch.save()
            except (ValueError, OSError, KeyError, TypeError) as error:
                batch.data.update(
                    state="local_correction_required",
                    issues=[issue("local_preparation_failed", str(error))],
                )
                batch.save()
            return self.report(batch)

    def query(self, batch):
        with batch.lock():
            for name, op in batch.data["operations"].items():
                if op["state"] not in {"succeeded", "superseded"}:
                    self.query_operation(batch, name)
            batch.data["state"] = self.batch_state(batch)
            batch.save()
            return self.report(batch)

    def batch_state(self, batch):
        content = batch.data["operations"].get("content")
        active = [op for op in batch.data["operations"].values() if op["state"] != "superseded"]
        if any(op["state"] in {"inflight", "unknown"} for op in active):
            return "awaiting_result"
        if batch.data.get("issues") or batch.data.get("preparation_errors"):
            return "local_correction_required"
        if any(op["state"] == "failed" for op in active):
            return (
                "partial_failure"
                if content and content["state"] == "succeeded"
                else "request_failed"
            )
        if batch.data.get("confirmation_required"):
            return "awaiting_confirmation"
        if content and content["state"] == "succeeded":
            for entry in batch.data["media"]:
                op = batch.data["operations"].get("media:" + entry["ref"])
                if not op or op["state"] != "succeeded":
                    return "prepared"
            return "complete"
        return "prepared"

    def report(self, batch):
        operations = batch.data["operations"]
        content = operations.get("content")

        def public(op):
            return {k: deepcopy(v) for k, v in op.items() if k != "facts"}

        return {
            "batch_id": batch.data["batch_id"],
            "state": self.batch_state(batch),
            "issues": batch.data.get("issues", []),
            "confirmation": self.confirmation(batch),
            "content": public(content) if content else None,
            "media": {
                name: public(op) for name, op in operations.items() if name.startswith("media:")
            },
            "newly_created_count": content["result"].get("created_count", 0)
            if content and content["state"] == "succeeded" and "content" in self.created_this_run
            else 0,
            "pending_operations": [
                name
                for name, op in operations.items()
                if op["state"] not in {"succeeded", "superseded"}
            ],
        }
