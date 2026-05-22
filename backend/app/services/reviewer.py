import asyncio
import json
import os
import re
from typing import Any, Dict

from openai import OpenAI

from app.schemas import ReviewRequest, ReviewResponse, BugFinding


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """Parse a JSON object from an AI response, including markdown-wrapped output."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object.")

    return parsed


def _coerce_risk_score(value: Any) -> int:
    if isinstance(value, (int, float)):
        score = int(value)
    elif isinstance(value, str):
        match = re.search(r"\d+", value)
        score = int(match.group(0)) if match else 50
    else:
        score = 50

    return max(0, min(score, 100))


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or fallback
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return fallback


def _has_probable_division(code: str) -> bool:
    return bool(re.search(r"[\w)\]]+\s*/\s*[\w(\[]+", code))


def _is_ollama_base_url(base_url: str) -> bool:
    normalized = base_url.lower()
    return "ollama" in normalized or "localhost:11434" in normalized or "127.0.0.1:11434" in normalized


def _int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _bug_findings(value: Any) -> list[BugFinding]:
    findings: list[BugFinding] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            findings.append(
                BugFinding(
                    title=str(item.get("title") or "Potential issue").strip(),
                    severity=str(item.get("severity") or "Medium").strip(),
                    explanation=str(item.get("explanation") or "The AI review flagged this area for closer inspection.").strip(),
                    suggested_fix=str(item.get("suggested_fix") or "Review this code path and add a safer implementation.").strip(),
                )
            )

    return findings or [
        BugFinding(
            title="No specific bug reported",
            severity="Low",
            explanation="The AI response did not include a concrete bug finding.",
            suggested_fix="Review edge cases manually and add tests around the most important behavior.",
        )
    ]


def _is_placeholder_bug(bug: BugFinding) -> bool:
    return bug.title.lower() == "no specific bug reported"


def _bug_title_key(title: str) -> str:
    normalized = title.lower()
    normalized = re.sub(r"\b(possible|potential)\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _merge_safety_checks(ai_review: ReviewResponse, safety_review: ReviewResponse) -> ReviewResponse:
    safety_bugs = [bug for bug in safety_review.bugs if not _is_placeholder_bug(bug)]
    ai_bugs = ai_review.bugs
    if safety_bugs:
        ai_bugs = [bug for bug in ai_bugs if not _is_placeholder_bug(bug)]

    seen_bug_titles = {_bug_title_key(bug.title) for bug in ai_bugs}
    merged_bugs = list(ai_bugs)
    for bug in safety_bugs:
        bug_key = _bug_title_key(bug.title)
        has_similar_bug = any(bug_key == seen or bug_key in seen or seen in bug_key for seen in seen_bug_titles)
        if not has_similar_bug:
            merged_bugs.append(bug)
            seen_bug_titles.add(bug_key)

    if not merged_bugs:
        merged_bugs = ai_review.bugs

    improvements = list(dict.fromkeys(ai_review.improvements + safety_review.improvements))
    test_cases = list(dict.fromkeys(ai_review.test_cases + safety_review.test_cases))
    summary = ai_review.summary
    if safety_bugs:
        summary = f"AI review completed. Local safety checks also flagged {len(safety_bugs)} issue(s)."

    return ReviewResponse(
        summary=summary,
        risk_score=max(ai_review.risk_score, safety_review.risk_score),
        bugs=merged_bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=ai_review.fixed_code,
        used_ai=True,
    )


def _fallback_review(payload: ReviewRequest, reason: str | None = None) -> ReviewResponse:
    """Local rules keep the demo usable when no AI API key is configured."""
    code = payload.code
    language = payload.language.lower()
    code_lower = code.lower()
    bugs: list[BugFinding] = []
    improvements: list[str] = []
    test_cases: list[str] = []
    risk_score = 15

    if re.search(r"(password|api[_-]?key|secret|token)\s*=", code_lower):
        bugs.append(
            BugFinding(
                title="Possible hardcoded secret",
                severity="High",
                explanation="The code appears to assign a password, API key, token, or secret directly in source code.",
                suggested_fix="Move secrets to environment variables and never commit them to GitHub.",
            )
        )
        risk_score += 25

    if "except:" in code or ("catch (" in code and "console.log" not in code and "throw" not in code):
        bugs.append(
            BugFinding(
                title="Weak error handling",
                severity="Medium",
                explanation="The code may catch errors too broadly or without clear handling.",
                suggested_fix="Catch specific exceptions and log enough context to debug safely.",
            )
        )
        risk_score += 15

    has_division = _has_probable_division(code)

    if re.search(r"/\s*0\b", code) or (has_division and re.search(r"\([^)]*,\s*0\s*\)", code)):
        bugs.append(
            BugFinding(
                title="Potential division by zero",
                severity="High",
                explanation="The code divides values and appears to call the function with zero or divide by zero directly.",
                suggested_fix="Validate the denominator before division and return a clear error for zero values.",
            )
        )
        risk_score += 25

    if re.search(r"\[[0-9]+\]", code) and re.search(r"\(\s*\[\s*\]\s*\)|=\s*\[\s*\]", code):
        bugs.append(
            BugFinding(
                title="Possible empty collection access",
                severity="High",
                explanation="The code indexes into a list or array while also showing an empty collection case.",
                suggested_fix="Check that the collection has enough items before reading by index.",
            )
        )
        risk_score += 20

    if language in ["python", "py"] and re.search(r"\b(eval|exec)\s*\(", code):
        bugs.append(
            BugFinding(
                title="Unsafe dynamic execution",
                severity="Critical",
                explanation="eval or exec can execute arbitrary input and create serious security risk.",
                suggested_fix="Replace dynamic execution with explicit parsing or a safe allow-list of operations.",
            )
        )
        risk_score += 35

    if language in ["javascript", "typescript", "react"] and ("innerHTML" in code or "dangerouslySetInnerHTML" in code):
        bugs.append(
            BugFinding(
                title="Possible unsafe HTML injection",
                severity="High",
                explanation="Writing raw HTML into the DOM can allow cross-site scripting when data is not trusted.",
                suggested_fix="Render text safely or sanitize trusted HTML with a vetted sanitizer.",
            )
        )
        risk_score += 25

    if language in ["typescript", "react"] and re.search(r"\w+\??\s*:\s*[^)\n]+", code) and re.search(r"\w+\.\w+", code):
        bugs.append(
            BugFinding(
                title="Possible missing null or undefined check",
                severity="Medium",
                explanation="The code accesses a property on a value that appears optional or nullable.",
                suggested_fix="Add a guard clause, optional chaining, or make the parameter required.",
            )
        )
        risk_score += 15

    if language in ["java", "c++", "cpp"] and re.search(r"=\s*null\b|=\s*nullptr\b", code) and re.search(r"\.\w+\s*\(|->\w+\s*\(", code):
        bugs.append(
            BugFinding(
                title="Possible null pointer risk",
                severity="High",
                explanation="The code assigns a null value and later calls a method or member through an object reference.",
                suggested_fix="Validate the object before dereferencing it or avoid passing null into the function.",
            )
        )
        risk_score += 25

    if language == "sql" and re.search(r"(select|insert|update|delete).*(\+|f\"|f')", code_lower, re.DOTALL):
        bugs.append(
            BugFinding(
                title="Possible SQL injection",
                severity="Critical",
                explanation="The query appears to build SQL with string interpolation or concatenation.",
                suggested_fix="Use parameterized queries instead of building SQL strings from user input.",
            )
        )
        risk_score += 35

    if language in ["python", "py"] and "print(" in code:
        improvements.append("Replace print statements with structured logging for production code.")

    if language in ["javascript", "typescript", "react"] and "var " in code:
        improvements.append("Use let or const instead of var to avoid function-scope issues.")

    if len(code.splitlines()) > 80:
        improvements.append("Consider splitting this code into smaller functions or modules.")

    if has_division:
        improvements.append("Add explicit validation around arithmetic edge cases such as zero, null, or missing values.")

    if not improvements:
        improvements.append("Add comments for complex logic and keep function names clear.")

    if not bugs:
        bugs.append(
            BugFinding(
                title="No critical issue detected by fallback engine",
                severity="Low",
                explanation="The fallback engine did not detect obvious high-risk patterns. A real AI review may find deeper logic issues.",
                suggested_fix="Connect an AI API key for deeper analysis.",
            )
        )

    test_cases.extend(
        [
            "Test normal valid input.",
            "Test empty or missing input.",
            "Test invalid data type input.",
            "Test boundary values and large input.",
            "Test error-handling behavior.",
        ]
    )

    if has_division:
        test_cases.append("Test division or arithmetic behavior with zero and negative values.")

    if re.search(r"(password|api[_-]?key|secret|token)", code_lower):
        test_cases.append("Test that secrets are loaded from environment variables and never returned in logs.")

    risk_score = min(risk_score, 100)
    summary = (
        reason
        or f"Fallback review completed for {payload.language}. Local rules found {len(bugs)} issue(s) and {len(improvements)} improvement idea(s)."
    )

    return ReviewResponse(
        summary=summary,
        risk_score=risk_score,
        bugs=bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=None,
        used_ai=False,
    )


def _review_from_ai_json(parsed: Dict[str, Any]) -> ReviewResponse:
    fixed_code = parsed.get("fixed_code")
    if fixed_code is not None:
        fixed_code = str(fixed_code).strip() or None

    return ReviewResponse(
        summary=str(parsed.get("summary") or "AI review completed.").strip(),
        risk_score=_coerce_risk_score(parsed.get("risk_score", 50)),
        bugs=_bug_findings(parsed.get("bugs", [])),
        improvements=_string_list(parsed.get("improvements"), ["Review readability, edge cases, and error handling."]),
        test_cases=_string_list(parsed.get("test_cases"), ["Test the main success path and important failure paths."]),
        fixed_code=fixed_code,
        used_ai=True,
    )


async def review_code(payload: ReviewRequest) -> ReviewResponse:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    is_ollama = _is_ollama_base_url(base_url)

    if not api_key:
        return _fallback_review(payload, "Fallback review completed because OPENAI_API_KEY is not configured.")

    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "90" if is_ollama else "30"))
    client_options: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_options["base_url"] = base_url
    client = OpenAI(**client_options)

    system_prompt = """
