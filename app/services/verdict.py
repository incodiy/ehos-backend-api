"""PASS/FAIL verdict + Life-Safety Hazard Flag (PRD-F-02/F-03, task 6c).

Mengkonsumsi SessionScore hasil aggregation (6b):
- PASS bila total_ratio >= threshold (default 80%) DAN tidak ada blocking reason.
- Blocking reasons (safety-first):
    * below_threshold          total skor < 80%
    * life_safety_hazard       ada item is_life_safety gagal (ratio == 0) → tiket CAPA P1 (F-03)
    * has_unscored_items       ada item belum di-score → laporan tidak bisa disertifikasi PASS
- hazard list memuat item life-safety yang gagal (input auto-CAPA trigger 6e).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.aggregation import SessionScore

PASS_THRESHOLD = 0.80


@dataclass(frozen=True)
class Hazard:
    item_id: str
    code: str
    question_text: str
    max_score: float


@dataclass
class Verdict:
    pass_fail: str | None  # PASS | FAIL | None (belum ada item ter-score)
    total_score: float | None
    hazard: bool
    hazards: list[Hazard] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)


def compute_verdict(
    score: SessionScore,
    threshold: float = PASS_THRESHOLD,
) -> Verdict:
    """Keputusan PASS/FAIL + flag hazard untuk sebuah sesi audit."""
    if score.total_ratio is None:
        return Verdict(pass_fail=None, total_score=score.total_score,
                       hazard=False, blocking_reasons=["no_scored_items"])

    hazards = [
        Hazard(
            item_id=str(i.item_id), code=i.code,
            question_text=i.question_text, max_score=i.max_score,
        )
        for i in score.all_items
        if i.is_life_safety and not i.missing and not i.is_na and i.ratio == 0.0
    ]
    reasons: list[str] = []
    if score.items_missed > 0:
        reasons.append("has_unscored_items")
    if hazards:
        reasons.append("life_safety_hazard")
    if score.total_ratio < threshold:
        reasons.append("below_threshold")

    is_pass = score.total_ratio >= threshold and not reasons
    return Verdict(
        pass_fail="PASS" if is_pass else "FAIL",
        total_score=score.total_score,
        hazard=bool(hazards),
        hazards=hazards,
        blocking_reasons=reasons,
    )