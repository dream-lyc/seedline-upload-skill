from __future__ import annotations

import re
from typing import Any, cast
from uuid import UUID

from .errors import ContractError


def validate_context_shape(
    purpose: str,
    context: Any,
    *,
    allow_empty_targets: bool = False,
) -> dict[str, Any]:
    def invalid() -> ContractError:
        return ContractError(422, "invalid_media_context", "媒体关联字段无效")

    def identifier(value: Any) -> str:
        try:
            parsed = UUID(str(value))
            if parsed.int == 0:
                raise ValueError
            return str(parsed)
        except (ValueError, TypeError, AttributeError) as error:
            raise invalid() from error

    def item(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise invalid()
        return value

    if not isinstance(context, dict):
        raise invalid()
    context = cast(dict[str, Any], context)
    if purpose == "toefl-answer-audio":
        if set(context) != {"attempt_id", "response_id", "intent_id"}:
            raise invalid()
        return {key: identifier(value) for key, value in context.items()}
    if purpose == "ielts-answer-audio":
        if set(context) != {"attempt_id", "question_id", "item_id"}:
            raise invalid()
        return {
            "attempt_id": identifier(context["attempt_id"]),
            "question_id": identifier(context["question_id"]),
            "item_id": item(context["item_id"]),
        }
    if purpose == "ielts-explanation-video":
        targets = context.get("targets")
        if set(context) != {"targets"} or not isinstance(targets, list):
            raise invalid()
        targets = cast(list[Any], targets)
        if (not targets and not allow_empty_targets) or len(targets) > 1000:
            raise invalid()
        normalized: list[dict[str, str]] = []
        for target in targets:
            if not isinstance(target, dict):
                raise invalid()
            target = cast(dict[str, Any], target)
            if set(target) == {"paper_id"}:
                result = {"paper_id": identifier(target["paper_id"])}
            elif set(target) in ({"question_id"}, {"question_id", "item_id"}):
                result = {"question_id": identifier(target["question_id"])}
                if "item_id" in target:
                    result["item_id"] = item(target["item_id"])
            else:
                raise invalid()
            if result in normalized:
                raise invalid()
            normalized.append(result)
        return {"targets": normalized}
    if context:
        raise invalid()
    return {}
