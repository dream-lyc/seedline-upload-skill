"""Deterministic rule-ID executor, shared by package validation and the SDK."""

from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from . import PREVIEW_VERSION, VALIDATOR_VERSION, __version__
from .ielts.papers import validate_paper
from .ielts.validation import parse_package_json, validate_package
from .protocol import MODELS, Submission
from .toefl.policies import default_rules, default_scoring, validate_form
from .toefl.validation import ContentError, validate_item

RULES = {
    "protocol.submission.v1": {"scope": "batch"},
    "ielts.package.publish.v1": {"scope": "ielts_question"},
    "ielts.paper.publish.v1": {"scope": "ielts_paper"},
    "toefl.item.publish.v1": {"scope": "toefl_item"},
    "toefl.form.publish.v1": {"scope": "toefl_form"},
    "media.context.v1": {"scope": "media"},
}


def load_json(raw: str | bytes) -> dict[str, Any]:
    return parse_package_json(raw)


def issue(
    code: str, message: str, *, path: str = "", obj: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "object_id": obj.get("id") if obj else None,
        "kind": obj.get("kind") if obj else None,
        "path": path,
        "code": code,
        "message": message,
    }


def compatibility_errors(contract: dict[str, Any]) -> list[dict[str, Any]]:
    errors = []
    from .contracts import build_contract

    if contract.get("exam") not in {"ielts", "toefl"}:
        return [issue("unsupported_exam", "不支持该考试体系")]
    expected = build_contract(contract["exam"], "1.0.0")["schemas"]
    if contract.get("schemas") != expected:
        errors.append(
            issue(
                "schema_version_mismatch",
                "固定契约Schema与本校验器不一致，请更新SDK；不加载外部引用",
                path="schemas",
            )
        )
    for rule in contract.get("rules", []):
        if rule.get("id") not in RULES:
            errors.append(
                issue(
                    "unknown_rule",
                    "本地不支持契约规则，请更新SDK",
                    path=str(rule.get("id")),
                )
            )
        elif rule.get("parameters", {}) != RULES[rule["id"]]:
            errors.append(
                issue(
                    "rule_parameters_unsupported",
                    "规则参数与本地校验器不匹配，请更新SDK",
                    path=rule["id"],
                )
            )
    required = {"protocol.submission.v1", "media.context.v1"} | (
        {"ielts.package.publish.v1", "ielts.paper.publish.v1"}
        if contract.get("exam") == "ielts"
        else {"toefl.item.publish.v1", "toefl.form.publish.v1"}
    )
    if not required <= {r.get("id") for r in contract.get("rules", [])}:
        errors.append(issue("missing_rule", "契约缺少必需规则"))
    for field, wanted in [
        ("validator_version", VALIDATOR_VERSION),
        ("preview_version", PREVIEW_VERSION),
    ]:
        if contract.get(field) != wanted:
            errors.append(
                issue(
                    "component_version_mismatch",
                    f"{field}版本不匹配，请更新SDK",
                    path=field,
                )
            )
    try:
        if tuple(map(int, contract["minimum_sdk_version"].split("."))) > tuple(
            map(int, __version__.split("."))
        ):
            errors.append(issue("sdk_too_old", "请更新SDK", path="minimum_sdk_version"))
    except (KeyError, ValueError, TypeError):
        errors.append(issue("invalid_sdk_version", "契约最低SDK版本无效"))
    return errors


def expand_defaults(
    objects: list[dict[str, Any]], contract: dict[str, Any]
) -> list[dict[str, Any]]:
    result = deepcopy(objects)
    if contract.get("exam") == "toefl":
        # Fixed defaults must match the executable version; never execute remote code.
        expected = {"rules": default_rules({}), "scoring": default_scoring({})}
        if contract.get("defaults") != expected:
            raise ValueError("固定契约默认值与本地校验器不匹配，请更新SDK")
        for obj in result:
            if obj["kind"] == "toefl_form":
                obj["data"]["rules"] = default_rules(obj["data"].get("rules", {}))
                obj["data"]["scoring"] = default_scoring(obj["data"].get("scoring", {}))
    return result


