from app.schemas import Evidence, Hypothesis, HypothesisStatus


WEIGHTS = {"weak": 1.0, "medium": 2.0, "strong": 3.0}


def score_hypothesis(hypothesis: Hypothesis, evidence_by_id: dict[str, Evidence]) -> Hypothesis:
    support = sum(WEIGHTS[evidence_by_id[e].strength] for e in hypothesis.supporting_evidence if e in evidence_by_id)
    contradict = sum(WEIGHTS[evidence_by_id[e].strength] for e in hypothesis.contradicting_evidence if e in evidence_by_id)
    hypothesis.score = round(support - contradict, 2)

    if support >= 5 and contradict == 0:
        hypothesis.status = HypothesisStatus.CONFIRMED
    elif contradict >= 3 and support < 3:
        hypothesis.status = HypothesisStatus.RULED_OUT
    elif support == 0 and contradict == 0:
        hypothesis.status = HypothesisStatus.INCONCLUSIVE
    else:
        hypothesis.status = HypothesisStatus.TESTING
    return hypothesis
