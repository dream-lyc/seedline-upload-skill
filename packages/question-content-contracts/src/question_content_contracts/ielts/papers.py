from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, ValidationError, field_validator

from .schemas import PositiveInt, Skill, StrictModel
from .validation import SUBJECTIVE_TYPES

ZERO_UUID = UUID(int=0)


class PaperPart(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=200)]
    question_id: str
    first_number: PositiveInt | None = None

    @field_validator("question_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        identity = UUID(value)
        if not identity.int or str(identity) != value:
            raise ValueError("question_id must be a canonical nonzero UUID")
        return value


class PaperContent(StrictModel):
    book: Annotated[str, Field(pattern=r"^C[1-9]\d*$")] | None = None
    test: PositiveInt | None = None
    parts: list[PaperPart]


class PaperInput(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    skill: Skill
    source: Annotated[str, Field(min_length=1, max_length=1000)]
    content: PaperContent = Field(default_factory=lambda: PaperContent(parts=[]))


def parse_paper(data: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return PaperInput.model_validate(dict(data)).model_dump(mode="json", exclude_none=True), []
    except ValidationError as error:
        return {}, [
            {
                "path": ".".join(str(part) for part in item["loc"]),
                "code": item["type"],
                "message": item["msg"],
            }
            for item in error.errors(include_url=False, include_input=False)
        ]


def validate_paper(
    data: Mapping[str, Any],
    packages: dict[UUID, dict[str, Any]],
    *,
    publish: bool = False,
) -> list[dict[str, Any]]:
    value, errors = parse_paper(data)
    if errors:
        return errors

    def error(path: str, code: str, message: str) -> None:
        errors.append({"path": path, "code": code, "message": message})

    for field in ("title", "source"):
        if not value[field].strip():
            error(field, "paper_text_required", "标题与来源不能仅包含空白")
    content = value["content"]
    if ("book" in content) != ("test" in content):
        error("content", "paper_catalog_pair", "Cambridge book 和 test 必须成套填写")
    parts = content["parts"]
    objective = value["skill"] in {"listening", "reading"}
    seen: set[UUID] = set()
    assigned: set[int] = set()
    task_types: Counter[str] = Counter()
    expected_number = 1
    total = 0
    for index, part in enumerate(parts):
        path = f"content.parts.{index}"
        question_id = UUID(part["question_id"])
        if not part["label"].strip():
            error(f"{path}.label", "paper_label_required", "分部标题不能为空白")
        if question_id in seen:
            error(
                f"{path}.question_id",
                "paper_duplicate_question",
                "同一试卷不能重复引用题包",
            )
        seen.add(question_id)
        package = packages.get(question_id)
        if package is None or str(package.get("deleted_id", ZERO_UUID)) != str(ZERO_UUID):
            error(f"{path}.question_id", "paper_question_missing", "题包不存在或已删除")
            continue
        if content.get("book") and package.get("tags", {}).get("purpose") == "recall":
            error(
                f"{path}.question_id",
                "paper_purpose_conflict",
                "剑桥目录不能引用机经题包",
            )
        if package["skill"] != value["skill"]:
            error(
                f"{path}.question_id",
                "paper_skill_mismatch",
                "同一试卷必须使用同科技能题包",
            )
        if publish and package["status"] != "published":
            error(
                f"{path}.question_id",
                "paper_question_unpublished",
                "发布试卷只能引用已发布题包",
            )
        count = 0
        for group in package["content"]["groups"]:
            for item in group["items"]:
                if item.get("is_example"):
                    continue
                task_types[group["type"]] += 1
                if objective and group["type"] in SUBJECTIVE_TYPES:
                    error(
                        f"{path}.question_id",
                        "paper_mixed_scoring",
                        "客观卷不能包含主观任务",
                    )
                elif not objective and group["type"] not in SUBJECTIVE_TYPES:
                    error(
                        f"{path}.question_id",
                        "paper_mixed_scoring",
                        "主观卷不能包含客观小题",
                    )
                count += len(item.get("numbers", []))
        if objective:
            first = part.get("first_number")
            if publish and (not count or first != expected_number):
                error(
                    f"{path}.first_number",
                    "paper_numbering",
                    "计分位必须从1开始按分部连续编号",
                )
            if first is not None:
                numbers = set(range(first, first + count))
                if numbers.intersection(assigned):
                    error(
                        f"{path}.first_number",
                        "paper_numbering",
                        "计分位不能与另一分部重叠",
                    )
                assigned.update(numbers)
            expected_number += count
            total += count
        elif "first_number" in part:
            error(
                f"{path}.first_number",
                "paper_subjective_numbering",
                "主观题分部省略 first_number",
            )
    if publish:
        if value["skill"] in {"listening", "reading"}:
            expected_parts = 4 if value["skill"] == "listening" else 3
            if len(parts) != expected_parts:
                error(
                    "content.parts",
                    "paper_part_count",
                    f"完整单科卷应有 {expected_parts} 个分部",
                )
            if total != 40:
                error(
                    "content.parts",
                    "paper_score_count",
                    "完整听力或阅读卷必须含40个非例题计分位",
                )
        elif value["skill"] == "writing":
            if task_types != Counter({"writing_task1": 1, "writing_task2": 1}):
                error(
                    "content.parts",
                    "paper_writing_tasks",
                    "完整写作卷必须恰含Task 1和Task 2",
                )
        elif value["skill"] == "speaking":
            if not {"speaking_part1", "speaking_part2", "speaking_part3"} <= task_types.keys():
                error(
                    "content.parts",
                    "paper_speaking_parts",
                    "完整口语卷必须覆盖Part 1、2、3",
                )
            if "life_skills" in task_types:
                error(
                    "content.parts",
                    "paper_life_skills",
                    "Life Skills不属于普通四科模考会话",
                )
    return errors
