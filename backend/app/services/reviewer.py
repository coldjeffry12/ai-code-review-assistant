import asyncio
import difflib
import hashlib
import json
import os
import re
import time
from typing import Any, Dict

from openai import OpenAI

from app.schemas import ReviewRequest, ReviewResponse, BugFinding


_REVIEW_CACHE: dict[str, dict[str, Any]] = {}

_GENERIC_TEST_CASES = {
    "test normal valid input.",
    "test empty or missing input.",
    "test invalid data type input.",
    "test boundary values and large input.",
    "test error-handling behavior.",
}


def _cache_limit() -> int:
    return _int_env("REVIEW_CACHE_MAX_ENTRIES", 50, 1, 500)


def _similarity_threshold() -> float:
    try:
        value = float(os.getenv("REVIEW_SIMILARITY_THRESHOLD", "0.9"))
    except ValueError:
        value = 0.9
    return max(0.5, min(value, 0.99))


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
        return _dedupe_strings(items) or fallback
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return fallback


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item.strip()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(item.strip())
    return unique_items


def _has_probable_division(code: str) -> bool:
    code_without_strings = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "\"\"", code)
    return bool(re.search(r"[\w)\]]+\s*/\s*[\w(\[]+", code_without_strings))


def _is_ollama_base_url(base_url: str) -> bool:
    normalized = base_url.lower()
    return "ollama" in normalized or "localhost:11434" in normalized or "127.0.0.1:11434" in normalized


