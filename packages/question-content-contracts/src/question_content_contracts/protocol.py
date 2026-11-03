"""Strict create envelopes; domain schemas are imported from the canonical models."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, create_model, field_validator

from .ielts.papers import PaperContent, PaperInput
from .ielts.schemas import Difficulty, QuestionContent, Skill, StrictModel
from .toefl.schemas import (
    AnswerKey,
    Content,
    Explanation,
    ItemInput,
    Material,
    Solution,
    Stimulus,
    SubItem,
)


class IeltsQuestion(StrictModel):
    skill: Skill
    title: Annotated[str, Field(min_length=1, max_length=500)]
    difficulty: Difficulty
    tags: dict[str, str]
    source: Annotated[str, Field(min_length=1, max_length=1000)]
    content: QuestionContent


# Required import fields reuse the editor models' complete nested definitions.
# create_model is the Pydantic API for changing defaults without mutating the base model.
IeltsPaper = create_model("IeltsPaper", __base__=PaperInput, content=(PaperContent, ...))
ToeflImportContent = create_model(
    "ToeflImportContent",
    __base__=Content,
    version=(Literal[1], ...),
    instruction=(Annotated[str, Field(min_length=1)], ...),
    materials=(list[Material], ...),
    stimulus=(Stimulus, ...),
    subItems=(list[SubItem], ...),
)
ToeflImportSolution = create_model(
    "ToeflImportSolution",
    __base__=Solution,
    answersByKey=(dict[str, AnswerKey], ...),
    explanationsByKey=(dict[str, Explanation], ...),
)
ToeflItem = create_model(
    "ToeflItem",
    __base__=ItemInput,
    content=(ToeflImportContent, ...),
    solution=(ToeflImportSolution, ...),
)


class ToeflForm(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    modules: dict[str, list[str]]
    rules: dict[str, Any]
    scoring: dict[str, Any]

    @field_validator("modules")
    @classmethod
    def canonical_module_references(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for references in value.values():
            for reference in references:
                identity = UUID(reference)
                if not identity.int or str(identity) != reference:
                    raise ValueError("模块引用必须是规范非零UUID")
        return value


class Source(StrictModel):
    object_id: str
    namespace: Annotated[str, Field(min_length=1, max_length=200)]
    source_id: Annotated[str, Field(min_length=1, max_length=500)]
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    locator: str | None = None


class ImportObject(StrictModel):
    id: str
    kind: Literal["ielts_question", "ielts_paper", "toefl_item", "toefl_form"]
    data: dict[str, Any]

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        parsed = UUID(value)
        if str(parsed) != value or parsed.version != 7:
            raise ValueError("新建对象必须使用规范 UUIDv7")
        return value


class Submission(StrictModel):
    exam: Literal["ielts", "toefl"]
    contract_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    objects: Annotated[list[ImportObject], Field(min_length=1, max_length=1000)]
    source_manifest: list[Source] = Field(default_factory=list)


MODELS = {
    "ielts_question": IeltsQuestion,
    "ielts_paper": IeltsPaper,
    "toefl_item": ToeflItem,
    "toefl_form": ToeflForm,
}
