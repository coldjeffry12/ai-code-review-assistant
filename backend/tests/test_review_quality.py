from app.schemas import BugFinding, ReviewRequest, ReviewResponse
from app.services.reviewer import _fallback_review, _merge_safety_checks, _normalize_review_response


def _review_with_bug(severity: str, risk_score: int = 15) -> ReviewResponse:
    return ReviewResponse(
        summary="Test review",
        risk_score=risk_score,
        bugs=[
            BugFinding(
                title=f"{severity} test issue",
                severity=severity,
                explanation="Test issue explanation.",
                suggested_fix="Fix the test issue.",
            )
        ],
        improvements=["Improve validation."],
        test_cases=["Test invalid input."],
        fixed_code=None,
        used_ai=True,
    )


def test_critical_bug_forces_risk_score_floor():
    review = _normalize_review_response(_review_with_bug("Critical", 15))

    assert review.risk_score >= 70


def test_high_bug_forces_risk_score_floor():
    review = _normalize_review_response(_review_with_bug("High", 15))

    assert review.risk_score >= 50


def test_fallback_placeholder_is_not_merged_with_real_ai_bugs():
    ai_review = _review_with_bug("Critical", 15)
    fallback_review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="def add(a, b):\n    return a + b",
            focus="bugs",
        )
    )

    merged = _merge_safety_checks(ai_review, fallback_review)
    titles = [bug.title for bug in merged.bugs]

    assert "No critical issue detected by fallback engine" not in titles
    assert any("critical test issue" in title.lower() for title in titles)
    assert merged.risk_score >= 70


def test_duplicate_test_cases_are_removed():
    review = ReviewResponse(
        summary="Test review",
        risk_score=30,
        bugs=[],
        improvements=["Improve validation.", "Improve validation."],
        test_cases=[
            "Test invalid input.",
            "Test invalid input.",
            "  Test invalid input.  ",
            "Test valid input.",
        ],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(review)

    assert normalized.test_cases == ["Test invalid input.", "Test valid input."]
    assert normalized.improvements == ["Improve validation."]


def test_discount_sample_gets_fallback_runtime_and_business_rule_findings():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def calculate_discount(price, discount):
    final_price = price - (price * discount)

    if final_price < 0:
        return 0

    return final_price

print(calculate_discount(100, 1.5))
print(calculate_discount("100", 0.2))""",
            focus="bugs, business logic",
        )
    )

    finding_text = " ".join(
        [bug.title + " " + bug.explanation + " " + bug.suggested_fix for bug in review.bugs]
        + review.improvements
    ).lower()

    assert "typeerror" in finding_text or "type error" in finding_text
    assert "discount range" in finding_text
    assert review.risk_score >= 50
