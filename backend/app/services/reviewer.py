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

_JAVASCRIPT_LANGUAGES = {"javascript", "js", "node", "node.js", "express", "typescript", "ts", "react"}

_NODE_MARKERS = (
    "require(",
    "express",
    "axios",
    "fs.",
    "jwt",
    "child_process",
    "module.exports",
    "app.get",
    "app.post",
    "router.get",
    "router.post",
)


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


def _is_javascript_language(language: str | None) -> bool:
    if not language:
        return False
    return language.strip().lower() in _JAVASCRIPT_LANGUAGES


def _code_looks_like_node_js(code_lower: str) -> bool:
    return any(marker in code_lower for marker in _NODE_MARKERS)


def _selected_language_matches_code(language: str | None, code_lower: str) -> bool:
    if _is_javascript_language(language) and _code_looks_like_node_js(code_lower):
        return True
    return False


def _detected_review_language(selected_language: str, code: str) -> str:
    code_lower = code.lower()
    selected = selected_language.strip() or "Plain Text"
    normalized_selected = selected.lower()

    if _code_looks_like_node_js(code_lower):
        return "JavaScript"
    if re.search(r"\b(public|private|protected)\s+class\b|\bsystem\.out\.println\b|\bstring\[\]\s+args\b|\.getbytes\s*\(", code_lower):
        return "Java"
    if re.search(r"#include\s*<|std::|->\w+\s*\(|\bnullptr\b", code_lower):
        return "C++"
    if re.search(r"\binterface\s+\w+|\btype\s+\w+\s*=|:\s*(string|number|boolean)\b", code_lower) and normalized_selected in {"typescript", "ts", "react"}:
        return "TypeScript"
    if re.search(r"\b(select|insert|update|delete)\b", code_lower) and "def " not in code_lower and "function " not in code_lower:
        return "SQL"
    if re.search(r"^\s*def\s+\w+\s*\(|^\s*import\s+\w+|^\s*from\s+\w+\s+import", code_lower, re.MULTILINE):
        return "Python"
    if _is_javascript_language(selected):
        return "JavaScript"
    return selected


def _redact_for_ai_retry(code: str) -> str:
    redacted = re.sub(r"rm\s+-rf\s+[^\"'\n;)]+", "[dangerous shell command omitted]", code, flags=re.IGNORECASE)
    redacted = re.sub(r"(?i)(password|api[_-]?key|secret|token|admin[_-]?key)\s*=\s*([\"']).*?\2", r"\1 = \"[redacted-demo-secret]\"", redacted)
    return redacted


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


def _apply_similar_cache_metadata(review: ReviewResponse, similar_cached_review: dict[str, Any] | None) -> ReviewResponse:
    if similar_cached_review:
        review.review_source = f"{review.review_source}_with_cache_context"
        review.similarity_used = similar_cached_review["similarity"]
    return review


def _fallback_after_ai_error(payload: ReviewRequest, similar_cached_review: dict[str, Any] | None = None) -> ReviewResponse:
    review = _fallback_review(payload)
    review.review_source = "fallback_after_ai_error"
    return _apply_similar_cache_metadata(review, similar_cached_review)


def _ai_user_prompt(payload: ReviewRequest, similar_cached_review: dict[str, Any] | None = None, retry_safe_mode: bool = False) -> str:
    detected_language = _detected_review_language(payload.language, payload.code)
    code_for_prompt = _redact_for_ai_retry(payload.code) if retry_safe_mode else payload.code
    retry_instruction = ""
    if retry_safe_mode:
        retry_instruction = """
This is a defensive review retry. Some dangerous string literals were redacted only to keep the AI request safe.
Still review the code structure and identify likely security and reliability issues.
Do not provide exploit steps or executable attack commands.
"""

    return f"""
Review this code for a defensive software security/code quality audit.

Selected language from UI:
{payload.language}

Detected code language:
{detected_language}

If selected language and detected language differ, review the actual pasted code using the detected language.
Mention the language mismatch only as a Low note if it matters.

Focus areas:
{payload.focus}

Code:
```{detected_language}
{code_for_prompt}
```

{retry_instruction}
{_similar_review_context(similar_cached_review, payload.code)}
"""


