from app.schemas import BugFinding, ReviewRequest, ReviewResponse
from app.services.reviewer import _bug_category, _fallback_review, _merge_safety_checks, _normalize_review_response


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


def test_duplicate_hardcoded_secret_fallback_is_removed_when_ai_reports_secret():
    ai_review = ReviewResponse(
        summary="AI found a secret.",
        risk_score=70,
        bugs=[
            BugFinding(
                title="Hardcoded API key",
                severity="High",
                explanation="A secret is stored in source code.",
                suggested_fix="Move it to an environment variable.",
            )
        ],
        improvements=[],
        test_cases=["Test that the API key is loaded from the environment."],
        fixed_code=None,
        used_ai=True,
    )
    fallback_review = _fallback_review(
        ReviewRequest(language="Python", code='API_KEY = "demo"\nprint(API_KEY)', focus="security")
    )

    merged = _merge_safety_checks(ai_review, fallback_review)

    assert sum(1 for bug in merged.bugs if _bug_category(bug) == "hardcoded_secret") == 1


def test_duplicate_type_error_fallback_is_removed_when_ai_reports_type_mismatch():
    ai_review = ReviewResponse(
        summary="AI found type mismatch.",
        risk_score=60,
        bugs=[
            BugFinding(
                title="Type mismatch in arithmetic calculation",
                severity="High",
                explanation="The function can receive a string and then perform arithmetic.",
                suggested_fix="Validate numeric inputs.",
            )
        ],
        improvements=[],
        test_cases=["Test string price input raises a clear validation error."],
        fixed_code=None,
        used_ai=True,
    )
    fallback_review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='def total(price):\n    return price * 2\nprint(total("100"))',
            focus="bugs",
        )
    )

    merged = _merge_safety_checks(ai_review, fallback_review)

    assert sum(1 for bug in merged.bugs if _bug_category(bug) == "type_mismatch") == 1


def test_generic_test_cases_are_removed_when_specific_tests_exist():
    review = ReviewResponse(
        summary="Specific tests exist.",
        risk_score=80,
        bugs=[BugFinding(title="SQL injection", severity="Critical", explanation="Bad SQL.", suggested_fix="Use parameters.")],
        improvements=[],
        test_cases=[
            "Test normal valid input.",
            "Test invalid data type input.",
            "Test malicious SQL input is rejected.",
        ],
        fixed_code=None,
        used_ai=True,
    )

    normalized = _normalize_review_response(review)

    assert normalized.test_cases == ["Test malicious SQL input is rejected."]


def test_sql_injection_local_rule_creates_critical_severity():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='query = "SELECT * FROM users WHERE email = \'" + email + "\'"',
            focus="security",
        )
    )

    assert any(_bug_category(bug) == "sql_injection" and bug.severity == "Critical" for bug in review.bugs)
    assert review.risk_score >= 80


def test_requests_post_without_timeout_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='import requests\nresponse = requests.post("https://api.example.com/pay", json=payload)',
            focus="reliability",
        )
    )

    assert any(_bug_category(bug) == "request_timeout" for bug in review.bugs)


def test_requests_post_without_exception_handling_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code='import requests\nresponse = requests.post("https://api.example.com/pay", json=payload)',
            focus="reliability",
        )
    )

    assert any(_bug_category(bug) == "request_exception" for bug in review.bugs)


def test_datetime_inside_json_dumps_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="import json\nfrom datetime import datetime\ndata = {'created_at': datetime.utcnow()}\njson.dumps(data)",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "datetime_json" for bug in review.bugs)


def test_delete_user_connection_leak_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def delete_user(user_id):
    conn = sqlite3.connect("app.db")
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return True""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "db_connection" for bug in review.bugs)


def test_unchecked_fetchone_is_detected_when_another_fetchone_has_guard():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def create_order(user_id):
    conn = sqlite3.connect("shop.db")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return user["email"]

def delete_user(user_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    return True""",
            focus="bugs",
        )
    )

    assert any(_bug_category(bug) == "collection_none" for bug in review.bugs)


def test_negative_refund_amount_is_detected():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""def refund_payment(payment_id, amount):
    refund_amount = -amount
    return gateway.refund(payment_id, refund_amount)""",
            focus="business logic",
        )
    )

    assert any(_bug_category(bug) == "negative_payment" for bug in review.bugs)


def test_risk_score_becomes_100_for_multiple_critical_security_payment_issues():
    review = _fallback_review(
        ReviewRequest(
            language="Python",
            code="""PAYMENT_TOKEN = "demo"
card_number = request["card_number"]
query = "SELECT * FROM payments WHERE card = '" + card_number + "'"
open(os.path.join("/tmp/uploads", request["filename"]), "w").write("x")""",
            focus="security, payments",
        )
    )

    categories = {_bug_category(bug) for bug in review.bugs}
    assert {"sql_injection", "raw_card", "hardcoded_secret"}.issubset(categories)
    assert review.risk_score == 100
