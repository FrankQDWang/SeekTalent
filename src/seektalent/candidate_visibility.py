WORKBENCH_MIN_CANDIDATE_SCORE = 60


def is_workbench_visible_score(score: int | None) -> bool:
    return score is not None and score >= WORKBENCH_MIN_CANDIDATE_SCORE