def _ai_local_findings_prompt(payload: ReviewRequest, safety_review: ReviewResponse) -> str:
    detected_language = _detected_review_language(payload.language, payload.code)
    bug_lines = [
        f"- {bug.severity}: {bug.title}. {bug.explanation} Fix: {bug.suggested_fix}"
        for bug in safety_review.bugs
    ] or ["- No concrete local bug findings."]
    improvement_lines = [f"- {item}" for item in safety_review.improvements] or ["- No local improvement notes."]
    test_lines = [f"- {item}" for item in safety_review.test_cases] or ["- No local test ideas."]

    return f"""
The direct raw-code AI review failed, so complete a defensive AI review using this local static-analysis context.
Do not claim you executed the code.
Do not invent exploit steps.
Use the findings below as evidence and improve the wording, severity consistency, summary, and test ideas.
Return the same strict JSON structure.

Selected language from UI: {payload.language}
Detected code language: {detected_language}
Code size: {len(payload.code.splitlines())} lines
Focus areas: {payload.focus}

Local safety findings:
{chr(10).join(bug_lines)}

Local improvement notes:
{chr(10).join(improvement_lines)}

Local test ideas:
{chr(10).join(test_lines)}
"""


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
    title = bug.title.lower()

    title_first_patterns = [
        ("language_mismatch", ["language mismatch", "wrong language"]),
        ("path_traversal", ["path traversal", "directory traversal", "arbitrary file"]),
        ("event_loop_blocking", ["event loop", "synchronous file", "writefilesync"]),
        ("command_injection", ["command injection", "shell injection"]),
        ("sql_injection", ["sql injection"]),
        ("raw_card", ["raw card", "card number", "payment card", "credit card"]),
        ("ssrf", ["ssrf", "server-side request forgery"]),
        ("hardcoded_secret", ["hardcoded secret", "hardcoded api", "hardcoded key"]),
    ]

    for category, patterns in title_first_patterns:
        if any(pattern in title for pattern in patterns):
            return category

    category_patterns = [
        ("language_mismatch", ["language mismatch", "wrong language", "not python", "not javascript", "selected language", "code is node.js", "code is javascript"]),
        ("event_loop_blocking", ["event loop blocking", "writefilesync", "synchronous file"]),
        ("plaintext_password", ["plain-text password", "plaintext password", "password comparison"]),
        ("weak_admin", ["admin key", "adminkey", "weak admin", "admin authorization"]),
        ("sql_injection", ["sql injection", "parameterized quer", "prepared statement", "unsafe query"]),
        ("raw_card", ["raw card", "card number", "card_number", "cardnumber", "credit card", "payment card", "cvv", "pci"]),
        ("path_traversal", ["path traversal", "arbitrary file", "unsafe filename", "file write", "directory traversal"]),
        ("command_injection", ["command injection", "child_process", "shell command", "shell execution", "exec(", "execsync", "system command"]),
        ("dynamic_execution", ["eval", "dynamic execution", "arbitrary code"]),
        ("hardcoded_secret", ["hardcoded", "api key", "secret", "token", "password", "admin_token", "payment_token", "jwt_secret", "admin_key"]),
        ("ssrf", ["ssrf", "server-side request forgery", "webhook url", "webhookurl", "user-controlled url", "callback url"]),
        ("type_mismatch", ["typeerror", "type error", "type mismatch", "non-numeric", "invalid data type"]),
        ("negative_payment", ["negative refund", "negative payment", "refund amount", "payment amount", "discount range"]),
        ("collection_none", ["fetchone", "none check", "empty collection", "index access", "list index", "array access", "rows[0]", "results[0]"]),
        ("request_exception", ["request exception", "network exception", "without exception handling", "raise_for_status", "axios.post call missing error"]),
        ("request_timeout", ["missing timeout", "without a timeout"]),
        ("db_connection", ["connection not closed", "db connection", "database connection", "delete_user"]),
        ("db_query_error", ["db.query", "database error", "query error", "ignored error"]),
        ("transaction_rollback", ["rollback", "transaction", "multi-step database"]),
        ("datetime_json", ["datetime", "json serial", "json.dumps", "created_at"]),
        ("missing_auth", ["missing authentication", "unauthenticated", "without authentication", "auth middleware"]),
        ("off_by_one", ["off-by-one", "<= items.length", "<= array.length", "out of bounds"]),
        ("info_leakage", ["err.message", "raw error", "error message returned", "information leakage"]),
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

    if {"sql_injection", "command_injection", "raw_card"}.issubset(categories):
        return 100
    if "sql_injection" in categories and ("raw_card" in categories or "hardcoded_secret" in categories):
        return 100
    if critical_count >= 2:
        return 95
    return None


def _normalize_review_response(
    review: ReviewResponse,
    selected_language: str | None = None,
    code: str | None = None,
) -> ReviewResponse:
    code_lower = (code or "").lower()
    normalized_bugs: list[BugFinding] = []

    for bug in review.bugs:
        if _is_placeholder_bug(bug):
            continue

        normalized_bug = BugFinding(
            title=bug.title,
            severity=_normalize_severity(bug.severity),
            explanation=bug.explanation,
            suggested_fix=bug.suggested_fix,
        )

        if _bug_category(normalized_bug) == "language_mismatch":
            if _selected_language_matches_code(selected_language, code_lower):
                continue
            normalized_bug = BugFinding(
                title="Language selection mismatch note",
                severity="Low",
                explanation=normalized_bug.explanation,
                suggested_fix="Choose the closest language before reviewing so syntax-specific checks are more accurate.",
            )

        normalized_bugs.append(normalized_bug)

    real_bugs = _dedupe_bug_findings(normalized_bugs)

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


def _user_input_variables(code_lower: str) -> set[str]:
    variables: set[str] = set()

    for destructured in re.findall(r"(?:const|let|var)?\s*\{([^}]+)\}\s*=\s*req\.(?:body|query|params)", code_lower):
        for name in destructured.split(","):
            variable = name.split(":")[0].strip().strip("{}[]")
            if variable:
                variables.add(variable)

    for match in re.finditer(
        r"(?:const|let|var)\s+(\w+)\s*=\s*req\.(?:body|query|params)(?:\.(\w+)|\[['\"](\w+)['\"]\])?",
        code_lower,
    ):
        variables.add(match.group(1))
        for group in match.groups()[1:]:
            if group:
                variables.add(group)

    variables.update(re.findall(r"req\.(?:body|query|params)\.(\w+)", code_lower))
    return variables


def _contains_user_input(text: str, user_vars: set[str]) -> bool:
    return "req.body" in text or "req.query" in text or "req.params" in text or any(re.search(rf"\b{re.escape(var)}\b", text) for var in user_vars)


def _has_hardcoded_secret(code_lower: str) -> bool:
    secret_name = r"(password|api[_-]?key|secret|token|jwt_secret|admin_key|adminkey|db_password|database_password|payment_secret|payment_token)"
    return bool(re.search(rf"\b[\w]*{secret_name}[\w]*\s*=", code_lower))


def _has_unsafe_sql_construction(code_lower: str) -> bool:
    has_sql = re.search(r"\b(select|insert|update|delete)\b", code_lower)
    has_concat_or_format = re.search(r"(\+\s*\w+|f[\"']|`[^`]*\$\{|\.format\s*\(|%\s*\(|req\.(?:body|query|params))", code_lower, re.DOTALL)
    return bool(has_sql and has_concat_or_format)


def _has_raw_card_handling(code_lower: str) -> bool:
    return bool(re.search(r"\b(card_number|cardnumber|credit_card|creditcard|pan|cvv)\b", code_lower))


def _has_unsafe_path_construction(code_lower: str) -> bool:
    user_vars = _user_input_variables(code_lower)
    has_file_write = re.search(r"\b(open|write_text|write_bytes)\s*\(", code_lower) or ".save(" in code_lower
    has_file_write = has_file_write or "fs.writefile" in code_lower or "fs.promises.writefile" in code_lower
    has_user_filename = re.search(r"\b(filename|file_name|path|upload|user_input|email)\b", code_lower) or bool({"filename", "file_name", "path", "email", "upload"} & user_vars)
    has_path_join = "os.path.join" in code_lower or "pathlib.path" in code_lower or "path.join" in code_lower or "/" in code_lower
    return bool(has_file_write and has_user_filename and has_path_join)


def _has_js_command_injection(code_lower: str) -> bool:
    user_vars = _user_input_variables(code_lower)
    has_shell_exec = bool(re.search(r"(child_process\.)?(exec|execsync)\s*\(|\bexec\s*\(|\bsystem\s*\(", code_lower))
    if not has_shell_exec and "require('child_process')" not in code_lower and 'require("child_process")' not in code_lower:
        return False

    command_assignments = re.findall(r"(?:const|let|var)\s+\w*command\w*\s*=\s*([^;\n]+)", code_lower)
    exec_calls = re.findall(r"(?:exec|execsync|system)\s*\(([^)\n]+)", code_lower)
    suspicious_parts = command_assignments + exec_calls
    return any("${" in part or "+" in part or _contains_user_input(part, user_vars) for part in suspicious_parts) or _contains_user_input(code_lower, user_vars)


def _has_axios_post(code_lower: str) -> bool:
    return "axios.post" in code_lower


def _has_js_ssrf(code_lower: str) -> bool:
    if not _has_axios_post(code_lower):
        return False

    user_vars = _user_input_variables(code_lower)
    for first_arg in re.findall(r"axios\.post\s*\(\s*([^,\n)]+)", code_lower):
        if _contains_user_input(first_arg, user_vars):
            return True
        if re.search(r"\b(webhookurl|callbackurl|targeturl|url)\b", first_arg) and _contains_user_input(code_lower, user_vars):
            return True

    return False


def _has_axios_missing_error_handling(code_lower: str) -> bool:
    return _has_axios_post(code_lower) and "try" not in code_lower and ".catch(" not in code_lower


def _has_js_plaintext_password_check(code_lower: str) -> bool:
    has_login = "login" in code_lower or "password" in code_lower
    has_sql_password = bool(re.search(r"where[^;\n]*(password|pwd)[^;\n]*(\+|\$\{|req\.)", code_lower))
    has_direct_compare = bool(re.search(r"(user|row|rows\[0\]|result)\.password\s*(===|==)\s*password", code_lower))
    return has_login and (has_sql_password or has_direct_compare)


def _has_missing_express_auth(code_lower: str) -> bool:
    route_pattern = r"(app|router)\.(post|put|patch|delete)\s*\(\s*['\"][^'\"]*(admin|refund|payment|order|delete|user)"
    if not re.search(route_pattern, code_lower):
        return False
    return not re.search(r"(auth|authenticate|authorize|requireauth|verifytoken|jwt\.verify)", code_lower)


def _has_weak_admin_key_check(code_lower: str) -> bool:
    return bool(re.search(r"\b(admin_key|adminkey|admin-key|x-admin-key)\b", code_lower))


def _has_js_off_by_one_loop(code_lower: str) -> bool:
    return bool(re.search(r"for\s*\([^;]+;\s*\w+\s*<=\s*[\w.]+\.length\s*;", code_lower))


def _has_unchecked_js_first_row(code_lower: str) -> bool:
    if not re.search(r"\b(rows|results|result)\s*\[\s*0\s*\]", code_lower):
        return False
    has_guard = re.search(r"if\s*\(\s*(!\s*(rows|results|result)|(rows|results|result)\.length\s*(===|==|>|>=)\s*0)", code_lower)
    return not bool(has_guard)


def _has_db_query_without_error_handling(code_lower: str) -> bool:
    if "db.query" not in code_lower:
        return False
    return "err" not in code_lower and "error" not in code_lower or not re.search(r"if\s*\(\s*(err|error)|catch\s*\(", code_lower)


def _has_raw_error_message_return(code_lower: str) -> bool:
    return bool(re.search(r"res\.(status\([^)]*\)\.)?(json|send)\s*\([^;\n]*(err|error)\.message", code_lower))


def _has_sync_file_write_in_express_route(code_lower: str) -> bool:
    has_route = bool(re.search(r"(app|router)\.(get|post|put|patch|delete)\s*\(", code_lower))
    return has_route and "fs.writefilesync" in code_lower


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
    python_write_count = len(re.findall(r"\.execute\s*\(\s*[f]?[\"']\s*(insert|update|delete)\b", code_lower))
    js_write_count = len(re.findall(r"\b(insert|update|delete)\b", code_lower)) if "db.query" in code_lower else 0
    has_commit = ".commit(" in code_lower or "commit(" in code_lower
    return (python_write_count >= 2 and has_commit or js_write_count >= 2) and "rollback" not in code_lower


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
    has_negative_path = re.search(
        r"(<\s*0|-\s*amount|amount\s*=\s*-|refund_amount\s*=\s*-|refundamount\s*=\s*-|payment_amount\s*=\s*-|paymentamount\s*=\s*-)",
        code_lower,
    )
    return bool(has_payment_context and has_negative_path)


def _merge_safety_checks(
    ai_review: ReviewResponse,
    safety_review: ReviewResponse,
    selected_language: str | None = None,
    code: str | None = None,
) -> ReviewResponse:
    ai_review = _normalize_review_response(ai_review, selected_language, code)
    safety_review = _normalize_review_response(safety_review, selected_language, code)
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
    ), selected_language, code)


