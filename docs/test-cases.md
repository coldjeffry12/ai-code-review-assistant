# Review Feature Test Cases

These test cases show example buggy inputs that can be pasted into the AI Code Review Assistant.

AI review is not a 100% guarantee. The app gives possible issues, risk signals, improvement ideas, and test suggestions, but the developer must still run, debug, and test the fixed code manually. The app does not execute user code for safety.

Test run notes:

- Manual API test date: May 22, 2026
- Endpoint tested: `POST http://localhost:8000/api/review`
- Mode tested: fallback mode with `OPENAI_API_KEY` empty
- Backend status: `GET /health` returned `{"status":"ok"}`
- Frontend status: `http://localhost:5173` returned HTTP 200

Live deployment language coverage:

- Manual API test date: May 23, 2026
- Endpoint tested: `POST https://ai-code-review-backend-c17u.onrender.com/api/review`
- Health check: `GET https://ai-code-review-backend-c17u.onrender.com/health` returned `{"status":"ok"}`
- Frontend language options tested: Python, JavaScript, TypeScript, React, Java, C++, SQL
- Result: all 7 language examples returned review output and detected the expected issue.
- Mode during final repeated run: fallback review. The free AI provider was unavailable or returned invalid JSON during the repeated test run, so the backend used the fallback review engine instead of failing.

| Language | Live test case | Actual live result summary | Risk score | AI used | Status |
| --- | --- | --- | --- | --- | --- |
| Python | Division by zero | Detected `Potential division by zero`; suggested denominator validation and arithmetic edge case tests. | 40/100 | No | Pass |
| JavaScript | `var` usage and empty array access | Detected `Possible empty collection access`; suggested `let` or `const` instead of `var`. | 35/100 | No | Pass |
| TypeScript | Missing undefined check | Detected `Possible missing null or undefined check`; suggested a guard clause or optional chaining. | 30/100 | No | Pass |
| React | Unsafe HTML rendering | Detected `Possible unsafe HTML injection`; suggested safer rendering or sanitization. | 40/100 | No | Pass |
| Java | Null pointer risk | Detected `Possible null pointer risk`; suggested validating the object before dereferencing it. | 40/100 | No | Pass |
| C++ | Null pointer method call and division by zero | Detected `Potential division by zero` and `Possible null pointer risk`. | 65/100 | No | Pass |
| SQL | Unsafe string concatenation | Detected `Possible SQL injection`; suggested parameterized queries. | 50/100 | No | Pass |

Free AI mode note:

The deployed backend is configured for optional free AI through an OpenAI-compatible provider. During testing, individual requests can use AI when the provider responds correctly, but repeated calls may be throttled or may return invalid JSON. The fallback engine is intentionally kept so the live app remains usable without paid AI access.

## How to Use These Test Cases

1. Start the backend and frontend.
2. Open `http://localhost:5173`.
3. Paste one buggy input into the code editor.
4. Select the matching language.
5. Click **Review Code**.
6. Compare the output with the actual tested result below.

## Test Case 1: Python Division by Zero

Language: Python

Buggy input code:

```python
def divide_numbers(a, b):
    return a / b

print(divide_numbers(10, 0))
```

Expected review result:

- Should flag possible division by zero.
- Should recommend validating the denominator before division.
- Should suggest a test case with zero as input.

Actual review result:

```text
Mode: Fallback review
Risk score: 40/100
Detected issue: Potential division by zero
Improvement suggestions:
- Replace print statements with structured logging for production code.
- Add explicit validation around arithmetic edge cases such as zero, null, or missing values.
Relevant test idea: Test division or arithmetic behavior with zero and negative values.
```

Status: Pass

## Test Case 2: Python Hardcoded Password or API Key

Language: Python

Buggy input code:

```python
API_KEY = "[redacted-demo-key]"
password = "[redacted-demo-password]"

def connect():
    return f"Using {API_KEY} and {password}"
```

Expected review result:

- Should flag possible hardcoded secrets.
- Should recommend using environment variables.
- Should warn against committing secrets to GitHub.

Actual review result:

```text
Mode: Fallback review
Risk score: 40/100
Detected issue: Possible hardcoded secret
Suggested fix: Move secrets to environment variables and never commit them to GitHub.
Relevant test idea: Test that secrets are loaded from environment variables and never returned in logs.
```

Status: Pass

## Test Case 3: Python List Index Error

Language: Python

Buggy input code:

```python
def first_item(items):
    return items[0]

print(first_item([]))
```

Expected review result:

