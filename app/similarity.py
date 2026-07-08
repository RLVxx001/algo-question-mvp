from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.models import GeneratedProblem


@dataclass
class SimilarProblemCandidate:
    problem_id: str
    title: str
    topic: str
    difficulty: str
    source: str
    score: float
    risk: str
    matched_fields: list[str]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SimilarityReport:
    problem_id: str
    threshold: float
    has_risk: bool
    candidates: list[SimilarProblemCandidate]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return data


def find_similar_problems(
    problem: GeneratedProblem,
    candidates: list[GeneratedProblem],
    threshold: float = 0.35,
    limit: int = 5,
) -> SimilarityReport:
    matches = []
    for candidate in candidates:
        if candidate.id == problem.id:
            continue
        score, fields = _similarity_score(problem, candidate)
        if score >= threshold:
            matches.append(
                SimilarProblemCandidate(
                    problem_id=candidate.id,
                    title=candidate.title,
                    topic=candidate.topic,
                    difficulty=candidate.difficulty,
                    source=candidate.source,
                    score=round(score, 3),
                    risk=_risk_label(score),
                    matched_fields=fields,
                    reason=_reason(fields, score),
                )
            )
    matches.sort(key=lambda item: (-item.score, item.title, item.problem_id))
    matches = matches[: max(0, limit)]
    return SimilarityReport(
        problem_id=problem.id,
        threshold=threshold,
        has_risk=bool(matches),
        candidates=matches,
    )


def _similarity_score(left: GeneratedProblem, right: GeneratedProblem) -> tuple[float, list[str]]:
    field_scores = {
        "title": _jaccard(_tokens(left.title), _tokens(right.title)),
        "topic": 1.0 if left.topic.strip().lower() == right.topic.strip().lower() else 0.0,
        "tags": _jaccard(set(_normalize_items(left.tags)), set(_normalize_items(right.tags))),
        "statement": _jaccard(_tokens(left.statement), _tokens(right.statement)),
    }
    score = (
        field_scores["title"] * 0.35
        + field_scores["topic"] * 0.2
        + field_scores["tags"] * 0.2
        + field_scores["statement"] * 0.25
    )
    matched_fields = [field for field, field_score in field_scores.items() if field_score >= 0.5]
    return score, matched_fields


def _tokens(value: str) -> set[str]:
    normalized = value.lower()
    ascii_tokens = re.findall(r"[a-z0-9_]+", normalized)
    cjk_tokens = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    return {token for token in ascii_tokens + cjk_tokens if len(token) > 0}


def _normalize_items(items: list[str]) -> list[str]:
    return [item.strip().lower() for item in items if item.strip()]


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _risk_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _reason(fields: list[str], score: float) -> str:
    if not fields:
        return f"overall similarity score {score:.2f}"
    return f"matched {', '.join(fields)} with score {score:.2f}"
