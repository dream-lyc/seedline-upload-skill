"""Executable synthetic acceptance examples, never presented as source exam content."""

from typing import Any
from uuid import UUID

from .toefl.policies import QUOTAS, STAGES, default_rules, default_scoring
from .toefl.schemas import TASKS


def uid(n: int) -> str:
    return str(UUID(f"019a0000-0000-7000-8000-{n:012x}"))


AUDIO = uid(90001)
TOEFL_AUDIO = uid(90002)


def example_media() -> dict[str, dict[str, Any]]:
    return {
        AUDIO: {
            "state": "confirmed",
            "purpose": "ielts-material-audio",
            "duration_ms": 10000,
        },
        TOEFL_AUDIO: {
            "state": "confirmed",
            "purpose": "toefl-stimulus-audio",
            "duration_ms": 10000,
        },
    }


def explanation(evidence: bool = True) -> dict[str, Any]:
    return {
        "analysis": "验收样例：原文给出明确依据。",
        "evidence": [{"material_id": "m1", "sentence_id": "s1"}] if evidence else [],
        "distractors": "其他选项与原文不符。",
        "paraphrases": [],
    }


def ielts_question(skill: str, number: int = 1, count: int = 1) -> dict[str, Any]:
    subjective = skill in {"writing", "speaking"}
    material: dict[str, Any] = {
        "id": "m1",
        "kind": "audio" if skill == "listening" else "text",
        "paragraphs": [
            {
                "id": "p1",
                "sentences": [
                    {
                        "id": "s1",
                        "text": "The garden opened in June.",
                        "translation": "花园于六月开放。",
                    }
                ],
            }
        ],
    }
    if skill == "listening":
        material["file"] = AUDIO
        material["paragraphs"][0]["sentences"][0].update(start_ms=0, end_ms=3000)
    groups = []
    kinds = (
        ["writing_task1", "writing_task2"]
        if skill == "writing"
        else ["speaking_part1", "speaking_part2", "speaking_part3"]
        if skill == "speaking"
        else ["single_choice"]
    )
    for kind in kinds:
        group = {
            "type": kind,
            "instruction": "Synthetic acceptance example; answer the question.",
            "material_ids": [] if subjective else ["m1"],
            "items": [],
        }
        if subjective:
            group["rules"] = (
                {"min_words": 150 if kind == "writing_task1" else 250} if skill == "writing" else {}
            )
            dims = (
                ["TA" if kind == "writing_task1" else "TR", "CC", "LR", "GRA"]
                if skill == "writing"
                else ["FC", "LR", "GRA", "P"]
            )
            group["items"] = [
                {
                    "id": kind,
                    "prompt": "Describe a garden.",
                    "answer": {
                        "samples": [
                            {
                                "paragraphs": material["paragraphs"],
                                "comments": {d: "验收教学点评完整。" for d in dims},
                            }
                        ]
                    },
                    "explanation": explanation(False),
                }
            ]
        else:
            group["options"] = {"A": "June", "B": "July"}
            group["items"] = [
                {
                    "id": f"q{i + 1}",
                    "numbers": [i + 1],
                    "prompt": "When did it open?",
                    "answer": {"values": [["A"]]},
                    "explanation": explanation(),
                }
                for i in range(count)
            ]
        groups.append(group)
    return {
        "id": uid(number),
        "kind": "ielts_question",
        "data": {
            "skill": skill,
            "title": f"合成验收 {skill} {number}",
            "difficulty": 3,
            "tags": {"purpose": "recall"},
            "source": "仅用于自动化验收的合成资料",
            "content": {
                "materials": [] if subjective else [material],
                "groups": groups,
            },
        },
    }