- Should identify that the code can fail when the list is empty.
- Should recommend checking the list length before accessing index `0`.
- Should suggest test cases for empty and non-empty lists.

Actual review result:

```text
Mode: Fallback review
Risk score: 35/100
Detected issue: Possible empty collection access
Suggested fix: Check that the collection has enough items before reading by index.
Improvement suggestion: Replace print statements with structured logging for production code.
Relevant test idea: Test empty or missing input.
```

Status: Pass

## Test Case 4: JavaScript var Usage

Language: JavaScript

Buggy input code:

```javascript
function totalPrices(prices) {
  var total = 0;

  for (var i = 0; i < prices.length; i++) {
    total += prices[i];
  }

  return total;
}
```

Expected review result:

- Should recommend `let` or `const` instead of `var`.
- Should mention block scoping and safer variable usage.
- May suggest tests for empty, single-item, and multi-item arrays.

Actual review result:

```text
Mode: Fallback review
Risk score: 15/100
Detected issue: No critical issue detected by fallback engine
Improvement suggestion: Use let or const instead of var to avoid function-scope issues.
Relevant test ideas: Test normal valid input, empty or missing input, invalid data type input, boundary values, and error-handling behavior.
```

Status: Pass

## Test Case 5: JavaScript Empty Array Issue

Language: JavaScript

Buggy input code:

```javascript
function getFirstUserName(users) {
  return users[0].name;
}

console.log(getFirstUserName([]));
```

Expected review result:

- Should identify risk when the array is empty.
- Should recommend checking `users.length` before reading `users[0]`.
- Should suggest test cases for empty and populated arrays.

Actual review result:

```text
Mode: Fallback review
Risk score: 35/100
Detected issue: Possible empty collection access
Suggested fix: Check that the collection has enough items before reading by index.
Relevant test idea: Test empty or missing input.
```

Status: Pass

## Test Case 6: TypeScript Missing Null or Undefined Check

Language: TypeScript

Buggy input code:

```typescript
type User = {
  id: number;
  name: string;
};

function findUserName(user?: User) {
  return user.name.toUpperCase();
}
```

Expected review result:

- Should identify that `user` may be `undefined`.
- Should recommend a guard clause, optional chaining, or a required parameter.
- Should suggest tests for defined and undefined user values.

Actual review result:

```text
Mode: Fallback review
Risk score: 30/100
Detected issue: Possible missing null or undefined check
Suggested fix: Add a guard clause, optional chaining, or make the parameter required.
Relevant test ideas: Test normal valid input, empty or missing input, invalid data type input, boundary values, and error-handling behavior.
```

Status: Pass

## Test Case 7: SQL Unsafe String Concatenation

Language: SQL

Buggy input code:

```sql
query = "SELECT * FROM users WHERE email = '" + user_email + "'"
```

Expected review result:

- Should flag possible SQL injection.
- Should recommend parameterized queries.
- Should suggest tests with normal input and malicious-looking input.

Actual review result:

```text
Mode: Fallback review
Risk score: 50/100
Detected issue: Possible SQL injection
Suggested fix: Use parameterized queries instead of building SQL strings from user input.
Relevant test ideas: Test normal valid input, invalid data type input, boundary values, and error-handling behavior.
```

Status: Pass

## Test Case 8: Java Null Pointer Risk

Language: Java

Buggy input code:

```java
class Example {
    static int getNameLength(String name) {
        return name.length();
    }

    public static void main(String[] args) {
        String value = null;
        System.out.println(getNameLength(value));
    }
}
```

Expected review result:

- Should identify possible null pointer risk.
- Should recommend validating `name` before calling `.length()`.
- Should suggest tests for null, empty string, and normal string values.

Actual review result:

```text
Mode: Fallback review
Risk score: 40/100
Detected issue: Possible null pointer risk
Suggested fix: Validate the object before dereferencing it or avoid passing null into the function.
Relevant test ideas: Test normal valid input, empty or missing input, invalid data type input, boundary values, and error-handling behavior.
```

Status: Pass

## Summary

All 8 documented manual review cases passed in fallback mode:

- Python division by zero: Pass
- Python hardcoded password or API key: Pass
- Python list index error: Pass
- JavaScript var usage: Pass
- JavaScript empty array issue: Pass
- TypeScript missing null or undefined check: Pass
- SQL unsafe string concatenation: Pass
- Java null pointer risk: Pass

## Notes for Portfolio Reviewers

- Fallback mode is intentionally simple and rule-based.
- Real AI mode can provide deeper review, but still needs human verification.
- The app does not execute pasted code.
- Suggested fixed code must be reviewed and tested manually before use.