def validate_objects(
    objects: list[dict[str, Any]],
    *,
    exam: str,
    media: dict[str, dict[str, Any]] | dict[UUID, dict[str, Any]] | None = None,
    dependencies: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    errors = []
    media = media or {}
    known = {str(k): v for k, v in (dependencies or {}).items()}
    ids = set()
    for obj in objects:
        if obj.get("id") in ids:
            errors.append(issue("duplicate_object_id", "同批对象ID不得重复", obj=obj, path="id"))
        ids.add(obj.get("id"))
        kind = obj.get("kind", "")
        if kind not in MODELS or not kind.startswith(exam + "_"):
            errors.append(
                issue(
                    "unsupported_kind",
                    "题型或考试体系不受当前契约支持",
                    obj=obj,
                    path="kind",
                )
            )
            continue
        try:
            from .protocol import ImportObject

            ImportObject.model_validate(obj)
            MODELS[kind].model_validate(obj["data"])
        except ValidationError as error:
            for e in error.errors(include_url=False, include_input=False):
                errors.append(
                    issue(
                        "schema." + e["type"],
                        "字段结构无效：" + e["msg"],
                        path=".".join(map(str, e["loc"])),
                        obj=obj,
                    )
                )
            continue
        data = deepcopy(obj["data"])
        known[obj["id"]] = data | {"status": "available" if kind == "toefl_item" else "published"}
    if errors:
        return errors
    for obj in objects:
        kind = obj["kind"]
        data = obj["data"]
        found = []
        if kind == "ielts_question":
            found = validate_package(
                data, publish=True, media={UUID(str(k)): v for k, v in media.items()}
            )
        elif kind == "ielts_paper":
            found = validate_paper(data, {UUID(k): v for k, v in known.items()}, publish=True)
        elif kind == "toefl_item":
            try:
                validate_item(data, publish=True, media={str(k): v for k, v in media.items()})
            except ContentError as error:
                found = [e | {"code": e.get("code", "toefl_content_invalid")} for e in error.errors]
        else:
            try:
                rules = default_rules(data["rules"])
                scoring = default_scoring(data["scoring"])
                if data["rules"] != rules or data["scoring"] != scoring:
                    found = [
                        {
                            "path": "rules/scoring",
                            "code": "defaults_not_expanded",
                            "message": "请先按固定契约在本地展开完整规则和评分配置",
                        }
                    ]
                else:
                    validate_form(data["modules"], known, rules, scoring)
            except (ValueError, KeyError, TypeError) as error:
                found = [
                    {
                        "path": "modules/rules/scoring",
                        "code": "toefl_form_invalid",
                        "message": str(error),
                    }
                ]
        errors.extend(
            issue(
                e.get("code", "content_invalid"),
                e["message"],
                path=e.get("path", ""),
                obj=obj,
            )
            for e in found
        )
    return errors


def validate_submission(payload, contract, *, media=None, dependencies=None, limits=None):
    errors = compatibility_errors(contract)
    if errors:
        return errors
    from jsonschema import Draft202012Validator

    for error in Draft202012Validator(contract["schemas"]["submission"]).iter_errors(payload):
        errors.append(
            issue(
                "json_schema",
                "提交Schema不通过：" + error.message,
                path=".".join(map(str, error.absolute_path)),
            )
        )
    for obj in payload.get("objects", []):
        schema = contract["schemas"].get(obj.get("kind"))
        if schema:
            for error in Draft202012Validator(schema).iter_errors(obj.get("data")):
                errors.append(
                    issue(
                        "json_schema",
                        "题目Schema不通过：" + error.message,
                        path=".".join(map(str, error.absolute_path)),
                        obj=obj,
                    )
                )
    if errors:
        return errors
    try:
        parsed = Submission.model_validate(payload)
    except ValidationError as error:
        return [
            issue(
                "schema." + e["type"],
                "提交包络字段无效：" + e["msg"],
                path=".".join(map(str, e["loc"])),
            )
            for e in error.errors(include_url=False, include_input=False)
        ]
    if parsed.exam != contract["exam"] or parsed.contract_version != contract["contract_version"]:
        return [issue("contract_mismatch", "批次考试或固定契约版本不匹配")]
    active_limits = {k: min(v, (limits or {}).get(k, v)) for k, v in contract["limits"].items()}
    if len(parsed.objects) > active_limits["max_submission_objects"]:
        errors.append(issue("object_limit", "批次对象数超过目标环境限制", path="objects"))
    import json

    if (
        len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        )
        > active_limits["max_submission_body_bytes"]
    ):
        errors.append(issue("body_limit", "内容字节数超过目标环境限制"))
    ids = {o.id for o in parsed.objects}
    for index, source in enumerate(parsed.source_manifest):
        if source.object_id not in ids:
            errors.append(
                issue(
                    "source_object_missing",
                    "来源清单必须关联本批真实对象",
                    path=f"source_manifest.{index}.object_id",
                )
            )
        if source.locator:
            from urllib.parse import urlsplit

            parts = urlsplit(source.locator)
            if parts.username or parts.password or parts.query or parts.fragment:
                errors.append(
                    issue(
                        "source_secret_risk",
                        "来源URL请移除查询令牌和私密片段",
                        path=f"source_manifest.{index}.locator",
                    )
                )
    if errors:
        return errors
    return validate_objects(
        payload["objects"], exam=parsed.exam, media=media, dependencies=dependencies
    )
