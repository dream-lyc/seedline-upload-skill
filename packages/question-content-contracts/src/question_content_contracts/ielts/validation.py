from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError

from question_content_contracts.ielts.schemas import QuestionPackage
from question_content_contracts.ielts.taxonomy import TAG_CODES, tag_errors

COMMON_TYPES = frozenset(
    {
        "single_choice",
        "multiple_choice",
        "matching",
        "note_completion",
        "table_completion",
        "flow_chart_completion",
        "summary_completion",
        "sentence_completion",
        "short_answer",
    }
)
READING_TYPES = COMMON_TYPES | {
    "true_false_not_given",
    "yes_no_not_given",
    "matching_information",
    "matching_headings",
    "matching_features",
    "matching_endings",
}
SKILL_TYPES = {
    "listening": COMMON_TYPES | {"form_completion"},
    "reading": READING_TYPES,
    "writing": {"writing_task1", "writing_task2"},
    "speaking": {"speaking_part1", "speaking_part2", "speaking_part3"},
}
SUBJECTIVE_TYPES = frozenset(
    {
        "writing_task1",
        "writing_task2",
        "speaking_part1",
        "speaking_part2",
        "speaking_part3",
    }
)
MATCHING_TYPES = frozenset(
    {
        "matching",
        "matching_information",
        "matching_headings",
        "matching_features",
        "matching_endings",
    }
)
TEXT_TYPES = frozenset(
    {
        "form_completion",
        "note_completion",
        "table_completion",
        "flow_chart_completion",
        "summary_completion",
        "sentence_completion",
        "short_answer",
    }
)
ROMAN = re.compile(r"(?=.)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_-]+):([1-9]\d*)\}\}")
UNSAFE = re.compile(
    r"<[A-Za-z!/][^>]*>|!\[[^]]*\]\([^)]*\)|\[[^]]*\]\(\s*(?:https?|data|javascript):",
    re.I,
)
PLACEHOLDERS = {
    "待补充",
    "待完善",
    "待翻译",
    "暂无",
    "todo",
    "tbd",
    "placeholder",
    "...",
    "…",
}
CHINESE = re.compile(r"[\u3400-\u9fff]")
NUMBER = re.compile(r"[+\-]?\d+(?:[.,:/-]\d+)*(?:%|st|nd|rd|th)?$", re.I)
TRANSCRIPT_INSTRUCTION = re.compile(
    r"^(?:you (?:will|are going to) hear|listen (?:to|carefully)|"
    r"questions?\s+\d|part\s+[1-4]\b|write (?:one|no more)|"
    r"choose (?:one|two|three)|audio transcript|transcript(?: unavailable| pending))",
    re.I,
)


def parse_package_json(raw: str | bytes) -> dict[str, Any]:
    """Reject duplicate object keys before PostgreSQL/Pydantic can discard them."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate_json_key: {key}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("question request must be an object")
    return cast(dict[str, Any], value)


def collect_media_references(content: Mapping[str, Any]) -> dict[UUID, str]:
    references: dict[UUID, str] = {}
    for material in content.get("materials", []):
        if material.get("file"):
            references[UUID(material["file"])] = f"ielts-material-{material['kind']}"
    for group in content.get("groups", []):
        for item in group.get("items", []):
            for sample in item.get("answer", {}).get("samples", []):
                if sample.get("file"):
                    references[UUID(sample["file"])] = "ielts-sample-audio"
    return references


def answer_obeys_rules(value: str, rules: Mapping[str, Any]) -> bool:
    tokens = value.strip().split()
    numbers = sum(bool(NUMBER.fullmatch(token)) for token in tokens)
    words = len(tokens) - numbers
    if words > rules.get("max_words", words) or numbers > rules.get("max_numbers", numbers):
        return False
    combination = rules.get("combination")
    if combination == "number_only" and words:
        return False
    if combination == "words_only" and numbers:
        return False
    if combination == "or" and words and numbers:
        return False
    return rules.get("exact_words", words) == words


def ordered_option_codes(options: Mapping[str, str]) -> list[str]:
    codes = list(options)
    if all(re.fullmatch(r"[1-9]\d*", code) for code in codes):
        return sorted(codes, key=int)
    if all(ROMAN.fullmatch(code) for code in codes):
        values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}

        def roman_value(code: str) -> int:
            return sum(
                -values[ch]
                if i + 1 < len(code) and values[ch] < values[code[i + 1]]
                else values[ch]
                for i, ch in enumerate(code)
            )

        return sorted(codes, key=roman_value)
    return sorted(codes, key=lambda code: (len(code), code))


def validate_package(
    data: Mapping[str, Any],
    *,
    publish: bool = False,
    media: Mapping[UUID, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return all structural and semantic errors; never mutate the supplied package."""
    try:
        parsed = QuestionPackage.model_validate(dict(data))
    except ValidationError as error:
        return [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "code": item["type"],
                "message": item["msg"],
            }
            for item in error.errors(include_url=False, include_input=False)
        ]
    value = parsed.model_dump(mode="json", exclude_none=True)
    validator = _PackageValidator(value, publish=publish, media=media)
    validator.run()
    return validator.errors


