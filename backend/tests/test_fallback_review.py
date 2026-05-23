import base64

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_review_works_without_openai_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/review",
        json={
            "language": "Python",
            "code": "def divide(a, b):\n    return a / b\nprint(divide(10, 0))",
            "focus": "bugs, security",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["used_ai"] is False
    assert body["risk_score"] > 0
    assert body["bugs"]


def test_review_accepts_base64_encoded_code(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code = "def divide(a, b):\n    return a / b\nprint(divide(10, 0))"

    response = client.post(
        "/api/review",
        json={
            "code": base64.b64encode(code.encode("utf-8")).decode("ascii"),
            "code_encoding": "base64",
            "focus": "bugs, security",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["used_ai"] is False
    assert any("division by zero" in bug["title"].lower() for bug in body["bugs"])


def test_hardcoded_password_gets_detected(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/review",
        json={
            "language": "Python",
            "code": 'password = "[redacted-demo-password]"\ndef login():\n    return password',
            "focus": "security",
        },
    )
    body = response.json()

    bug_titles = [bug["title"] for bug in body["bugs"]]
    assert response.status_code == 200
    assert any("hardcoded secret" in title.lower() for title in bug_titles)


def test_javascript_var_gets_improvement_suggestion(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/review",
        json={
            "language": "JavaScript",
            "code": "function add(a, b) {\n  var total = a + b;\n  return total;\n}",
            "focus": "readability",
        },
    )
    body = response.json()

    suggestions = " ".join(body["improvements"]).lower()
    assert response.status_code == 200
    assert "let or const" in suggestions


def test_html_strings_do_not_trigger_arithmetic_suggestion(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/review",
        json={
            "language": "JavaScript",
            "code": 'function render(items) {\n  var html = "<li>" + items[0] + "</li>";\n  return html;\n}\nrender([]);',
            "focus": "bugs, security",
        },
    )
    body = response.json()

    suggestions = " ".join(body["improvements"]).lower()
    assert response.status_code == 200
    assert "arithmetic edge cases" not in suggestions


def test_url_strings_do_not_trigger_arithmetic_suggestion(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/api/review",
        json={
            "language": "Python",
            "code": 'import requests\nresponse = requests.post("https://api.example.com/refund", json=payload)',
            "focus": "bugs, reliability",
        },
    )
    body = response.json()

    suggestions = " ".join(body["improvements"]).lower()
    test_cases = " ".join(body["test_cases"]).lower()
    assert response.status_code == 200
    assert "arithmetic edge cases" not in suggestions
    assert "division or arithmetic" not in test_cases
