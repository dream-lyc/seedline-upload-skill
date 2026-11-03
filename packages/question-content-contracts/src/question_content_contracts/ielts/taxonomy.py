"""Skill-local package filters and the legacy item-level teaching vocabulary."""

from collections.abc import Mapping, Sequence
from typing import Any

from question_content_contracts.errors import ContractError

TOPIC_TAGS = frozenset(
    {
        "education",
        "environment",
        "community",
        "housing",
        "daily_life",
        "reading_habits",
        "technology",
        "science",
        "history",
        "culture",
        "art",
        "arts",
        "music",
        "sport",
        "sports",
        "health",
        "medicine",
        "work",
        "employment",
        "business",
        "economy",
        "economics",
        "travel",
        "tourism",
        "transport",
        "transportation",
        "nature",
        "animals",
        "wildlife",
        "plants",
        "agriculture",
        "food",
        "nutrition",
        "family",
        "friends",
        "relationships",
        "communication",
        "media",
        "language",
        "languages",
        "psychology",
        "society",
        "geography",
        "architecture",
        "cities",
        "urban_development",
        "energy",
        "water",
        "climate",
        "space",
        "archaeology",
        "leisure",
        "shopping",
        "government",
        "crime",
        "population",
        "childhood",
        "memory",
        "imagination",
        "change",
        "books",
        "clothing",
    }
)
PURPOSE_TAGS = frozenset({"recall", "listening_spelling", "cambridge"})
SKILL_TAGS = frozenset(
    {
        "detail",
        "main_idea",
        "inference",
        "opinion",
        "comparison",
        "cause_effect",
        "number_correction",
        "paraphrase",
        "distractor",
        "vocabulary",
        "spelling",
        "numbers",
        "formal_letter",
        "informal_letter",
        "semi_formal_letter",
        "request",
        "complaint",
        "invitation",
        "apology",
        "suggestion",
        "bar_chart",
        "line_graph",
        "pie_chart",
        "table",
        "map",
        "process",
        "mixed_charts",
        "opinion_essay",
        "discussion",
        "advantages_disadvantages",
        "problem_solution",
        "two_part_question",
        "library",
        "accommodation",
        "university",
        "museum",
        "booking",
        "registration",
        "directions",
        "lecture",
        "conversation",
    }
)
TAG_CODES = TOPIC_TAGS | PURPOSE_TAGS | SKILL_TAGS

# Only package filter keys and labels are fixed. Candidate values live in question rows.
# TAG_CODES above remains the separate optional item-level teaching vocabulary.
FILTERS: dict[str, tuple[tuple[str, str], ...]] = {
    "listening": (("part", "Part"), ("scene", "场景")),
    "reading": (("passage", "Passage"), ("scene", "场景")),
    "writing": (("task", "Task"),),
    "speaking": (("part", "Part"), ("question_status", "新题还是旧题")),
}
SOURCE_TAG_KEYS = {
    "Passage": "passage",
    "Part": "part",
    "场景": "scene",
    "Task": "task",
    "新题还是旧题": "question_status",
}


def filter_options(
    skill: str, values: Mapping[str, Sequence[str]] | None = None
) -> list[dict[str, object]]:
    """Fixed rows; every candidate must be supplied by the actual database query."""
    values = values or {}
    return [
        {
            "key": key,
            "label": label,
            "values": [
                {"code": value, "label": value} for value in sorted(set(values.get(key, ())))
            ],
        }
        for key, label in FILTERS[skill]
    ]


class TagValidationError(ContractError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(422, "ielts_invalid_tags", "请检查科目及标签筛选条件")
        self.errors = errors


def tag_errors(
    skill: str | None,
    tags: Mapping[str, Any],
    *,
    publish: bool = False,
    groups: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    allowed = {key for key, _ in FILTERS.get(skill or "", ())} | {"purpose"}
    for key, value in tags.items():
        if key not in allowed:
            message = "该科目不支持此筛选键；科目专属筛选必须指定skill"
        elif (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > 200
            or value in {"全部", "未知"}
        ):
            message = "分类必须是1–200字符、无首尾空白的单个文本，不能使用占位值"
        elif key == "purpose" and value not in PURPOSE_TAGS:
            message = "题库用途不合法"
        elif (
            key == "purpose" and value == "listening_spelling" and skill not in {None, "listening"}
        ):
            message = "拼写用途只适用于听力"
        else:
            continue
        errors.append({"path": f"tags.{key}", "code": "invalid_tag", "message": message})
    if publish and "purpose" not in tags:
        errors.append(
            {
                "path": "tags.purpose",
                "code": "purpose_required",
                "message": "发布必须明确题库用途",
            }
        )
    types = {group["type"] for group in groups}
    for key, expected in (
        ("task", {"Task 1": {"writing_task1"}, "Task 2": {"writing_task2"}}),
        (
            "part",
            {
                "Part1": {"speaking_part1"},
                "Part2": {"speaking_part2"},
                "Part3": {"speaking_part3"},
                "Part2&3": {"speaking_part2", "speaking_part3"},
            },
        ),
    ):
        if (
            types
            and key in tags
            and (skill == "writing" and key == "task" or skill == "speaking" and key == "part")
        ):
            if isinstance(tags[key], str) and expected.get(tags[key]) != types:
                errors.append(
                    {
                        "path": f"tags.{key}",
                        "code": "tag_task_mismatch",
                        "message": "分类与实际任务不一致",
                    }
                )
    return errors


def require_tag_filters(skill: str | None, tags: Mapping[str, Any]) -> dict[str, str]:
    errors = tag_errors(skill, tags)
    if skill is not None and skill not in FILTERS:
        errors.append(
            {
                "path": "skill",
                "code": "invalid_skill",
                "message": "请选择支持的雅思科目",
            }
        )
    if errors:
        raise TagValidationError(errors)
    return dict(tags)


def parse_tag_filters(raw: str | None, *, skill: str | None) -> dict[str, str]:
    if raw is None:
        return {}
    from question_content_contracts.ielts.validation import parse_package_json

    try:
        parsed = parse_package_json(raw)
    except (ValueError, TypeError) as error:
        raise TagValidationError(
            [
                {
                    "path": "tags",
                    "code": "invalid_tag_json",
                    "message": "tags必须是无重复键的JSON对象",
                }
            ]
        ) from error
    return require_tag_filters(skill, parsed)
