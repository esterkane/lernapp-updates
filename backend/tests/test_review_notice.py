from lernapp_ui.components import review_notice_text


def test_missing_evidence_does_not_claim_second_rater():
    text = review_notice_text(
        {"validator": None, "review_handoff": {"reasons": ["integration:evidence_in_text:private text"]}}
    )
    assert "Textbelege" in text
    assert "Zweitmeinung" not in text and "private text" not in text


def test_real_disagreement_requires_validator():
    assert "Zweitmeinung weicht ab" in review_notice_text({"validator": {"disagreeing_criteria": ["grammatik"]}})
    assert "Zweitmeinung weicht ab" not in review_notice_text(
        {"review_handoff": {"reasons": ["disagreement:grammatik:4vs2"]}}
    )


def test_agreeing_validator_and_uncertainty():
    text = review_notice_text(
        {"validator": {"disagreeing_criteria": []}, "review_handoff": {"reasons": ["confidence:grammatik=0.4"]}}
    )
    assert "unsicher" in text and "Zweitmeinung" not in text
