from fastapi.testclient import TestClient

from app.main import app
from app.services.reviewer import _clear_review_cache


client = TestClient(app)


def setup_function():
    _clear_review_cache()


def test_exact_same_review_uses_cache(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    payload = {
        "language": "Python",
        "code": "def divide(a, b):\n    return a / b\nprint(divide(10, 0))",
        "focus": "bugs, security",
    }

    first_response = client.post("/api/review", json=payload)
    second_response = client.post("/api/review", json=payload)

    first_body = first_response.json()
    second_body = second_response.json()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_body["cache_hit"] is False
    assert second_body["cache_hit"] is True
    assert second_body["review_source"] == "cache"
    assert second_body["similarity_used"] == 1.0
    assert second_body["bugs"] == first_body["bugs"]


def test_similar_changed_code_uses_previous_review_context_without_cache_hit(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    original_payload = {
        "language": "Python",
        "code": """def calculate_discount(price, discount):
    final_price = price - (price * discount)

    if final_price < 0:
        return 0

    return final_price

print(calculate_discount(100, 1.5))
print(calculate_discount("100", 0.2))""",
        "focus": "bugs, business logic",
    }
    changed_payload = {
        **original_payload,
        "code": original_payload["code"].replace("1.5", "1.2"),
    }

    first_response = client.post("/api/review", json=original_payload)
    second_response = client.post("/api/review", json=changed_payload)
    second_body = second_response.json()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_body["cache_hit"] is False
    assert second_body["review_source"] == "fallback_with_cache_context"
    assert second_body["similarity_used"] is not None
    assert second_body["similarity_used"] >= 0.9