You are a senior software engineer and security-aware code reviewer.
Return only valid JSON.
Do not use markdown.
Use null for fixed_code unless the fix is short and can be represented as a valid escaped JSON string.
Do not put raw line breaks inside JSON string values.
Your JSON must match this structure:
{
  "summary": "short summary",
  "risk_score": 0,
  "bugs": [
    {
      "title": "bug title",
      "severity": "Low/Medium/High/Critical",
      "explanation": "why this is a problem",
      "suggested_fix": "how to fix it"
    }
  ],
  "improvements": ["suggestion 1"],
  "test_cases": ["test idea 1"],
  "fixed_code": null
}
"""

    user_prompt = f"""
Review this {payload.language} code.

Focus areas:
{payload.focus}

Code:
```{payload.language}
{payload.code}
```
"""

    completion_options: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }

    if is_ollama:
        completion_options["extra_body"] = {
            "options": {
                "num_ctx": _int_env("OLLAMA_NUM_CTX", 512, 256, 8192),
                "num_predict": _int_env("OLLAMA_NUM_PREDICT", 128, 64, 4096),
            }
        }

    try:
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            **completion_options,
        )

        content = completion.choices[0].message.content or "{}"
        parsed = _safe_json_loads(content)
        ai_review = _review_from_ai_json(parsed)
        safety_review = _fallback_review(payload)
        return _merge_safety_checks(ai_review, safety_review)
    except Exception:
        return _fallback_review(
            payload,
            "AI review was unavailable or returned invalid JSON, so the backend used the fallback review engine.",
        )
