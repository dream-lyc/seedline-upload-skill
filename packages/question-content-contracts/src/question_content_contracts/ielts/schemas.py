from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, cast, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Skill = Literal["listening", "speaking", "reading", "writing"]
QuestionType = Literal[
    "single_choice",
    "multiple_choice",
    "true_false_not_given",
    "yes_no_not_given",
    "matching",
    "matching_information",
    "matching_headings",
    "matching_features",
    "matching_endings",
    "form_completion",
    "note_completion",
    "table_completion",
    "flow_chart_completion",
    "summary_completion",
    "sentence_completion",
    "short_answer",
    "writing_task1",
    "writing_task2",
    "speaking_part1",
    "speaking_part2",
    "speaking_part3",
]
QUESTION_TYPES: tuple[str, ...] = get_args(QuestionType)
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=100)]
PositiveInt = Annotated[int, Field(gt=0)]
NonnegativeInt = Annotated[int, Field(ge=0)]
Difficulty = Annotated[int, Field(ge=1, le=5)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def explicit_null_is_not_an_omitted_field(cls, value: Any) -> Any:
        if isinstance(value, dict):
            value = cast(dict[str, Any], value)
            for key, item in value.items():
                if item is None and key not in cls.nullable_fields:
                    raise ValueError(f"{key}: optional content must be omitted, not null")
        return value


class Sentence(StrictModel):
    id: Identifier
    text: str | None = None
    translation: str | None = None
    speaker: str | None = None
    start_ms: NonnegativeInt | None = None
    end_ms: PositiveInt | None = None


class Paragraph(StrictModel):
    id: Identifier
    sentences: list[Sentence] = Field(default_factory=lambda: list[Sentence]())


class MediaReference(StrictModel):
    file: str | None = None

    @field_validator("file")
    @classmethod
    def valid_media_uuid(cls, value: str | None) -> str | None:
        if value is not None:
            identity = UUID(value)
            if identity.int == 0 or str(identity) != value:
                raise ValueError("file must be a canonical nonzero media UUID")
        return value


class Material(MediaReference):
    id: Identifier
    kind: Literal["text", "audio", "image"]
    paragraphs: list[Paragraph] | None = None


class Rules(StrictModel):
    max_words: NonnegativeInt | None = None
    max_numbers: NonnegativeInt | None = None
    combination: Literal["words_only", "number_only", "and_or", "or"] | None = None
    exact_words: PositiveInt | None = None
    select_count: PositiveInt | None = None
    reuse_options: bool | None = None
    min_words: PositiveInt | None = None
    recommended_seconds: PositiveInt | None = None
    prepare_seconds: NonnegativeInt | None = None
    min_seconds: PositiveInt | None = None
    max_seconds: PositiveInt | None = None


class Layout(StrictModel):
    text: str


class Evidence(StrictModel):
    material_id: Identifier
    sentence_id: Identifier


class ParagraphTarget(StrictModel):
    material_id: Identifier
    paragraph_id: Identifier


class Paraphrase(StrictModel):
    question: str
    original: str


class Explanation(StrictModel):
    analysis: str
    evidence: list[Evidence]
    distractors: str
    paraphrases: list[Paraphrase]


class Sample(MediaReference):
    paragraphs: list[Paragraph] = Field(default_factory=lambda: list[Paragraph]())
    comments: dict[str, str] = Field(default_factory=dict)


class Answer(StrictModel):
    values: list[list[str]] | None = None
    ordered: bool | None = None
    samples: list[Sample] | None = None


class Item(StrictModel):
    id: Identifier
    numbers: list[PositiveInt] | None = None
    prompt: str | None = None
    translation: str | None = None
    difficulty: Difficulty | None = None
    tags: list[str] | None = None
    is_example: bool | None = None
    paragraph: ParagraphTarget | None = None
    answer: Answer | None = None
    explanation: Explanation | None = None


class Group(StrictModel):
    type: QuestionType
    instruction: str | None = None
    material_ids: list[Identifier] = Field(default_factory=list)
    options: dict[str, str] | None = None
    rules: Rules | None = None
    layout: Layout | None = None
    items: list[Item] = Field(default_factory=lambda: list[Item]())


class QuestionContent(StrictModel):
    materials: list[Material]
    groups: list[Group]


class QuestionPackage(StrictModel):
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"source", "difficulty"})
    id: str | UUID | None = None
    skill: Skill
    title: Annotated[str, Field(min_length=1, max_length=500)]
    difficulty: Difficulty | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    source: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    status: Literal["draft", "published"] = "draft"
    content: QuestionContent
    created_at: str | datetime | None = None
    updated_at: str | datetime | None = None
    deleted_id: str | UUID | None = None


class QuestionInput(StrictModel):
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"source", "difficulty"})
    skill: Skill
    title: Annotated[str, Field(min_length=1, max_length=500)]
    difficulty: Difficulty | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    source: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    content: QuestionContent = Field(
        default_factory=lambda: QuestionContent(materials=[], groups=[])
    )