class _PackageValidator:
    def __init__(
        self,
        data: dict[str, Any],
        *,
        publish: bool,
        media: Mapping[UUID, Mapping[str, Any]] | None,
    ) -> None:
        self.data = data
        self.publish = publish
        self.media = media
        self.errors: list[dict[str, Any]] = []
        self.materials: dict[str, dict[str, Any]] = {}
        self.sentences: dict[str, set[str]] = {}
        self.paragraphs: dict[str, set[str]] = {}
        self.item_ids: set[str] = set()
        self.numbers: set[int] = set()
        self.heading_targets: set[tuple[str, str]] = set()
        self.media_purposes: dict[str, str] = {}

    def error(self, path: str, code: str, message: str) -> None:
        self.errors.append({"path": path, "code": code, "message": message})

    def required(self, value: Any, path: str, *, chinese: bool = False) -> None:
        if not self.publish:
            return
        if not value or (
            isinstance(value, str)
            and (
                not value.strip()
                or value.strip().lower() in PLACEHOLDERS
                or (chinese and not CHINESE.search(value))
            )
        ):
            self.error(
                path,
                "incomplete_content",
                "发布需要完整且经过核验的内容，不能使用占位文本",
            )

    def unique(self, value: Any, seen: set[Any], path: str, code: str = "duplicate_id") -> None:
        if value in seen:
            self.error(path, code, "同一作用域内标识或位置不得重复")
        seen.add(value)

    def text_safety(self, value: Any, path: str) -> None:
        if isinstance(value, str) and UNSAFE.search(value):
            self.error(path, "unsafe_content", "题目仅允许受限 Markdown，媒体必须通过材料引用")
        elif isinstance(value, dict):
            for key, child in cast(dict[str, Any], value).items():
                self.text_safety(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(cast(list[Any], value)):
                self.text_safety(child, f"{path}.{index}")

    def tags(self, tags: list[str], path: str) -> None:
        seen: set[str] = set()
        for index, tag in enumerate(tags):
            if tag not in TAG_CODES:
                self.error(f"{path}.{index}", "unknown_tag", "标签必须使用应用字典中的代码")
            self.unique(tag, seen, f"{path}.{index}", "duplicate_tag")

    def run(self) -> None:
        self.text_safety(self.data, "")
        self.required(self.data.get("difficulty"), "difficulty")
        self.required(self.data.get("source"), "source")
        if not self.data["title"].strip():
            self.error("title", "blank_title", "标题不能为空白")
        if "source" in self.data and not self.data["source"].strip():
            self.error("source", "blank_source", "来源不能为空白")
        self.errors.extend(
            tag_errors(
                self.data["skill"],
                self.data["tags"],
                publish=self.publish,
                groups=self.data["content"]["groups"],
            )
        )
        content = self.data["content"]
        self.required(content["groups"], "content.groups")
        ids: set[str] = set()
        for index, material in enumerate(content["materials"]):
            path = f"content.materials.{index}"
            self.unique(material["id"], ids, f"{path}.id")
            self.materials[material["id"]] = material
            self.material(material, path)
        for index, group in enumerate(content["groups"]):
            self.group(group, f"content.groups.{index}")
        if (
            self.publish
            and self.data["skill"] == "listening"
            and not any(material["kind"] == "audio" for material in self.materials.values())
        ):
            self.error(
                "content.materials",
                "listening_audio_required",
                "听力发布必须包含真实音频材料",
            )

    def paragraphs_check(
        self,
        paragraphs: list[dict[str, Any]],
        path: str,
        *,
        timed: bool = False,
        duration: int | None = None,
    ) -> tuple[set[str], set[str]]:
        paragraph_ids: set[str] = set()
        sentence_ids: set[str] = set()
        self.required(paragraphs, path)
        for p_index, paragraph in enumerate(paragraphs):
            p_path = f"{path}.{p_index}"
            self.unique(paragraph["id"], paragraph_ids, f"{p_path}.id")
            self.required(paragraph["sentences"], f"{p_path}.sentences")
            for s_index, sentence in enumerate(paragraph["sentences"]):
                s_path = f"{p_path}.sentences.{s_index}"
                self.unique(sentence["id"], sentence_ids, f"{s_path}.id")
                self.required(sentence.get("text"), f"{s_path}.text")
                self.required(sentence.get("translation"), f"{s_path}.translation", chinese=True)
                start, end = sentence.get("start_ms"), sentence.get("end_ms")
                if (start is None) != (end is None) or (
                    start is not None
                    and end is not None
                    and (start >= end or (duration is not None and end > duration))
                ):
                    self.error(s_path, "audio_timing", "时间轴必须成对且处于真实音频时长内")
                elif timed and self.publish and start is None:
                    self.error(s_path, "audio_timing", "听力发布必须提供逐句时间轴")
        return paragraph_ids, sentence_ids

    def media_check(self, identity: str, purpose: str, path: str) -> int | None:
        previous = self.media_purposes.get(identity)
        if previous and previous != purpose:
            self.error(path, "media_purpose_conflict", "同一媒体不能绑定不兼容用途")
        self.media_purposes[identity] = purpose
        row = (self.media or {}).get(UUID(identity))
        if row is None:
            if self.publish or self.media is not None:
                self.error(path, "media_reference_invalid", "媒体不存在或未经有效确认")
            return None
        deleted = row.get("deleted_id")
        if (
            row.get("purpose") != purpose
            or row.get("state", "confirmed") != "confirmed"
            or (deleted and str(deleted) != str(UUID(int=0)))
        ):
            self.error(path, "media_reference_invalid", "媒体用途、确认状态或有效性不符")
        duration = row.get("duration_ms")
        if purpose.endswith("audio") and (not isinstance(duration, int) or duration <= 0):
            self.error(path, "media_duration_required", "音频必须有经过核验的时长")
            return None
        return duration

    def material(self, material: dict[str, Any], path: str) -> None:
        kind = material["kind"]
        duration = None
        if kind in {"image", "audio"}:
            self.required(material.get("file"), f"{path}.file")
            if material.get("file"):
                duration = self.media_check(
                    material["file"], f"ielts-material-{kind}", f"{path}.file"
                )
        elif "file" in material:
            self.error(f"{path}.file", "unexpected_file", "文字材料不能附带媒体 file")
        paragraphs = material.get("paragraphs", [])
        if kind == "audio" and self.publish and paragraphs:
            text_units = [
                sentence.get("text", "").strip()
                for paragraph in paragraphs
                for sentence in paragraph.get("sentences", [])
            ]
            if text_units and all(TRANSCRIPT_INSTRUCTION.match(unit) for unit in text_units):
                self.error(
                    f"{path}.paragraphs",
                    "placeholder_transcript",
                    "转写不能只包含作答指令或转写占位",
                )
        if kind in {"text", "audio"} or paragraphs:
            pids, sids = self.paragraphs_check(
                paragraphs,
                f"{path}.paragraphs",
                timed=kind == "audio",
                duration=duration,
            )
            self.paragraphs[material["id"]] = pids
            self.sentences[material["id"]] = sids

    def group(self, group: dict[str, Any], path: str) -> None:
        kind = group["type"]
        if kind not in SKILL_TYPES[self.data["skill"]]:
            self.error(f"{path}.type", "skill_type_mismatch", "题型与题包技能不匹配")
        self.required(group.get("instruction"), f"{path}.instruction")
        self.required(group["items"], f"{path}.items")
        refs: set[str] = set()
        for index, reference in enumerate(group["material_ids"]):
            self.unique(reference, refs, f"{path}.material_ids.{index}", "duplicate_reference")
            if reference not in self.materials:
                self.error(f"{path}.material_ids.{index}", "missing_material", "引用材料不存在")
        options = group.get("options", {})
        if "options" in group:
            codes = list(options)
            if not codes or not (
                all(re.fullmatch(r"[A-Z]+", c) for c in codes)
                or all(re.fullmatch(r"[1-9]\d*", c) for c in codes)
                or all(ROMAN.fullmatch(c) for c in codes)
            ):
                self.error(
                    f"{path}.options",
                    "invalid_option_codes",
                    "组内选项代码必须使用一种合法格式",
                )
            for code, label in options.items():
                if not label.strip():
                    self.error(f"{path}.options.{code}", "empty_option", "选项内容不能为空")
        if kind in {"true_false_not_given", "yes_no_not_given"} and "options" in group:
            self.error(f"{path}.options", "unexpected_options", "判断题不重复保存固定三值选项")
        if kind in MATCHING_TYPES | {"single_choice", "multiple_choice"}:
            self.required(options, f"{path}.options")
        rules = group.get("rules", {})
        allowed_rules = {"recommended_seconds"}
        if kind in TEXT_TYPES and not options:
            allowed_rules |= {"max_words", "max_numbers", "combination", "exact_words"}
        if kind == "multiple_choice":
            allowed_rules.add("select_count")
        if kind in MATCHING_TYPES or (kind in TEXT_TYPES and options):
            allowed_rules.add("reuse_options")
        if kind.startswith("writing_task"):
            allowed_rules.add("min_words")
        if kind.startswith("speaking_") or kind == "life_skills":
            allowed_rules |= {"prepare_seconds", "min_seconds", "max_seconds"}
        for key in rules.keys() - allowed_rules:
            self.error(f"{path}.rules.{key}", "unexpected_rule", "此规则不适用于当前题型")
        if self.publish and self.data["skill"] in {"reading", "listening"}:
            expected_kind = "text" if self.data["skill"] == "reading" else "audio"
            image_matching = (
                self.data["skill"] == "reading"
                and kind == "matching"
                and any(
                    self.materials.get(mid, {}).get("kind") == "image"
                    for mid in group["material_ids"]
                )
            )
            if (
                not any(
                    self.materials.get(mid, {}).get("kind") == expected_kind
                    for mid in group["material_ids"]
                )
                and not image_matching
            ):
                self.error(
                    f"{path}.material_ids",
                    f"{self.data['skill']}_material_required",
                    "每个题组必须关联本技能的有效原始材料",
                )
        word_keys = {"max_words", "max_numbers", "combination"}
        if word_keys.intersection(rules) and not word_keys <= rules.keys():
            self.error(f"{path}.rules", "incomplete_word_rules", "文字与数字限制必须成套填写")
        if kind in TEXT_TYPES and not options:
            for key in word_keys:
                if self.publish and key not in rules:
                    self.error(
                        f"{path}.rules.{key}",
                        "word_rule_required",
                        "自由文本题发布必须明确限制",
                    )
        if rules.get("combination") == "number_only" and rules.get("max_words", 0) != 0:
            self.error(
                f"{path}.rules",
                "invalid_word_rules",
                "number_only 的 max_words 必须为零",
            )
        if rules.get("combination") == "words_only" and rules.get("max_numbers", 0) != 0:
            self.error(
                f"{path}.rules",
                "invalid_word_rules",
                "words_only 的 max_numbers 必须为零",
            )
        if rules.get("exact_words", 0) > rules.get("max_words", rules.get("exact_words", 0)):
            self.error(
                f"{path}.rules.exact_words",
                "invalid_word_rules",
                "exact_words 不能超过上限",
            )
        if kind == "multiple_choice":
            self.required(rules.get("select_count"), f"{path}.rules.select_count")
            if rules.get("select_count", 0) > len(options) and options:
                self.error(
                    f"{path}.rules.select_count",
                    "select_count_exceeds_options",
                    "选取数超过选项数",
                )
        if kind in MATCHING_TYPES and self.publish and "reuse_options" not in rules:
            self.error(
                f"{path}.rules.reuse_options",
                "reuse_rule_required",
                "匹配题必须说明选项是否复用",
            )
        if kind == "matching_headings" and rules.get("reuse_options") is not False:
            self.error(f"{path}.rules.reuse_options", "heading_reuse", "标题选项不得复用")
        if kind.startswith("writing_task") and self.publish:
            expected = 150 if kind == "writing_task1" else 250
            if rules.get("min_words") != expected:
                self.error(
                    f"{path}.rules.min_words",
                    "writing_min_words",
                    f"该任务最低词数为 {expected}",
                )
        if rules.get("min_seconds", 0) > rules.get("max_seconds", rules.get("min_seconds", 0)):
            self.error(f"{path}.rules", "invalid_duration", "时长下限不能超过上限")
        occupied: set[str] = set()
        for index, item in enumerate(group["items"]):
            self.item(item, group, f"{path}.items.{index}", occupied)
        if group.get("layout"):
            self.layout(group, path)

    def item(
        self, item: dict[str, Any], group: dict[str, Any], path: str, occupied: set[str]
    ) -> None:
        self.unique(item["id"], self.item_ids, f"{path}.id")
        self.required(item.get("prompt"), f"{path}.prompt")
        self.tags(item.get("tags", []), f"{path}.tags")
        is_example = item.get("is_example", False)
        if "is_example" in item and not is_example:
            self.error(
                f"{path}.is_example",
                "example_flag",
                "普通题省略 is_example，例题只能为 true",
            )
        numbers = item.get("numbers", [])
        if not is_example:
            for index, number in enumerate(numbers):
                self.unique(number, self.numbers, f"{path}.numbers.{index}", "duplicate_number")
        kind = group["type"]
        subjective = kind in SUBJECTIVE_TYPES
        if not subjective and not is_example:
            self.required(numbers, f"{path}.numbers")
        if "paragraph" in item:
            target = item["paragraph"]
            mid, pid = target["material_id"], target["paragraph_id"]
            if (
                kind != "matching_headings"
                or mid not in group["material_ids"]
                or (
                    self.materials.get(mid, {}).get("kind") != "text"
                    or pid not in self.paragraphs.get(mid, set())
                )
            ):
                self.error(
                    f"{path}.paragraph",
                    "invalid_paragraph_target",
                    "标题目标必须为本组真实文字段落",
                )
            if not is_example:
                self.unique(
                    (mid, pid),
                    self.heading_targets,
                    f"{path}.paragraph",
                    "duplicate_paragraph_target",
                )
        elif kind == "matching_headings":
            self.required(None, f"{path}.paragraph")
        answer = item.get("answer", {})
        self.required(answer, f"{path}.answer")
        if answer:
            if ("values" in answer) == ("samples" in answer):
                self.error(
                    f"{path}.answer",
                    "answer_shape",
                    "values 与 samples 必须且只能使用一种",
                )
            elif subjective:
                if "samples" not in answer or "ordered" in answer:
                    self.error(f"{path}.answer", "answer_shape", "主观题只能保存 samples")
                else:
                    self.samples(answer["samples"], kind, f"{path}.answer.samples")
            elif "values" not in answer:
                self.error(f"{path}.answer", "answer_shape", "客观题只能保存 values")
            else:
                self.objective_answer(answer, numbers, is_example, group, path, occupied)
        explanation = item.get("explanation")
        self.required(explanation, f"{path}.explanation")
        if explanation is not None:
            self.explanation(explanation, answer, group, path, subjective)

    def objective_answer(
        self,
        answer: dict[str, Any],
        numbers: list[int],
        is_example: bool,
        group: dict[str, Any],
        path: str,
        occupied: set[str],
    ) -> None:
        values = answer["values"]
        if not values or (not is_example and len(values) != len(numbers)):
            self.error(
                f"{path}.answer.values",
                "answer_arity",
                "答案位置必须与计分题号一一对应",
            )
        options = group.get("options", {})
        kind = group["type"]
        rules = group.get("rules", {})
        if kind in MATCHING_TYPES | {
            "single_choice",
            "true_false_not_given",
            "yes_no_not_given",
        }:
            if len(values) != 1 or (numbers and len(numbers) != 1):
                self.error(
                    f"{path}.answer.values",
                    "single_position_required",
                    "该题型每个小题只能对应一个计分位置",
                )
        allowed = set(options)
        if kind == "true_false_not_given":
            allowed = {"TRUE", "FALSE", "NOT_GIVEN"}
        elif kind == "yes_no_not_given":
            allowed = {"YES", "NO", "NOT_GIVEN"}
        if kind == "multiple_choice":
            if answer.get("ordered") is not False:
                self.error(
                    f"{path}.answer.ordered",
                    "multiple_choice_order",
                    "多选必须使用无序答案",
                )
            if rules.get("select_count") is not None and len(values) != rules["select_count"]:
                self.error(
                    f"{path}.answer.values",
                    "answer_arity",
                    "多选答案数必须等于 select_count",
                )
        seen: set[str] = set()
        for position, variants in enumerate(values):
            vpath = f"{path}.answer.values.{position}"
            if not variants or any(not v.strip() for v in variants):
                self.error(vpath, "empty_answer", "接受答案集合必须非空")
            normalized = {v.strip().casefold() for v in variants}
            if len(normalized) != len(variants):
                self.error(vpath, "duplicate_answer", "同一接受集合不能重复")
            if kind == "multiple_choice" and seen.intersection(normalized):
                self.error(vpath, "overlapping_answers", "多选接受集合不能重叠")
            seen.update(normalized)
            for index, variant in enumerate(variants):
                if (
                    allowed or kind in MATCHING_TYPES | {"single_choice", "multiple_choice"}
                ) and variant not in allowed:
                    self.error(
                        f"{vpath}.{index}",
                        "invalid_answer_code",
                        "答案必须属于题型的合法选项",
                    )
                if kind in TEXT_TYPES and not options and not answer_obeys_rules(variant, rules):
                    self.error(
                        f"{vpath}.{index}",
                        "answer_word_limit",
                        "标准答案违反单词或数字限制",
                    )
            if kind in MATCHING_TYPES and rules.get("reuse_options") is False:
                if occupied.intersection(variants):
                    self.error(
                        vpath,
                        "option_reused",
                        "不可复用匹配的答案不能占用相同选项（含例题）",
                    )
                occupied.update(variants)

    def explanation(
        self,
        explanation: dict[str, Any],
        answer: dict[str, Any],
        group: dict[str, Any],
        path: str,
        subjective: bool,
    ) -> None:
        base = f"{path}.explanation"
        self.required(explanation["analysis"], f"{base}.analysis", chinese=True)
        evidence = explanation["evidence"]
        is_ng = any("NOT_GIVEN" in v for v in answer.get("values", []))
        if is_ng and evidence:
            self.error(f"{base}.evidence", "not_given_evidence", "NOT_GIVEN 必须使用空证据数组")
        relevant_text = any(
            self.materials.get(mid, {}).get("kind") in {"text", "audio"}
            for mid in group["material_ids"]
        )
        if self.publish and not subjective and not is_ng and relevant_text and not evidence:
            self.error(
                f"{base}.evidence",
                "evidence_required",
                "存在原文的客观答案需要真实句子定位",
            )
        seen: set[tuple[str, str]] = set()
        for index, reference in enumerate(evidence):
            mid, sid = reference["material_id"], reference["sentence_id"]
            if (
                mid not in group["material_ids"]
                or sid not in self.sentences.get(mid, set())
                or self.materials.get(mid, {}).get("kind") not in {"text", "audio"}
            ):
                self.error(
                    f"{base}.evidence.{index}",
                    "invalid_evidence",
                    "证据必须指向本组原文中的真实句子",
                )
            self.unique((mid, sid), seen, f"{base}.evidence.{index}", "duplicate_evidence")
        for index, pair in enumerate(explanation["paraphrases"]):
            for field in ("question", "original"):
                if not pair[field].strip():
                    self.error(
                        f"{base}.paraphrases.{index}.{field}",
                        "empty_paraphrase",
                        "同义表达不能为空",
                    )
        if self.publish and group.get("options") and not subjective:
            correct = {v for variants in answer.get("values", []) for v in variants}
            if set(group["options"]) - correct and not explanation["distractors"].strip():
                self.error(
                    f"{base}.distractors",
                    "distractors_required",
                    "有错误选项时需要纯文本干扰说明",
                )

    def samples(self, samples: list[dict[str, Any]], kind: str, path: str) -> None:
        self.required(samples, path)
        expected = (
            {"TA", "CC", "LR", "GRA"}
            if kind == "writing_task1"
            else {"TR", "CC", "LR", "GRA"}
            if kind == "writing_task2"
            else {
                "obtaining_information",
                "conveying_information",
                "speaking_to_communicate",
                "engaging_in_discussion",
            }
            if kind == "life_skills"
            else {"FC", "LR", "GRA", "P"}
        )
        for index, sample in enumerate(samples):
            spath = f"{path}.{index}"
            duration = (
                self.media_check(sample["file"], "ielts-sample-audio", f"{spath}.file")
                if sample.get("file")
                else None
            )
            self.paragraphs_check(sample["paragraphs"], f"{spath}.paragraphs", duration=duration)
            comments = sample["comments"]
            for key in comments.keys() - expected:
                self.error(
                    f"{spath}.comments.{key}",
                    "invalid_comment_dimension",
                    "教学点评维度与题型不符",
                )
            for key in expected:
                self.required(comments.get(key), f"{spath}.comments.{key}", chinese=True)

    def layout(self, group: dict[str, Any], path: str) -> None:
        text = group["layout"]["text"]
        if group["type"] not in TEXT_TYPES:
            self.error(f"{path}.layout", "unexpected_layout", "layout 仅用于共享文字填空正文")
        expected = {
            (item["id"], position)
            for item in group["items"]
            for position in range(1, len(item.get("numbers", [])) + 1)
        }
        actual: set[tuple[str, int]] = set()
        for match in PLACEHOLDER.finditer(text):
            reference = (match[1], int(match[2]))
            if reference not in expected:
                self.error(
                    f"{path}.layout.text",
                    "invalid_placeholder",
                    "占位必须指向当前组存在的输入位置",
                )
            self.unique(reference, actual, f"{path}.layout.text", "duplicate_placeholder")
        if "{{" in PLACEHOLDER.sub("", text) or "}}" in PLACEHOLDER.sub("", text):
            self.error(
                f"{path}.layout.text",
                "invalid_placeholder",
                "占位格式必须为 {{item_id:position}}",
            )
        if self.publish and expected - actual:
            self.error(
                f"{path}.layout.text",
                "missing_placeholder",
                "共享正文必须覆盖全部输入位置",
            )