def _fallback_review(payload: ReviewRequest, reason: str | None = None) -> ReviewResponse:
    """Local rules keep the demo usable when no AI API key is configured."""
    code = payload.code
    language = payload.language.lower()
    code_lower = code.lower()
    is_javascript_review = _is_javascript_language(language) or _code_looks_like_node_js(code_lower)
    bugs: list[BugFinding] = []
    improvements: list[str] = []
    test_cases: list[str] = []
    risk_score = 15

    if _has_hardcoded_secret(code_lower):
        is_payment_or_admin_secret = bool(re.search(r"(admin|payment|stripe|paypal|jwt|database|db|secret|token)", code_lower))
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

    if is_javascript_review and _has_js_command_injection(code_lower):
        bugs.append(
            BugFinding(
                title="Command injection from child_process.exec",
                severity="Critical",
                explanation="The code builds a shell command from user-controlled data before passing it to child_process.exec or a similar shell execution API.",
                suggested_fix="Avoid shell execution for user input. Use safe library calls or spawn/execFile with a fixed command and validated arguments.",
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

    if is_javascript_review and _has_js_ssrf(code_lower):
        bugs.append(
            BugFinding(
                title="SSRF risk from user-controlled webhook URL",
                severity="High",
                explanation="The code sends axios.post to a URL that appears to come from request body, query, or params, allowing attackers to make the server call internal or unexpected URLs.",
                suggested_fix="Allow-list trusted webhook hosts, reject private/internal IP ranges, and avoid sending server-side requests to arbitrary user-provided URLs.",
            )
        )
        risk_score += 30

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

    if is_javascript_review and _has_axios_post(code_lower) and "timeout" not in code_lower:
        bugs.append(
            BugFinding(
                title="axios.post call missing timeout",
                severity="Medium",
                explanation="The code sends an HTTP request without a timeout, so a slow external service can hang the route or background task.",
                suggested_fix="Pass a timeout option to axios.post and handle timeout failures explicitly.",
            )
        )
        risk_score += 15

    if is_javascript_review and _has_axios_missing_error_handling(code_lower):
        bugs.append(
            BugFinding(
                title="axios.post call missing error handling",
                severity="Medium",
                explanation="The code sends an HTTP request without try/catch or a .catch handler for network errors and non-success responses.",
                suggested_fix="Wrap external requests in try/catch, handle AxiosError cases, and return a controlled response.",
            )
        )
        risk_score += 15

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

    if is_javascript_review and _has_js_plaintext_password_check(code_lower):
        bugs.append(
            BugFinding(
                title="Plain-text password comparison in login flow",
                severity="High",
                explanation="The code appears to compare passwords directly or include a password in SQL login logic instead of using password hashing.",
                suggested_fix="Store salted password hashes and verify with bcrypt, argon2, or another password hashing library.",
            )
        )
        risk_score += 25

    if is_javascript_review and _has_missing_express_auth(code_lower):
        bugs.append(
            BugFinding(
                title="Missing authentication on sensitive Express route",
                severity="High",
                explanation="A sensitive route such as payment, refund, admin, order, or delete appears to run without authentication middleware.",
                suggested_fix="Require authentication middleware and server-side authorization checks before executing sensitive actions.",
            )
        )
        risk_score += 25

    if is_javascript_review and _has_weak_admin_key_check(code_lower):
        bugs.append(
            BugFinding(
                title="Weak adminKey-only authorization",
                severity="High",
                explanation="The code appears to authorize admin actions using only a shared admin key, which is easy to leak and hard to audit.",
                suggested_fix="Use authenticated users, roles/permissions, key rotation, and audit logging instead of a single shared admin key.",
            )
        )
        risk_score += 25

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

    if is_javascript_review and _has_db_query_without_error_handling(code_lower):
        bugs.append(
            BugFinding(
                title="db.query call missing error handling",
                severity="Medium",
                explanation="The code uses db.query without checking callback errors or handling query failures.",
                suggested_fix="Check the error argument in every callback or use async database calls with try/catch.",
            )
        )
        risk_score += 15

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

    if is_javascript_review and _has_unchecked_js_first_row(code_lower):
        bugs.append(
            BugFinding(
                title="rows[0] used without checking query results",
                severity="High",
                explanation="The code reads rows[0] or results[0] without checking whether the query returned any rows.",
                suggested_fix="Check that the result array exists and has at least one item before reading index 0.",
            )
        )
        risk_score += 20

    if _has_negative_payment_amount(code_lower):
        bugs.append(
            BugFinding(
                title="Negative refund/payment amount business logic risk",
                severity="High",
                explanation="The code appears to allow or create a negative refund, payment, charge, or amount value, which can break payment and accounting logic.",
                suggested_fix="Validate payment and refund amounts before processing, reject negative values, and define explicit business rules for credits.",
            )
        )
        risk_score += 30

    if is_javascript_review and _has_js_off_by_one_loop(code_lower):
        bugs.append(
            BugFinding(
                title="Off-by-one loop can read past array length",
                severity="High",
                explanation="A for loop uses <= items.length, which will access one index past the end of the array.",
                suggested_fix="Use i < items.length and add tests for empty and single-item arrays.",
            )
        )
        risk_score += 20

    if is_javascript_review and _has_raw_error_message_return(code_lower):
        bugs.append(
            BugFinding(
                title="Raw error message returned to client",
                severity="Medium",
                explanation="The route appears to return err.message or error.message directly, which can leak implementation details to users.",
                suggested_fix="Log detailed errors server-side and return a generic client-safe error message.",
            )
        )
        risk_score += 15

    if is_javascript_review and _has_sync_file_write_in_express_route(code_lower):
        bugs.append(
            BugFinding(
                title="Synchronous file write inside Express route",
                severity="Medium",
                explanation="fs.writeFileSync blocks the Node.js event loop when used inside a request handler.",
                suggested_fix="Use async fs.promises.writeFile or move blocking work to a background job.",
            )
        )
        risk_score += 15

    has_division = _has_probable_division(code)
    code_without_strings = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "\"\"", code)
    has_arithmetic = bool(re.search(r"[\w)\]]+\s*[-*/]\s*[\w(\[]+", code_without_strings))
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

    if language in ["python", "py"] and has_arithmetic and calls_function_with_string:
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

    if is_javascript_review and ("innerHTML" in code or "dangerouslySetInnerHTML" in code):
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

    if is_javascript_review and "var " in code:
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

    if _has_hardcoded_secret(code_lower):
        test_cases.append("Test that secrets are loaded from environment variables and never returned in logs.")

    if _has_unsafe_sql_construction(code_lower):
        test_cases.append("Test malicious SQL input such as quoted OR conditions and verify parameterized queries are used.")

    if _has_raw_card_handling(code_lower):
        test_cases.append("Test payment flow with tokenized card data and verify raw card numbers are never stored or logged.")

    if _has_unsafe_path_construction(code_lower):
        test_cases.append("Test filenames containing ../ path traversal and verify writes stay inside the allowed upload directory.")

    if is_javascript_review and _has_js_command_injection(code_lower):
        test_cases.append("Test shell command inputs containing separators such as ; and && and verify they are rejected.")

    if is_javascript_review and _has_js_ssrf(code_lower):
        test_cases.append("Test webhook URLs pointing to localhost and private IP ranges and verify they are blocked.")

    if is_javascript_review and _has_js_plaintext_password_check(code_lower):
        test_cases.append("Test login with a stored password hash and verify plain-text password comparison is not used.")

    if is_javascript_review and _has_missing_express_auth(code_lower):
        test_cases.append("Test sensitive routes without a valid token and verify they return 401 or 403.")

    if is_javascript_review and _has_weak_admin_key_check(code_lower):
        test_cases.append("Test admin actions with a leaked or missing admin key and verify role-based authorization is required.")

    if is_javascript_review and _has_js_off_by_one_loop(code_lower):
        test_cases.append("Test array loops with empty and single-item arrays to verify no out-of-bounds access occurs.")

    if is_javascript_review and _has_unchecked_js_first_row(code_lower):
        test_cases.append("Test database queries that return zero rows and verify rows[0] is not read before checking length.")

    if is_javascript_review and _has_db_query_without_error_handling(code_lower):
        test_cases.append("Test database query errors and verify the route returns a controlled failure response.")

    if is_javascript_review and _has_raw_error_message_return(code_lower):
        test_cases.append("Test internal exceptions and verify detailed error messages are not returned to clients.")

    if is_javascript_review and _has_sync_file_write_in_express_route(code_lower):
        test_cases.append("Test concurrent upload or export requests and verify file writes do not block the event loop.")

    if _has_requests_post(code_lower):
        test_cases.append("Test external API timeout, connection error, and non-2xx response handling.")

    if is_javascript_review and _has_axios_post(code_lower):
        test_cases.append("Test axios timeout, network failure, and non-2xx response handling.")

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
    ), payload.language, payload.code)


