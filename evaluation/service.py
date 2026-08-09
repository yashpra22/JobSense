"""Thin service boundary around JobSense's evaluator.

Keeping orchestration behind this interface makes it possible to replace the
LLM provider or run deterministic-only evaluations in tests without changing
the scraper/dashboard layers.
"""
from dataclasses import dataclass

from llm_evaluator import calculate_candidate_skill_score, prefilter_job


@dataclass(frozen=True)
class EvaluationResult:
    eligible: bool
    score: float
    reason: str
    analysis: str


class EvaluationService:
    def evaluate(self, title: str, location: str, jd_text: str) -> EvaluationResult:
        filtered, passes, reason, _ = prefilter_job(title, location, jd_text)
        if filtered:
            return EvaluationResult(False, 0.0, reason, f"Deterministic eligibility: {reason}")
        score, analysis = calculate_candidate_skill_score(title, location, jd_text)
        return EvaluationResult(bool(passes), score, "Passes eligibility", analysis)
