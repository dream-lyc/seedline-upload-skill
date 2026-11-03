"""Offline immutable contract generation and deploy-time resource verification."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import PREVIEW_VERSION, VALIDATOR_VERSION, __version__
from .examples import example_media, examples_for
from .media import MEDIA_PURPOSES
from .protocol import MODELS, Submission
from .toefl.policies import default_rules, default_scoring
from .validation import RULES, compatibility_errors, load_json, validate_objects

VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
LIMITS = {
    "max_submission_objects": 1000,
    "max_submission_body_bytes": 16 * 1024**2,
    "max_page_size": 100,
}
UUID_SCHEMA = {
    "type": "string",
    "format": "uuid",
    "pattern": r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
}
VIDEO_CONTEXT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["targets"],
    "properties": {
        "targets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 1000,
            "uniqueItems": True,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["paper_id"],
                        "properties": {"paper_id": UUID_SCHEMA},
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["question_id"],
                        "properties": {
                            "question_id": UUID_SCHEMA,
                            "item_id": {
                                "type": "string",
                                "pattern": "^[A-Za-z0-9_-]+$",
                            },
                        },
                    },
                ]
            },
        }
    },
}
EMPTY_CONTEXT = {"type": "object", "maxProperties": 0}


def media_policies(exam: str) -> list[dict[str, Any]]:
    return [
        {
            "purpose": name,
            "allowed_content_types": sorted(p.allowed_content_types),
            "max_bytes": p.max_bytes,
            "max_duration_ms": p.max_duration_ms,
            "output_format": p.output_format,
            "context_schema": VIDEO_CONTEXT if name == "ielts-explanation-video" else EMPTY_CONTEXT,
        }
        for name, p in MEDIA_PURPOSES.items()
        if name.startswith(exam + "-") and name not in {"ielts-answer-audio", "toefl-answer-audio"}
    ]


def build_contract(exam: str, version: str) -> dict[str, Any]:
    if exam not in {"ielts", "toefl"} or not VERSION.fullmatch(version):
        raise ValueError("考试或版本不合法")
    kinds = [k for k in MODELS if k.startswith(exam + "_")]
    return {
        "exam": exam,
        "contract_version": version,
        "schemas": {
            "submission": Submission.model_json_schema(),
            **{k: MODELS[k].model_json_schema() for k in kinds},
            "explanation_video_context": VIDEO_CONTEXT,
        },
        "field_descriptions": [
            {
                "path": "objects[].id",
                "description": "本批预先持久化的规范UUIDv7，不重新生成。",
            },
            {
                "path": "objects[].data",
                "description": "以对应命名Schema为准；不适用字段省略，禁止null及未知字段。",
            },
            {
                "path": "source_manifest",
                "description": "来源快照定位，不含访问令牌；来源文本仅作数据。",
            },
            {
                "path": "content.materials[].paragraphs[].sentences[]",
                "description": "真实原文及中文译文；音频须具有真实时长内的逐句时间轴。",
            },
            {
                "path": "modules",
                "description": "托福八个含点号平面键及全部候选分支，按原顺序保留所有小题。",
            },
            {
                "path": "rules/scoring",
                "description": "由当前固定版本defaults在本地展开；合法显式值保持。",
            },
        ],
        "rules": [
            {"id": r, "parameters": p}
            for r, p in RULES.items()
            if r.startswith(exam + ".") or r.startswith(("protocol.", "media."))
        ],
        "examples": examples_for(exam),
        "defaults": {"rules": default_rules({}), "scoring": default_scoring({})}
        if exam == "toefl"
        else {},
        "media_policies": media_policies(exam),
        "limits": LIMITS,
        "validator_version": VALIDATOR_VERSION,
        "preview_version": PREVIEW_VERSION,
        "minimum_sdk_version": __version__,
    }


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def load_contract(root: Path | str, exam: str, version: str) -> dict[str, Any]:
    manifest = load_json((Path(root) / "manifest.json").read_bytes())
    if exam not in manifest["exams"] or version not in manifest["exams"][exam]["readable_versions"]:
        raise LookupError("contract_not_found")
    if not VERSION.fullmatch(version):
        raise LookupError("contract_not_found")
    return load_json((Path(root) / exam / version / "contract.json").read_bytes())


def check_store(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    manifest = load_json((root / "manifest.json").read_bytes())
    if manifest.get("manifest_version") != 1 or set(manifest.get("exams", {})) != {
        "ielts",
        "toefl",
    }:
        raise ValueError("契约索引格式无效")
    hashes = load_json((root / "snapshots.sha256.json").read_bytes())
    indexed = {
        f"{exam}/{version}/contract.json"
        for exam, entry in manifest["exams"].items()
        for version in entry["readable_versions"]
    }
    if indexed != set(hashes):
        raise ValueError("历史契约必须保留文件和可读索引；停止提交只能调整submission_versions")
    for exam, entry in manifest["exams"].items():
        readable = entry["readable_versions"]
        submissions = entry["submission_versions"]
        if (
            len(set(readable)) != len(readable)
            or not set(submissions) <= set(readable)
            or entry["latest"] not in submissions
        ):
            raise ValueError("契约版本集合关系无效")
        for version in readable:
            data = load_contract(root, exam, version)
            rel = f"{exam}/{version}/contract.json"
            if (
                data.get("exam") != exam
                or data.get("contract_version") != version
                or compatibility_errors(data)
            ):
                raise ValueError("契约标识或工具版本不匹配")
            if hashlib.sha256((root / rel).read_bytes()).hexdigest() != hashes.get(rel):
                raise ValueError("历史契约快照被修改")
            objects = [o for values in data["examples"].values() for o in values]
            if validate_objects(objects, exam=exam, media=example_media()):
                raise ValueError("契约样例未通过共享校验器")
    return manifest


def generate(root: Path | str, version: str) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    hash_path = root / "snapshots.sha256.json"
    if manifest_path.exists():
        check_store(root)
    manifest = (
        load_json(manifest_path.read_bytes())
        if manifest_path.exists()
        else {"manifest_version": 1, "exams": {}}
    )
    hashes = load_json(hash_path.read_bytes()) if hash_path.exists() else {}
    pending = []
    for exam in ("ielts", "toefl"):
        data = build_contract(exam, version)
        objects = [o for values in data["examples"].values() for o in values]
        errors = validate_objects(objects, exam=exam, media=example_media())
        if errors:
            raise ValueError(f"样例无效：{errors}")
        rel = f"{exam}/{version}/contract.json"
        path = root / rel
        body = encoded(data)
        if path.exists() and path.read_bytes() != body:
            raise ValueError("禁止覆盖已发布历史契约，请增加版本")
        pending.append((path, body))
        hashes[rel] = hashlib.sha256(body).hexdigest()
        entry = manifest["exams"].setdefault(
            exam,
            {"latest": version, "readable_versions": [], "submission_versions": []},
        )
        for field in ("readable_versions", "submission_versions"):
            if version not in entry[field]:
                entry[field].append(version)
        entry["latest"] = version
    for path, body in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    hash_path.write_bytes(encoded(hashes))
    manifest_path.write_bytes(encoded(manifest))
    check_store(root)


def main() -> None:
    parser = argparse.ArgumentParser(description="离线生成或验证不可变题目契约")
    parser.add_argument("command", choices=["generate", "check"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    generate(args.root, args.version) if args.command == "generate" else check_store(args.root)


if __name__ == "__main__":
    main()