def _review_from_ai_json(parsed: Dict[str, Any], payload: ReviewRequest | None = None) -> ReviewResponse:
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
    ), payload.language if payload else None, payload.code if payload else None)


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
        _apply_similar_cache_metadata(fallback_review, similar_cached_review)
        _store_review(payload, fallback_review)
        return fallback_review

    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "90" if is_ollama else "60"))
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
This is defensive code review for a portfolio app. The user is asking to find and fix vulnerabilities,
not to exploit them. Do not provide executable attack steps.
Risk score must match issue severity:
- Critical issue: risk_score must be at least 80.
- High issue: risk_score must be at least 50.
- Medium issue: risk_score must be at least 30.
- Only Low issues: risk_score should usually be between 10 and 25.
If multiple Critical issues exist, risk_score should usually be 95-100.
If SQL injection appears with command injection and raw card handling, risk_score should be 100.
If SQL injection appears with payment secrets or raw card handling, risk_score should be 100.
Do not give Low risk when Critical or High issues exist.
Mark issues as Critical only for crashes, data loss, security risks, or serious runtime failures.
Mention business logic issues separately from runtime bugs.
Do not mark a Node.js/Express codebase as a language mismatch when JavaScript is selected.
If the selected language appears wrong, mention it only as a Low note, not a Critical bug.
If the selected language and pasted code do not match, still review the actual code shown.
Look specifically for payment/card handling, SQL injection, path traversal, missing request timeouts,
missing request exception handling, missing DB rollback, unclosed DB connections, fetchone None checks,
datetime JSON serialization, and negative refund/payment amounts when relevant.
For JavaScript/Node.js/Express, look specifically for child_process.exec command injection,
SSRF from user-controlled webhook URLs, axios timeout/error handling, fs path traversal,
hardcoded JWT/admin/database/payment secrets, plain-text password checks, missing auth middleware,
weak adminKey-only checks, off-by-one loops, rows[0] without length checks, db.query ignored errors,
raw err.message responses, and fs.writeFileSync inside routes.
child_process.exec is asynchronous; the security issue is shell command injection, not synchronous blocking.
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

    def completion_options_for(user_prompt: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        if is_ollama:
            options["extra_body"] = {
                "options": {
                    "num_ctx": _int_env("OLLAMA_NUM_CTX", 512, 256, 8192),
                    "num_predict": _int_env("OLLAMA_NUM_PREDICT", 512, 128, 8192),
                }
            }
        return options

    async def run_ai_review(user_prompt: str) -> ReviewResponse:
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            **completion_options_for(user_prompt),
        )

        content = completion.choices[0].message.content or "{}"
        parsed = _safe_json_loads(content)
        return _review_from_ai_json(parsed, payload)

    safety_review = _fallback_review(payload)

    try:
        try:
            ai_review = await run_ai_review(_ai_user_prompt(payload, similar_cached_review))
        except Exception:
            try:
                ai_review = await run_ai_review(_ai_user_prompt(payload, similar_cached_review, retry_safe_mode=True))
            except Exception:
                ai_review = await run_ai_review(_ai_local_findings_prompt(payload, safety_review))
                ai_review.review_source = "ai_from_local_findings"
        review = _merge_safety_checks(ai_review, safety_review, payload.language, payload.code)
        if ai_review.review_source == "ai_from_local_findings":
            review.review_source = "ai_from_local_findings"
        if similar_cached_review:
            review.review_source = "ai_with_cache_context"
            review.similarity_used = similar_cached_review["similarity"]
        _store_review(payload, review)
        return review
    except Exception:
        fallback_review = _fallback_after_ai_error(payload, similar_cached_review)
        _store_review(payload, fallback_review)
        return fallback_review
