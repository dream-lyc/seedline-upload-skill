"""Versioned TOEFL rules. No database or IELTS dependencies."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, cast
from uuid import UUID

DEFAULTS: dict[str, Any] = json.loads(Path(__file__).with_name("defaults-v2.json").read_text())
QUOTAS = {
    "listening.router": {
        "listen_choose_response": 8,
        "listen_conversation": 4,
        "listen_announcement": 4,
        "listen_academic_talk": 4,
    },
    "listening.lower": {
        "listen_choose_response": 7,
        "listen_conversation": 4,
        "listen_announcement": 4,
    },
    "listening.upper": {
        "listen_choose_response": 3,
        "listen_conversation": 4,
        "listen_academic_talk": 8,
    },
    "writing.linear": {
        "build_sentence": 10,
        "write_email": 1,
        "academic_discussion": 1,
    },
    "speaking.linear": {"listen_repeat": 7, "take_interview": 4},
}
STAGES = ("reading.router", "reading.lower", "reading.upper", *QUOTAS)


def merge_missing(default: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(default)
    for key, value in supplied.items():
        if value is None:
            continue
        if key in result and isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"{key}必须为对象")
            result[key] = merge_missing(result[key], cast(dict[str, Any], value))
        else:
            result[key] = deepcopy(value)
    return result


def integer(value: Any, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("必须为合法整数")
    return value


def unscored_refs(rules: dict[str, Any]) -> set[tuple[str, str, str]]:
    if "unscoredQuestions" not in rules:
        return set()
    policy = rules["unscoredQuestions"]
    if not isinstance(policy, dict) or set(policy) != {"kind", "version", "note", "questions"}:
        raise ValueError("非计分配置字段无效")
    if (
        policy["kind"] != "ai-teaching"
        or type(policy["version"]) is not int
        or policy["version"] != 1
    ):
        raise ValueError("非计分配置仅支持平台AI教学版本1")
    if not isinstance(policy["note"], str) or not policy["note"].strip():
        raise ValueError("非计分配置必须提供平台AI教学说明")
    questions = policy["questions"]
    if not isinstance(questions, list) or not questions:
        raise ValueError("非计分题引用不能为空")
    refs: set[tuple[str, str, str]] = set()
    for ref in questions:
        if not isinstance(ref, dict) or set(ref) != {"stage", "itemId", "subItemKey"}:
            raise ValueError("非计分题引用字段无效")
        if ref["stage"] != "listening.router":
            raise ValueError("非计分题仅允许听力首阶段")
        identity, key = ref["itemId"], ref["subItemKey"]
        if not isinstance(identity, str):
            raise ValueError("非计分题itemId必须为规范非零UUID")
        try:
            parsed = UUID(identity)
        except ValueError as exc:
            raise ValueError("非计分题itemId必须为规范非零UUID") from exc
        if not parsed.int or str(parsed) != identity:
            raise ValueError("非计分题itemId必须为规范非零UUID")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
            raise ValueError("非计分题subItemKey无效")
        value = (ref["stage"], identity, key)
        if value in refs:
            raise ValueError("非计分题引用重复")
        refs.add(value)
    return refs


def is_scored(rules: dict[str, Any], stage: str, item_id: str, sub_item_key: str) -> bool:
    return (stage, item_id, sub_item_key) not in unscored_refs(rules)


def default_rules(supplied: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        *DEFAULTS["rulesJson"],
        "listeningQuestionSeconds",
        "source",
        "assemblyNotes",
        "unscoredQuestions",
    }
    if set(supplied) - allowed:
        raise ValueError("未知规则字段")
    for field in ("source", "assemblyNotes"):
        if supplied.get(field) is not None and not isinstance(supplied[field], dict):
            raise ValueError(f"{field}必须为对象")
    unscored_refs(supplied)
    result = merge_missing(DEFAULTS["rulesJson"], supplied)
    for group in ("timers", "listeningResponseSeconds", "readingSecondsPerItem"):
        for value in result[group].values():
            integer(value)
    if "listeningQuestionSeconds" in result:
        integer(result["listeningQuestionSeconds"])
    routing = result["routing"]
    for key, value in routing.items():
        if key not in {
            "readingUpperMinCorrect",
            "readingUpperMinPercent",
            "listeningUpperMinCorrect",
        }:
            raise ValueError("未知分流字段")
        integer(value, minimum=0)
    if routing["readingUpperMinPercent"] > 100 or routing["listeningUpperMinCorrect"] > 20:
        raise ValueError("分流阈值超出范围")
    return result


def reading_seconds(rules: dict[str, Any], stage: str, counts: dict[str, int]) -> int:
    if stage in rules["timers"]:
        return integer(rules["timers"][stage])
    return sum(
        integer(count, minimum=0) * rules["readingSecondsPerItem"][task]
        for task, count in counts.items()
    )


def routing_threshold(rules: dict[str, Any], section: str, count: int) -> int:
    routing = rules["routing"]
    if section == "reading":
        value = routing.get("readingUpperMinCorrect")
        if value is None:
            value = math.ceil(count * routing["readingUpperMinPercent"] / 100)
    else:
        value = routing["listeningUpperMinCorrect"]
    if value > count:
        raise ValueError("分流阈值超过实际题量")
    return integer(value, minimum=0)


def response_seconds(
    rules: dict[str, Any], task: str, item_id: str, question: dict[str, Any]
) -> int:
    timers = rules["timers"]
    if task.startswith("listen_") and task != "listen_repeat":
        return integer(
            rules.get("listeningQuestionSeconds", rules["listeningResponseSeconds"][task])
        )
    if task in {"listen_repeat", "take_interview"}:
        return integer(
            timers.get(
                f"speaking.{item_id}:{question['key']}",
                question.get("responseSeconds", timers[f"speaking.{task}"]),
            )
        )
    return integer(timers[f"writing.{task}"])


def half_up(value: Decimal) -> Decimal:
    return (value * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2


def ratio_band(raw: int, maximum: int) -> Decimal:
    if maximum <= 0 or not 0 <= raw <= maximum:
        raise ValueError("原始分超出范围")
    return half_up(Decimal(1) + Decimal(5) * raw / maximum)


def overall(bands: list[Decimal | None]) -> Decimal | None:
    if len(bands) != 4 or any(value is None for value in bands):
        return None
    return half_up(sum((v for v in bands if v is not None), Decimal(0)) / 4)


def validate_conversion(intervals: list[dict[str, Any]], maximum: int) -> None:
    if not intervals:
        raise ValueError("缺少换算区间")
    for row in intervals:
        if set(row) != {"minRaw", "maxRaw", "band"}:
            raise ValueError("换算区间字段无效")
        low, high = integer(row["minRaw"], minimum=0), integer(row["maxRaw"], minimum=0)
        value = row["band"]
        if type(value) not in {float, int, Decimal}:
            raise ValueError("等级分必须为数字")
        band = Decimal(str(value))
        if low > high or high > maximum or not band.is_finite() or not 1 <= band <= 6:
            raise ValueError("换算区间超出范围")
        if band * 2 != (band * 2).to_integral_value():
            raise ValueError("等级分必须为半分档")
    for raw in range(maximum + 1):
        if sum(row["minRaw"] <= raw <= row["maxRaw"] for row in intervals) != 1:
            raise ValueError("换算表必须完整覆盖且无重叠")


def objective(interaction: str, answer: dict[str, Any] | None, solution: dict[str, Any]) -> int:
    if not answer or answer.get("kind") != interaction:
        return 0
    if interaction == "choice":
        return int(bool(answer.get("optionId")) and answer["optionId"] == solution.get("optionId"))
    if interaction == "missing_letters":
        value = str(answer.get("value", "")).strip().lower()
        return int(
            bool(value) and value in [v.lower() for v in solution.get("acceptedSuffixes", [])]
        )
    if interaction == "token_order":
        values = answer.get("tokenIds", [])
        return int(bool(values) and values in solution.get("tokenOrders", []))
    raise ValueError("主观题不能客观判分")


def default_scoring(supplied: dict[str, Any]) -> dict[str, Any]:
    default = deepcopy(DEFAULTS["scoringJson"])
    default["readingMode"] = "ratio"
    metadata = {"calibrationNote": str, "calibrationStatus": str, "generation": dict}
    if set(supplied) - (set(default) | set(metadata)):
        raise ValueError("未知估分配置字段")
    for key, expected in metadata.items():
        if supplied.get(key) is not None and not isinstance(supplied[key], expected):
            raise ValueError(f"{key}类型无效")
    result = merge_missing(default, supplied)
    if result["readingMode"] not in {"ratio", "custom"}:
        raise ValueError("阅读估分模式无效")
    for route, intervals in result["conversions"].items():
        if not intervals:
            if route not in default["conversions"]:
                raise ValueError("未知换算路径")
            result["conversions"][route] = default["conversions"][route]
    return result


def validate_form(
    modules: dict[str, list[str]],
    items: dict[str, dict[str, Any]],
    rules: dict[str, Any],
    scoring: dict[str, Any],
) -> None:
    if set(modules) != set(STAGES):
        raise ValueError("需要完整的八个模块")
    excluded = unscored_refs(rules)
    if excluded:
        router_refs = modules["listening.router"]
        if len(excluded) != 12:
            raise ValueError("听力首阶段必须恰好12道非计分题")
        for _, identity, key in excluded:
            if identity not in router_refs or identity not in items:
                raise ValueError("非计分题引用不属于听力首阶段")
            keys = {q["key"] for q in items[identity]["content"]["subItems"]}
            if key not in keys:
                raise ValueError("非计分题引用不存在的小题")
            selected = {k for _, i, k in excluded if i == identity}
            if selected != keys:
                raise ValueError("非计分题必须完整排除共享题组")
        presented = sum(len(items[i]["content"]["subItems"]) for i in router_refs if i in items)
        if presented != 32:
            raise ValueError("含非计分题的听力首阶段必须呈现32题")
    totals: dict[str, int] = {}
    for stage, refs in modules.items():
        if not refs or len(set(refs)) != len(refs):
            raise ValueError(f"阶段为空或重复题组：{stage}")
        counts: dict[str, int] = {}
        order: list[str] = []
        for identity in refs:
            item = items.get(identity)
            if item is None or item.get("status") != "available":
                raise ValueError(f"题组不可用：{identity}")
            if not stage.startswith(item["section"] + "."):
                raise ValueError(f"阶段科目不匹配：{stage}")
            task = item["taskType"]
            if stage == "listening.router" and task not in QUOTAS[stage]:
                raise ValueError("听力首阶段题型无效")
            scored_count = sum(
                (stage, identity, q["key"]) not in excluded for q in item["content"]["subItems"]
            )
            if scored_count:
                counts[task] = counts.get(task, 0) + scored_count
            order.append(task)
        totals[stage] = sum(counts.values())
        if stage.startswith("reading."):
            reading_seconds(rules, stage, counts)
        elif counts != QUOTAS[stage]:
            raise ValueError(f"题型配额不符：{stage}，需要{QUOTAS[stage]}")
        if stage in {"writing.linear", "speaking.linear"}:
            expected = list(QUOTAS[stage])
            if [expected.index(task) for task in order] != sorted(expected.index(t) for t in order):
                raise ValueError("写作和口语任务顺序无效")
    for section in ("reading", "listening"):
        routing_threshold(rules, section, totals[f"{section}.router"])
        for route in ("lower", "upper"):
            refs = modules[f"{section}.router"] + modules[f"{section}.{route}"]
            if len(refs) != len(set(refs)):
                raise ValueError("同一实际作答路径不能重复题组")
            if section != "reading" or scoring.get("readingMode") == "custom":
                validate_conversion(
                    scoring["conversions"][f"{section}.{route}"],
                    totals[f"{section}.router"] + totals[f"{section}.{route}"],
                )
    validate_conversion(scoring["conversions"]["writing"], 20)
    validate_conversion(scoring["conversions"]["speaking"], 55)
