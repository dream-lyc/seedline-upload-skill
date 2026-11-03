from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Section = Literal["reading", "listening", "writing", "speaking"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=100)]
Interaction = Literal["choice", "missing_letters", "token_order", "text", "audio"]
TASKS: dict[str, tuple[str, str]] = {
    "complete_words": ("reading", "missing_letters"),
    "read_daily_life": ("reading", "choice"),
    "read_academic_passage": ("reading", "choice"),
    "listen_choose_response": ("listening", "choice"),
    "listen_conversation": ("listening", "choice"),
    "listen_announcement": ("listening", "choice"),
    "listen_academic_talk": ("listening", "choice"),
    "build_sentence": ("writing", "token_order"),
    "write_email": ("writing", "text"),
    "academic_discussion": ("writing", "text"),
    "listen_repeat": ("speaking", "audio"),
    "take_interview": ("speaking", "audio"),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def no_explicit_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            values = cast(dict[str, Any], data)
            if any(value is None for value in values.values()):
                raise ValueError("不适用字段请省略，不接受null")
            return values
        return data


class Sentence(StrictModel):
    id: Identifier
    text: str = ""
    translation: str | None = None
    speaker: str | None = None
    start_ms: Annotated[int, Field(ge=0)] | None = None
    end_ms: Annotated[int, Field(gt=0)] | None = None


class Paragraph(StrictModel):
    id: Identifier
    sentences: list[Sentence] = Field(default_factory=lambda: list[Sentence]())


class Material(StrictModel):
    id: Identifier
    kind: Literal["text", "audio", "image"]
    file: str | None = None
    reference_name: (
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\?#\x00-\x1f]+$")] | None
    ) = None
    paragraphs: list[Paragraph] = Field(default_factory=lambda: list[Paragraph]())

    @field_validator("file")
    @classmethod
    def media_uuid(cls, value: str | None) -> str | None:
        if value is not None:
            identity = UUID(value)
            if not identity.int or str(identity) != value:
                raise ValueError("媒体必须为规范的非零UUID")
        return value


class Segment(StrictModel):
    kind: Literal["text", "blank"]
    text: str | None = None
    subItemKey: Identifier | None = None
    visiblePrefix: str | None = None
    missingLength: Annotated[int, Field(gt=0)] | None = None


class Stimulus(StrictModel):
    kind: (
        Literal[
            "plain_text",
            "email",
            "notice",
            "course",
            "social_post",
            "image",
            "segmented_text",
        ]
        | None
    ) = None
    material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    displayUrl: str | None = None
    segments: list[Segment] = Field(default_factory=lambda: list[Segment]())


class Option(StrictModel):
    id: Identifier
    text: str


class Highlight(StrictModel):
    material_id: Identifier
    start: Annotated[int, Field(ge=0)]
    text: str


class DiscussionPost(StrictModel):
    id: Identifier
    name: str
    material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    image_material_id: Identifier | None = None


class EmailMeta(StrictModel):
    to: str
    subject: str


class Writing(StrictModel):
    version: Literal[1] = 1
    left_material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    emailMeta: EmailMeta | None = None
    professorName: str | None = None
    professor_material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    professor_image_material_id: Identifier | None = None
    discussionPosts: list[DiscussionPost] = Field(default_factory=lambda: list[DiscussionPost]())


class SubItem(StrictModel):
    key: Identifier
    interaction: Interaction
    prompt: str = ""
    material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    # Omitted inherits shared image; empty string explicitly means no image.
    image_material_id: str | None = None
    responseSeconds: Annotated[int, Field(gt=0)] | None = None
    options: list[Option] = Field(default_factory=lambda: list[Option]())
    tokens: list[Option] = Field(default_factory=lambda: list[Option]())
    fixedPrefix: str | None = None
    fixedSuffix: str | None = None
    passageHighlights: list[Highlight] = Field(default_factory=lambda: list[Highlight]())
    writing: Writing | None = None


class Content(StrictModel):
    version: Literal[1] = 1
    instruction: str = ""
    materials: list[Material] = Field(default_factory=lambda: list[Material]())
    stimulus: Stimulus = Field(default_factory=Stimulus)
    subItems: list[SubItem] = Field(default_factory=lambda: list[SubItem]())


class Evidence(StrictModel):
    material_id: Identifier
    sentence_id: Identifier


class Paraphrase(StrictModel):
    question: str
    original: str


class Explanation(StrictModel):
    analysis: str
    evidence: list[Evidence]
    distractors: str
    paraphrases: list[Paraphrase]


class AnswerKey(StrictModel):
    reference_material_ids: list[Identifier] = Field(default_factory=lambda: list[Identifier]())
    optionId: str | None = None
    acceptedSuffixes: list[str] | None = None
    tokenOrders: list[list[str]] | None = None
    referenceAnswer: str | None = None


class Solution(StrictModel):
    answersByKey: dict[str, AnswerKey] = Field(default_factory=dict)
    explanationsByKey: dict[str, Explanation] = Field(default_factory=dict)


class ItemInput(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    section: Section
    taskType: str
    difficulty: Annotated[int, Field(ge=1, le=5)]
    content: Content
    solution: Solution = Field(default_factory=Solution)
