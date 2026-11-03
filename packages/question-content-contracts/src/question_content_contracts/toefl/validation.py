"""One canonical TOEFL content contract for draft, publication and new starts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from question_content_contracts.errors import ContractError
from question_content_contracts.toefl.schemas import TASKS, ItemInput


class ContentError(ContractError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(422, "toefl_content_invalid", "托福内容校验失败")
        self.errors = errors


def material_text(material: dict[str, Any]) -> str:
    return "\n\n".join(
        " ".join(s["text"] for s in p["sentences"]) for p in material.get("paragraphs", [])
    )


def question_material_ids(stimulus: dict[str, Any], question: dict[str, Any]) -> set[str]:
    refs = set(stimulus.get("material_ids", []) + question.get("material_ids", []))
    writing = question.get("writing", {})
    refs.update(writing.get("left_material_ids", []))
    refs.update(writing.get("professor_material_ids", []))
    for field in ("professor_image_material_id",):
        if writing.get(field):
            refs.add(writing[field])
    for post in writing.get("discussionPosts", []):
        refs.update(post.get("material_ids", []))
        if post.get("image_material_id"):
            refs.add(post["image_material_id"])
    if question.get("image_material_id"):
        refs.add(question["image_material_id"])
    return refs


def usable(value: Any, *, chinese: bool = False) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() not in {"待补充", "待完善", "待翻译", "todo", "tbd"}
        and (not chinese or re.search(r"[\u4e00-\u9fff]", value) is not None)
    )


def validate_item(
    payload: Any,
    *,
    publish: bool = False,
    media: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        item = ItemInput.model_validate(payload).model_dump(exclude_none=True)
    except ValidationError as error:
        raise ContentError(
            [{"path": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in error.errors()]
        ) from error
    errors: list[dict[str, str]] = []

    def fail(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    def unique(values: list[str], path: str) -> None:
        if len(values) != len(set(values)):
            fail(path, "标识重复")

    task = item["taskType"]
    if task not in TASKS or TASKS[task][0] != item["section"]:
        fail("taskType", "题型与科目不一致")
    materials = item["content"]["materials"]
    unique([m["id"] for m in materials], "content.materials")
    by_id = {m["id"]: m for m in materials}
    sentences: dict[tuple[str, str], dict[str, Any]] = {}
    for mi, material in enumerate(materials):
        base = f"content.materials.{mi}"
        unique([p["id"] for p in material["paragraphs"]], base + ".paragraphs")
        ids: list[str] = []
        verified = (media or {}).get(material.get("file", ""), {})
        if publish and material["kind"] in {"audio", "image"}:
            purpose = "toefl-stimulus-" + material["kind"]
            if (
                not material.get("file")
                or verified.get("state") != "confirmed"
                or verified.get("purpose") != purpose
            ):
                fail(base + ".file", "缺少用途匹配的已确认媒体")
        if publish and material["kind"] != "image" and not material["paragraphs"]:
            fail(base + ".paragraphs", "材料缺少完整原文或转写")
        for pi, paragraph in enumerate(material["paragraphs"]):
            if publish and not paragraph["sentences"]:
                fail(f"{base}.paragraphs.{pi}.sentences", "段落不能为空")
            for si, sentence in enumerate(paragraph["sentences"]):
                path = f"{base}.paragraphs.{pi}.sentences.{si}"
                ids.append(sentence["id"])
                sentences[material["id"], sentence["id"]] = sentence
                if publish:
                    if not usable(sentence["text"]):
                        fail(path + ".text", "缺少真实原文")
                    if not usable(sentence.get("translation"), chinese=True):
                        fail(path + ".translation", "缺少中文译文")
                start, end = sentence.get("start_ms"), sentence.get("end_ms")
                if (start is None) != (end is None) or (start is not None and end <= start):
                    fail(path, "开始结束时间须成对且递增")
                if material["kind"] != "audio" and start is not None:
                    fail(path, "无音频材料不能设置时间轴")
                if publish and material["kind"] == "audio":
                    duration = verified.get("duration_ms")
                    if start is None or not isinstance(duration, int) or end > duration:
                        fail(path, "缺少有效真实时间轴或超出音频长度")
        unique(ids, base + ".sentences")
    stimulus = item["content"]["stimulus"]
    questions = item["content"]["subItems"]
    unique([q["key"] for q in questions], "content.subItems")
    keys = {q["key"] for q in questions}
    for field in ("answersByKey", "explanationsByKey"):
        for key in item["solution"][field]:
            if key not in keys:
                fail(f"solution.{field}.{key}", "引用不存在的小题")
    if publish and not questions:
        fail("content.subItems", "缺少小题")
    if task == "read_daily_life" and stimulus.get("kind") not in {
        "plain_text",
        "email",
        "notice",
        "course",
        "social_post",
        "image",
    }:
        fail("content.stimulus.kind", "生活阅读须指定六种材料类型之一")
    for ref in stimulus["material_ids"]:
        if ref not in by_id:
            fail("content.stimulus.material_ids", "引用不存在的材料")
    for i, question in enumerate(questions):
        path = f"content.subItems.{i}"
        key = question["key"]
        refs = question_material_ids(stimulus, question)
        if not refs.issubset(by_id):
            fail(path + ".material_ids", "引用不存在的材料")
        if task in TASKS and question["interaction"] != TASKS[task][1]:
            fail(path + ".interaction", "交互与题型不一致")
        for field in ("options", "tokens"):
            unique([option["id"] for option in question[field]], path + "." + field)
        answer = item["solution"]["answersByKey"].get(key, {})
        if not set(answer.get("reference_material_ids", [])).issubset(by_id):
            fail(f"solution.answersByKey.{key}.reference_material_ids", "参考材料不存在")
        interaction = question["interaction"]
        answer_field = {
            "choice": "optionId",
            "missing_letters": "acceptedSuffixes",
            "token_order": "tokenOrders",
            "text": "referenceAnswer",
            "audio": "referenceAnswer",
        }[interaction]
        if set(answer) - {answer_field, "reference_material_ids"}:
            fail(f"solution.answersByKey.{key}", "答案字段与题型不一致")
        if "optionId" in answer and answer["optionId"] not in {
            o["id"] for o in question["options"]
        }:
            fail(f"solution.answersByKey.{key}.optionId", "答案选项不存在")
        for order in answer.get("tokenOrders", []):
            tokens = {o["id"] for o in question["tokens"]}
            if not order or len(order) != len(set(order)) or not set(order).issubset(tokens):
                fail(f"solution.answersByKey.{key}.tokenOrders", "词块顺序引用无效")
        if publish:
            required = {
                "choice": "optionId",
                "missing_letters": "acceptedSuffixes",
                "token_order": "tokenOrders",
                "text": "referenceAnswer",
                "audio": "referenceAnswer",
            }[interaction]
            if not answer.get(required):
                fail(f"solution.answersByKey.{key}.{required}", "缺少答案依据")
            if interaction == "choice" and len(question["options"]) < 2:
                fail(path + ".options", "选择题至少两个选项")
            if task.startswith("listen_") or task == "take_interview":
                if not any(by_id.get(ref, {}).get("kind") == "audio" for ref in refs):
                    fail(path + ".material_ids", "缺少音频材料")
            if task in {"read_daily_life", "read_academic_passage", "complete_words"} and not refs:
                fail(path + ".material_ids", "缺少原文材料")
        for hi, highlight in enumerate(question["passageHighlights"]):
            text = material_text(by_id.get(highlight["material_id"], {}))
            encoded = text.encode("utf-16-le")
            start = highlight["start"] * 2
            expected = highlight["text"].encode("utf-16-le")
            if (
                highlight["material_id"] not in refs
                or not expected
                or encoded[start : start + len(expected)] != expected
            ):
                fail(f"{path}.passageHighlights.{hi}", "高亮与原文不匹配")
        explanation = item["solution"]["explanationsByKey"].get(key)
        epath = f"solution.explanationsByKey.{key}"
        if explanation is None:
            if publish:
                fail(epath, "缺少四项解析")
            continue
        if publish and not usable(explanation["analysis"], chinese=True):
            fail(epath + ".analysis", "缺少非占位中文解析")
        evidence = explanation["evidence"]
        if publish and interaction in {"choice", "missing_letters", "token_order"}:
            if any(by_id.get(ref, {}).get("paragraphs") for ref in refs) and not evidence:
                fail(epath + ".evidence", "有文本依据的客观题需要句子定位")
            if interaction == "choice" and not usable(explanation["distractors"], chinese=True):
                fail(epath + ".distractors", "缺少干扰说明")
        for j, ref in enumerate(evidence):
            identity = (ref["material_id"], ref["sentence_id"])
            if identity not in sentences or identity[0] not in refs:
                fail(f"{epath}.evidence.{j}", "句子不存在或不属于本题关联材料")
        for j, pair in enumerate(explanation["paraphrases"]):
            if not usable(pair["question"]) or not usable(pair["original"]):
                fail(f"{epath}.paraphrases.{j}", "同义替换两侧必须有内容")
    if task == "complete_words":
        blanks = [s.get("subItemKey") for s in stimulus["segments"] if s["kind"] == "blank"]
        if set(blanks) != keys or len(blanks) != len(set(blanks)):
            fail("content.stimulus.segments", "空位必须与小题一一对应")
        if publish:
            restored: list[str] = []
            for index, segment in enumerate(stimulus["segments"]):
                path = f"content.stimulus.segments.{index}"
                if segment["kind"] == "text":
                    if not isinstance(segment.get("text"), str):
                        fail(path + ".text", "缺少空位题面文字")
                    restored.append(segment.get("text", ""))
                    continue
                suffixes = (
                    item["solution"]["answersByKey"]
                    .get(segment.get("subItemKey"), {})
                    .get("acceptedSuffixes", [])
                )
                if (
                    not isinstance(segment.get("visiblePrefix"), str)
                    or not suffixes
                    or any(len(suffix) != segment.get("missingLength") for suffix in suffixes)
                ):
                    fail(path, "可见前缀、缺失长度与答案后缀必须一致")
                restored.append(
                    segment.get("visiblePrefix", "") + (suffixes[0] if suffixes else "")
                )
            original = "\n\n".join(
                material_text(by_id[mid])
                for mid in stimulus["material_ids"]
                if mid in by_id and by_id[mid]["kind"] == "text"
            )

            def normalize(text: str) -> str:
                return re.sub(r"\s+", " ", text).strip().casefold()

            if normalize("".join(restored)) != normalize(original):
                fail("content.stimulus.segments", "空位题面按答案还原后必须与原文一致")
    if errors:
        raise ContentError(errors)
    return item