def toefl_item(task: str, number: int = 1, count: int = 1) -> dict[str, Any]:
    section, interaction = TASKS[task]
    audio = task.startswith("listen_") or task == "take_interview"
    material: dict[str, Any] = {
        "id": "m1",
        "kind": "audio" if audio else "text",
        "paragraphs": [
            {
                "id": "p1",
                "sentences": [
                    {
                        "id": "s1",
                        "text": "The library closes late.",
                        "translation": "图书馆晚些时候关闭。",
                    }
                ],
            }
        ],
    }
    if audio:
        material["file"] = TOEFL_AUDIO
        material["paragraphs"][0]["sentences"][0].update(start_ms=0, end_ms=3000)
    stimulus: dict[str, Any] = {"material_ids": ["m1"]}
    if task == "read_daily_life":
        stimulus["kind"] = "notice"
    questions = []
    answers = {}
    explanations = {}
    for i in range(count):
        key = f"q{i + 1}"
        q: dict[str, Any] = {
            "key": key,
            "interaction": interaction,
            "prompt": "Answer based on the supplied material.",
        }
        answer = {}
        if interaction == "choice":
            q["options"] = [
                {"id": "A", "text": "Hours"},
                {"id": "B", "text": "Location"},
            ]
            answer = {"optionId": "A"}
        elif interaction == "missing_letters":
            answer = {"acceptedSuffixes": ["es"]}
        elif interaction == "token_order":
            q["tokens"] = [
                {"id": "a", "text": "The library"},
                {"id": "b", "text": "closes late."},
            ]
            answer = {"tokenOrders": [["a", "b"]]}
        else:
            answer = {"referenceAnswer": "The library closes later than usual."}
        questions.append(q)
        answers[key] = answer
        explanations[key] = explanation()
    if task == "complete_words":
        stimulus.update(
            kind="segmented_text",
            segments=[
                {"kind": "text", "text": "The library "},
                {
                    "kind": "blank",
                    "subItemKey": "q1",
                    "visiblePrefix": "clos",
                    "missingLength": 2,
                },
                {"kind": "text", "text": " late."},
            ],
        )
    return {
        "id": uid(number),
        "kind": "toefl_item",
        "data": {
            "title": f"合成验收 {task} {number}",
            "section": section,
            "taskType": task,
            "difficulty": 3,
            "content": {
                "version": 1,
                "instruction": "Synthetic acceptance example.",
                "materials": [material],
                "stimulus": stimulus,
                "subItems": questions,
            },
            "solution": {"answersByKey": answers, "explanationsByKey": explanations},
        },
    }


def examples_for(exam: str) -> dict[str, list[dict[str, Any]]]:
    if exam == "ielts":
        questions = []
        papers = []
        n = 1
        for skill, counts in [
            ("reading", [13, 13, 14]),
            ("listening", [10] * 4),
            ("writing", [1]),
            ("speaking", [1]),
        ]:
            parts = []
            first = 1
            for count in counts:
                q = ielts_question(skill, n, count)
                questions.append(q)
                n += 1
                part = {"label": f"Part {len(parts) + 1}", "question_id": q["id"]}
                if skill in {"reading", "listening"}:
                    part["first_number"] = first
                parts.append(part)
                first += count
            papers.append(
                {
                    "id": uid(100 + len(papers)),
                    "kind": "ielts_paper",
                    "data": {
                        "title": f"完整合成验收 {skill}",
                        "skill": skill,
                        "source": "合成验收",
                        "content": {"parts": parts},
                    },
                }
            )
        return {"ielts_question": questions, "ielts_paper": papers}
    items = []
    modules = {}
    n = 1000
    for stage in STAGES:
        modules[stage] = []
        quotas = QUOTAS.get(stage, {"read_daily_life": 1})
        for task, count in quotas.items():
            q = toefl_item(task, n, count)
            items.append(q)
            modules[stage].append(q["id"])
            n += 1
    covered = {q["data"]["taskType"] for q in items}
    for task in TASKS.keys() - covered:
        items.append(toefl_item(task, n))
        n += 1
    form = {
        "id": uid(2000),
        "kind": "toefl_form",
        "data": {
            "title": "全部候选分支合成验收",
            "modules": modules,
            "rules": default_rules({}),
            "scoring": default_scoring({}),
        },
    }
    return {"toefl_item": items, "toefl_form": [form]}