def _int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _cache_key(payload: ReviewRequest) -> str:
    fingerprint = "|".join(
        [
            payload.language.strip().lower(),
            payload.focus.strip().lower(),
            hashlib.sha256(payload.code.encode("utf-8")).hexdigest(),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _normalize_code_for_similarity(code: str) -> str:
    return re.sub(r"\s+", " ", code.strip())


def _clone_response(response: ReviewResponse) -> ReviewResponse:
    return response.model_copy(deep=True)


def _cached_response(payload: ReviewRequest) -> ReviewResponse | None:
    entry = _REVIEW_CACHE.get(_cache_key(payload))
    if not entry:
        return None

    cached_review = _clone_response(entry["response"])
    cached_review.review_source = "cache"
    cached_review.cache_hit = True
    cached_review.similarity_used = 1.0
    return cached_review


def _find_similar_cached_review(payload: ReviewRequest) -> dict[str, Any] | None:
    current_code = _normalize_code_for_similarity(payload.code)
    if not current_code:
        return None

    best_entry: dict[str, Any] | None = None
    best_score = 0.0
    threshold = _similarity_threshold()
    language = payload.language.strip().lower()
    focus = payload.focus.strip().lower()

    for entry in _REVIEW_CACHE.values():
        if entry["language"] != language or entry["focus"] != focus:
            continue
        previous_code = _normalize_code_for_similarity(entry["code"])
        score = difflib.SequenceMatcher(None, previous_code, current_code).ratio()
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= threshold:
        return {**best_entry, "similarity": round(best_score, 3)}

    return None


def _diff_summary(previous_code: str, current_code: str, max_lines: int = 24) -> str:
    diff_lines = list(
        difflib.unified_diff(
            previous_code.splitlines(),
            current_code.splitlines(),
            fromfile="previous",
            tofile="current",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return "No text differences detected."

    selected_lines = diff_lines[:max_lines]
    if len(diff_lines) > max_lines:
        selected_lines.append("... diff truncated ...")
    return "\n".join(selected_lines)


def _similar_review_context(entry: dict[str, Any] | None, current_code: str) -> str:
    if not entry:
        return ""

    previous_review: ReviewResponse = entry["response"]
    bug_titles = [f"{bug.severity}: {bug.title}" for bug in previous_review.bugs[:6]]
    return f"""
Previous similar review context:
- Similarity: {entry["similarity"]:.0%}
- Previous summary: {previous_review.summary}
- Previous risk score: {previous_review.risk_score}/100
- Previous findings: {", ".join(bug_titles) if bug_titles else "No concrete findings"}

Changed code summary:
{_diff_summary(entry["code"], current_code)}

Use this previous review only as context. Review the full current code and return a fresh result for the current code.
"""


def _store_review(payload: ReviewRequest, response: ReviewResponse) -> None:
    key = _cache_key(payload)
    review_to_store = _clone_response(response)
    review_to_store.cache_hit = False
    _REVIEW_CACHE[key] = {
        "language": payload.language.strip().lower(),
        "focus": payload.focus.strip().lower(),
        "code": payload.code,
        "response": review_to_store,
        "created_at": time.time(),
    }

    while len(_REVIEW_CACHE) > _cache_limit():
        oldest_key = min(_REVIEW_CACHE, key=lambda cache_key: _REVIEW_CACHE[cache_key]["created_at"])
        _REVIEW_CACHE.pop(oldest_key, None)


def _clear_review_cache() -> None:
    _REVIEW_CACHE.clear()


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

    return findings


def _is_placeholder_bug(bug: BugFinding) -> bool:
    placeholder_titles = {
        "no specific bug reported",
        "no critical issue detected by fallback engine",
    }
    return bug.title.strip().lower() in placeholder_titles


def _normalize_severity(severity: str) -> str:
    normalized = severity.strip().lower()
    if "critical" in normalized:
        return "Critical"
    if "high" in normalized:
        return "High"
    if "medium" in normalized or "moderate" in normalized:
        return "Medium"
    return "Low"


def _severity_floor(bugs: list[BugFinding]) -> int:
    severities = {_normalize_severity(bug.severity) for bug in bugs if not _is_placeholder_bug(bug)}
    if "Critical" in severities:
        return 80
    if "High" in severities:
        return 51
    if "Medium" in severities:
        return 30
    return 10


def _bug_text(bug: BugFinding) -> str:
    return " ".join([bug.title, bug.severity, bug.explanation, bug.suggested_fix]).lower()


def _bug_category(bug: BugFinding) -> str:
    text = _bug_text(bug)

    category_patterns = [
        ("sql_injection", ["sql injection", "parameterized quer", "prepared statement", "unsafe query"]),
        ("raw_card", ["raw card", "card number", "card_number", "cardnumber", "credit card", "payment card", "cvv", "pci"]),
        ("path_traversal", ["path traversal", "arbitrary file", "unsafe filename", "file write", "directory traversal"]),
        ("dynamic_execution", ["eval", "exec", "dynamic execution", "arbitrary code"]),
        ("hardcoded_secret", ["hardcoded", "api key", "secret", "token", "password", "admin_token", "payment_token"]),
        ("type_mismatch", ["typeerror", "type error", "type mismatch", "non-numeric", "invalid data type"]),
        ("negative_payment", ["negative refund", "negative payment", "refund amount", "payment amount", "discount range"]),
        ("collection_none", ["fetchone", "none check", "empty collection", "index access", "list index", "array access"]),
        ("request_exception", ["request exception", "network exception", "without exception handling", "raise_for_status"]),
        ("request_timeout", ["missing timeout", "without a timeout"]),
        ("db_connection", ["connection not closed", "db connection", "database connection", "delete_user"]),
        ("transaction_rollback", ["rollback", "transaction", "multi-step database"]),
        ("datetime_json", ["datetime", "json serial", "json.dumps", "created_at"]),
        ("weak_error_handling", ["weak error handling", "bare except", "broad exception"]),
        ("null_pointer", ["null pointer", "nullptr", "null reference"]),
        ("html_injection", ["html injection", "xss", "dangerouslysetinnerhtml", "innerhtml"]),
        ("division_by_zero", ["division by zero", "denominator", "divide by zero"]),
    ]

    for category, patterns in category_patterns:
        if any(pattern in text for pattern in patterns):
            return category

    return _bug_title_key(bug.title)


def _dedupe_bug_findings(bugs: list[BugFinding]) -> list[BugFinding]:
    deduped: list[BugFinding] = []
    seen_categories: set[str] = set()

    for bug in bugs:
        if _is_placeholder_bug(bug):
            continue
        category = _bug_category(bug)
        if category in seen_categories:
            continue
        seen_categories.add(category)
        deduped.append(bug)

    return deduped


def _clean_test_cases(test_cases: list[str]) -> list[str]:
    deduped = _dedupe_strings(test_cases)
    specific_tests = [item for item in deduped if item.strip().lower() not in _GENERIC_TEST_CASES]
    return specific_tests or deduped


def _score_security_payment_context(bugs: list[BugFinding]) -> int | None:
    categories = {_bug_category(bug) for bug in bugs}
    critical_count = sum(1 for bug in bugs if bug.severity == "Critical")

    if "sql_injection" in categories and ("raw_card" in categories or "hardcoded_secret" in categories):
        return 100
    if critical_count >= 2:
        return 95
    return None


def _normalize_review_response(review: ReviewResponse) -> ReviewResponse:
    real_bugs = _dedupe_bug_findings([
        BugFinding(
            title=bug.title,
            severity=_normalize_severity(bug.severity),
            explanation=bug.explanation,
            suggested_fix=bug.suggested_fix,
        )
        for bug in review.bugs
        if not _is_placeholder_bug(bug)
    ])

    risk_score = _coerce_risk_score(review.risk_score)
    if real_bugs:
        risk_score = max(risk_score, _severity_floor(real_bugs))
        security_payment_floor = _score_security_payment_context(real_bugs)
        if security_payment_floor is not None:
            risk_score = max(risk_score, security_payment_floor)
        if all(bug.severity == "Low" for bug in real_bugs):
            risk_score = min(max(risk_score, 10), 25)
    else:
        risk_score = min(max(risk_score, 10), 25)

    return ReviewResponse(
        summary=review.summary,
        risk_score=risk_score,
        bugs=real_bugs,
        improvements=_dedupe_strings(review.improvements),
        test_cases=_clean_test_cases(review.test_cases),
        fixed_code=review.fixed_code,
        used_ai=review.used_ai,
        review_source=review.review_source,
        cache_hit=review.cache_hit,
        similarity_used=review.similarity_used,
    )


def _bug_title_key(title: str) -> str:
    normalized = title.lower()
    normalized = re.sub(r"\b(possible|potential)\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _has_unsafe_sql_construction(code_lower: str) -> bool:
    has_sql = re.search(r"\b(select|insert|update|delete)\b", code_lower)
    has_concat_or_format = re.search(r"(\+\s*\w+|f[\"']|\.format\s*\(|%\s*\()", code_lower, re.DOTALL)
    return bool(has_sql and has_concat_or_format)


def _has_raw_card_handling(code_lower: str) -> bool:
    return bool(re.search(r"\b(card_number|cardnumber|credit_card|pan|cvv)\b", code_lower))


def _has_unsafe_path_construction(code_lower: str) -> bool:
    has_file_write = re.search(r"\b(open|write_text|write_bytes)\s*\(", code_lower) or ".save(" in code_lower
    has_user_filename = re.search(r"\b(filename|file_name|path|upload|user_input)\b", code_lower)
    has_path_join = "os.path.join" in code_lower or "pathlib.path" in code_lower or "/" in code_lower
    return bool(has_file_write and has_user_filename and has_path_join)


def _has_requests_post(code_lower: str) -> bool:
    return "requests.post" in code_lower


def _has_db_connection_without_close(code_lower: str) -> bool:
    has_connection = "sqlite3.connect" in code_lower or "get_db_connection" in code_lower or "db.connect" in code_lower
    if not has_connection:
        return False
    if "with sqlite3.connect" in code_lower or "with get_db_connection" in code_lower:
        return False
    if ".close(" not in code_lower:
        return True
    return "def delete_user" in code_lower and "return" in code_lower and "finally" not in code_lower


def _has_missing_transaction_rollback(code_lower: str) -> bool:
    write_count = len(re.findall(r"\.execute\s*\(\s*[f]?[\"']\s*(insert|update|delete)\b", code_lower))
    return write_count >= 2 and ".commit(" in code_lower and ".rollback(" not in code_lower


def _has_datetime_json_risk(code_lower: str) -> bool:
    return "json.dumps" in code_lower and ("datetime" in code_lower or "created_at" in code_lower)


def _has_unchecked_fetchone(code_lower: str) -> bool:
    if ".fetchone(" not in code_lower:
        return False

    assigned_results = re.findall(r"(\w+)\s*=\s*[^\n]*\.fetchone\s*\(", code_lower)
    if not assigned_results:
        return True

    for variable in assigned_results:
        guard_pattern = rf"if\s+(not\s+{re.escape(variable)}|{re.escape(variable)}\s+is\s+none|{re.escape(variable)}\s*==\s*none)"
        if not re.search(guard_pattern, code_lower):
            return True

    return False


def _has_negative_payment_amount(code_lower: str) -> bool:
    has_payment_context = re.search(r"\b(refund|payment|charge|amount|total)\b", code_lower)
    has_negative_path = re.search(r"(<\s*0|-\s*amount|amount\s*=\s*-|refund_amount\s*=\s*-|payment_amount\s*=\s*-)", code_lower)
    return bool(has_payment_context and has_negative_path)


def _merge_safety_checks(ai_review: ReviewResponse, safety_review: ReviewResponse) -> ReviewResponse:
    ai_review = _normalize_review_response(ai_review)
    safety_review = _normalize_review_response(safety_review)
    safety_bugs = [bug for bug in safety_review.bugs if not _is_placeholder_bug(bug)]
    ai_bugs = [bug for bug in ai_review.bugs if not _is_placeholder_bug(bug)]

    seen_bug_categories = {_bug_category(bug) for bug in ai_bugs}
    merged_bugs = list(ai_bugs)
    added_safety_count = 0
    for bug in safety_bugs:
        bug_category = _bug_category(bug)
        if bug_category not in seen_bug_categories:
            merged_bugs.append(bug)
            seen_bug_categories.add(bug_category)
            added_safety_count += 1

    improvements = _dedupe_strings(ai_review.improvements + safety_review.improvements)
    test_cases = _dedupe_strings(ai_review.test_cases + safety_review.test_cases)
    summary = ai_review.summary
    if added_safety_count:
        summary = f"AI review completed. Local safety checks also flagged {added_safety_count} additional issue(s)."

    return _normalize_review_response(ReviewResponse(
        summary=summary,
        risk_score=max(ai_review.risk_score, safety_review.risk_score),
        bugs=merged_bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=ai_review.fixed_code,
        used_ai=True,
        review_source="ai",
    ))


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
        is_payment_or_admin_secret = bool(re.search(r"(admin|payment|stripe|paypal|secret|token)", code_lower))
        bugs.append(
            BugFinding(
                title="Hardcoded payment/admin secret in source code" if is_payment_or_admin_secret else "Hardcoded secret in source code",
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

    if _has_unsafe_sql_construction(code_lower):
        bugs.append(
            BugFinding(
                title="SQL injection from unsafe query construction",
                severity="Critical",
                explanation="The code appears to build SQL using string concatenation, formatting, or an f-string with external values.",
                suggested_fix="Use parameterized queries and never insert user-controlled values directly into SQL strings.",
            )
        )
        risk_score += 40

    if _has_raw_card_handling(code_lower):
        bugs.append(
            BugFinding(
                title="Raw card number handling without tokenization",
                severity="Critical",
                explanation="The code appears to handle raw card numbers or CVV data directly, which creates serious payment security and PCI compliance risk.",
                suggested_fix="Use a payment provider tokenization flow and avoid storing, logging, or transmitting raw card data in application code.",
            )
        )
        risk_score += 40

    if _has_unsafe_path_construction(code_lower):
        bugs.append(
            BugFinding(
                title="Path traversal risk in file path construction",
                severity="Critical" if re.search(r"\b(open|write_text|write_bytes)\s*\(", code_lower) else "High",
                explanation="The code appears to construct a file path from user-controlled filename or path data before writing or saving a file.",
                suggested_fix="Normalize and validate filenames, reject path separators, and write only inside an allowed directory.",
            )
        )
        risk_score += 35

    if _has_requests_post(code_lower) and "timeout=" not in code_lower:
        bugs.append(
            BugFinding(
                title="requests.post call missing timeout",
                severity="Medium",
                explanation="The code sends an HTTP request without a timeout, so a slow external service can hang the request indefinitely.",
                suggested_fix="Pass a reasonable timeout to requests.post, such as timeout=10, and tune it for the external service.",
            )
        )
        risk_score += 15

    if _has_requests_post(code_lower) and "except" not in code_lower:
        bugs.append(
            BugFinding(
                title="requests.post call missing exception handling",
                severity="Medium",
                explanation="The code sends an HTTP request but does not appear to handle network errors, timeouts, or non-success responses.",
                suggested_fix="Wrap the call in targeted exception handling and call raise_for_status or handle unsuccessful status codes explicitly.",
            )
        )
        risk_score += 15

    if _has_db_connection_without_close(code_lower):
        bugs.append(
            BugFinding(
                title="Database connection may not close on early return",
                severity="High" if "def delete_user" in code_lower else "Medium",
                explanation="The code opens a database connection without a clear finally block, context manager, or guaranteed close path.",
                suggested_fix="Use a context manager or close the connection in a finally block so early returns and exceptions do not leak connections.",
            )
        )
        risk_score += 25

    if _has_missing_transaction_rollback(code_lower):
        bugs.append(
            BugFinding(
                title="Missing transaction rollback for multi-step database write",
                severity="High",
                explanation="The code performs multiple database write operations and commits, but does not appear to roll back if one step fails.",
                suggested_fix="Wrap related writes in one transaction and call rollback in the exception path before re-raising or returning an error.",
            )
        )
        risk_score += 25

    if _has_datetime_json_risk(code_lower):
        bugs.append(
            BugFinding(
                title="Datetime JSON serialization risk",
                severity="Medium",
                explanation="json.dumps does not serialize datetime objects by default, so exporting values such as created_at can crash or produce invalid output.",
                suggested_fix="Convert datetime values to ISO strings before json.dumps or provide a safe serializer.",
            )
        )
        risk_score += 15

    if _has_unchecked_fetchone(code_lower):
        bugs.append(
            BugFinding(
                title="fetchone result used without None check",
                severity="High" if "delete_user" in code_lower or "payment" in code_lower else "Medium",
                explanation="The code calls fetchone but does not appear to check whether the query returned a row before using the result.",
                suggested_fix="Check for None immediately after fetchone and return a clear not-found response before indexing or dereferencing the row.",
            )
        )
        risk_score += 20

    if _has_negative_payment_amount(code_lower):
        bugs.append(
            BugFinding(
                title="Negative refund/payment amount business logic risk",
                severity="Critical" if "refund" in code_lower and "payment" in code_lower else "High",
                explanation="The code appears to allow or create a negative refund, payment, charge, or amount value, which can break payment and accounting logic.",
                suggested_fix="Validate payment and refund amounts before processing, reject negative values, and define explicit business rules for credits.",
            )
        )
        risk_score += 30

    has_division = _has_probable_division(code)
    has_arithmetic = bool(re.search(r"[\w)\]\"']+\s*[-+*/]\s*[\w(\"']+", code))
    calls_function_with_string = bool(re.search(r"\w+\s*\([^)]*['\"][^'\"]+['\"][^)]*\)", code))

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

    if language in ["python", "py", "javascript", "typescript"] and has_arithmetic and calls_function_with_string:
        bugs.append(
            BugFinding(
                title="Type mismatch risk in arithmetic calculation",
                severity="High",
                explanation="The code performs arithmetic and the sample call passes a string value, which can cause a runtime type error or invalid calculation.",
                suggested_fix="Validate numeric inputs before arithmetic and return a clear error for non-numeric values.",
            )
        )
        risk_score += 25

    if "discount" in code_lower and re.search(r"final_price\s*<\s*0|return\s+0", code_lower):
        bugs.append(
            BugFinding(
                title="Discount range business rule issue",
                severity="Medium",
                explanation="The code clamps negative prices to zero, but it does not validate whether discount values outside the expected range are allowed.",
                suggested_fix="Validate the discount range, for example 0 <= discount <= 1, or document the intended business rule.",
            )
        )
        risk_score += 15

    if re.search(r"\[[0-9]+\]", code) and re.search(r"\(\s*\[\s*\]\s*\)|=\s*\[\s*\]", code):
        bugs.append(
            BugFinding(
                title="Unchecked collection/index access",
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

    if language == "sql" and _has_unsafe_sql_construction(code_lower):
        bugs.append(
            BugFinding(
                title="SQL injection from unsafe query construction",
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

    if "discount" in code_lower:
        improvements.append("Document and validate the expected discount range so business rules are explicit.")

    if not improvements:
        improvements.append("Add comments for complex logic and keep function names clear.")

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

    if _has_unsafe_sql_construction(code_lower):
        test_cases.append("Test malicious SQL input such as quoted OR conditions and verify parameterized queries are used.")

    if _has_raw_card_handling(code_lower):
        test_cases.append("Test payment flow with tokenized card data and verify raw card numbers are never stored or logged.")

    if _has_unsafe_path_construction(code_lower):
        test_cases.append("Test filenames containing ../ path traversal and verify writes stay inside the allowed upload directory.")

    if _has_requests_post(code_lower):
        test_cases.append("Test external API timeout, connection error, and non-2xx response handling.")

    if _has_missing_transaction_rollback(code_lower):
        test_cases.append("Test a failure in the second database write and verify the full transaction is rolled back.")

    if _has_db_connection_without_close(code_lower):
        test_cases.append("Test early returns and exceptions to verify database connections are always closed.")

    if _has_datetime_json_risk(code_lower):
        test_cases.append("Test JSON export with datetime values and verify created_at is serialized as an ISO string.")

    if _has_unchecked_fetchone(code_lower):
        test_cases.append("Test a missing database row and verify the code handles fetchone returning None.")

    if _has_negative_payment_amount(code_lower):
        test_cases.append("Test negative refund and payment amounts and verify the request is rejected before processing.")

    risk_score = min(risk_score, 100)
    summary = (
        reason
        or f"Fallback review completed for {payload.language}. Local rules found {len(bugs)} issue(s) and {len(improvements)} improvement idea(s)."
    )

    return _normalize_review_response(ReviewResponse(
        summary=summary,
        risk_score=risk_score,
        bugs=bugs,
        improvements=improvements,
        test_cases=test_cases,
        fixed_code=None,
        used_ai=False,
        review_source="fallback",
    ))


def _review_from_ai_json(parsed: Dict[str, Any]) -> ReviewResponse:
    fixed_code = parsed.get("fixed_code")
    if fixed_code is not None:
        fixed_code = str(fixed_code).strip() or None

    return _normalize_review_response(ReviewResponse(
        summary=str(parsed.get("summary") or "AI review completed.").strip(),
        risk_score=_coerce_risk_score(parsed.get("risk_score", 50)),
        bugs=_bug_findings(parsed.get("bugs", [])),
        improvements=_string_list(parsed.get("improvements"), ["Review readability, edge cases, and error handling."]),
        test_cases=_string_list(parsed.get("test_cases"), ["Test the main success path and important failure paths."]),
        fixed_code=fixed_code,
        used_ai=True,
        review_source="ai",
    ))


async def review_code(payload: ReviewRequest) -> ReviewResponse:
    exact_cached_review = _cached_response(payload)
    if exact_cached_review:
        return exact_cached_review

    similar_cached_review = _find_similar_cached_review(payload)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    is_ollama = _is_ollama_base_url(base_url)

    if not api_key:
        fallback_review = _fallback_review(payload, "Fallback review completed because OPENAI_API_KEY is not configured.")
        if similar_cached_review:
            fallback_review.review_source = "fallback_with_cache_context"
            fallback_review.similarity_used = similar_cached_review["similarity"]
        _store_review(payload, fallback_review)
        return fallback_review

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
Risk score must match issue severity:
- Critical issue: risk_score must be at least 80.
- High issue: risk_score must be at least 50.
- Medium issue: risk_score must be at least 30.
- Only Low issues: risk_score should usually be between 10 and 25.
If multiple Critical issues exist, risk_score should usually be 95-100.
If SQL injection appears with payment secrets or raw card handling, risk_score should be 100.
Do not give Low risk when Critical or High issues exist.
Mark issues as Critical only for crashes, data loss, security risks, or serious runtime failures.
Mention business logic issues separately from runtime bugs.
Look specifically for payment/card handling, SQL injection, path traversal, missing request timeouts,
missing request exception handling, missing DB rollback, unclosed DB connections, fetchone None checks,
datetime JSON serialization, and negative refund/payment amounts when relevant.
Avoid duplicate findings. If two issues describe the same root cause, return one stronger finding.
Prefer specific test cases over generic test ideas.
Keep fixed code practical and not overcomplicated.
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

{_similar_review_context(similar_cached_review, payload.code)}
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
        review = _merge_safety_checks(ai_review, safety_review)
        if similar_cached_review:
            review.review_source = "ai_with_cache_context"
            review.similarity_used = similar_cached_review["similarity"]
        _store_review(payload, review)
        return review
    except Exception:
        fallback_review = _fallback_review(
            payload,
            "AI review was unavailable or returned invalid JSON, so the backend used the fallback review engine.",
        )
        if similar_cached_review:
            fallback_review.review_source = "fallback_with_cache_context"
            fallback_review.similarity_used = similar_cached_review["similarity"]
        _store_review(payload, fallback_review)
        return fallback_review
