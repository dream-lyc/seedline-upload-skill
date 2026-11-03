"""Pure local publication-review identity, independent of transport outcomes."""

import hashlib
import json
from copy import deepcopy

from .media import sha256_file


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def replace(value, aliases):
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [replace(item, aliases) for item in value]
    if isinstance(value, dict):
        return {key: replace(item, aliases) for key, item in value.items()}
    return value


def media_aliases(data):
    aliases = {}
    for entry in data["media"]:
        op = data["operations"].get("media:" + entry["ref"])
        if not op or op["state"] != "succeeded":
            continue
        result = op.get("result") or {}
        if (
            entry.get("file_sha256")
            and result.get("source_sha256") == entry["file_sha256"]
            and result.get("purpose") == entry.get("purpose")
            and result.get("media_id")
        ):
            aliases[result["media_id"]] = entry["ref"]
    return aliases


def rendered_objects(data):
    objects = deepcopy(data["objects"])
    local = {obj["id"] for obj in objects}
    for identity, dependency in data["dependencies"].items():
        if identity in local:
            continue
        kind = "ielts_question" if "skill" in dependency else "toefl_item"
        domain = {
            key: value
            for key, value in dependency.items()
            if key
            not in {
                "id",
                "kind",
                "status",
                "created_at",
                "updated_at",
                "revision",
                "deleted_id",
            }
        }
        objects.append({"id": identity, "kind": kind, "data": domain})
    return objects


def fingerprint(data, root):
    aliases = media_aliases(data)
    media = []
    for entry in data["media"]:
        actual = sha256_file(root / entry["source_file"])
        if entry.get("file_sha256") and actual != entry["file_sha256"]:
            raise ValueError("本地材料已改变，请重新整理并展示预览后确认")
        metadata = deepcopy(entry.get("metadata") or {})
        metadata.pop("local_reference", None)
        op = data["operations"].get("media:" + entry["ref"])
        if op and op["state"] == "succeeded":
            result = op.get("result") or {}
            for key in ("content_type", "byte_size", "duration_ms", "width", "height"):
                if key in result:
                    metadata[key] = result[key]
        media.append(
            {
                key: deepcopy(entry.get(key))
                for key in (
                    "ref",
                    "purpose",
                    "content_type",
                    "context",
                    "after_content",
                )
            }
            | {"source_sha256": actual, "metadata": metadata}
        )
    local_refs = {entry["ref"] for entry in data["media"]}
    # Upload receipts add IDs and transient protocol fields, not new reviewed content.
    remote = {
        key: value
        for key, value in data["media_metadata"].items()
        if key not in local_refs and key not in aliases
    }
    facts = {
        key: deepcopy(data[key])
        for key in (
            "batch_id",
            "origin",
            "user_id",
            "contract",
            "objects",
            "source_manifest",
            "dependencies",
        )
    }
    facts.update(media=sorted(media, key=lambda item: item["ref"]), remote_media=remote)
    return hashlib.sha256(canonical_bytes(replace(facts, aliases))).hexdigest()
