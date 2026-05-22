# AI-Assisted Development Workflow

This project is meant to show practical AI-native development, not only a simple AI API call.

## How AI Was Used

AI assistance was used for:

1. Turning the project idea into a full-stack architecture.
2. Drafting the FastAPI route and Pydantic schemas.
3. Creating the first React UI structure.
4. Improving the prompt used for code review.
5. Identifying weak fallback rules.
6. Making JSON parsing more defensive.
7. Improving error handling between frontend and backend.
8. Writing setup, deployment, and portfolio documentation.

## Developer Responsibilities

The developer still had to verify:

- The backend starts correctly.
- `/api/review` returns a valid response.
- The app works with no `OPENAI_API_KEY`.
- The frontend builds successfully.
- CORS allows the frontend to call the backend.
- No API key is committed to the repository.
- The README commands work for Windows users.

## Example Prompt

```text
Build a full-stack AI Code Review Assistant using React, TypeScript, and Python FastAPI.
The app should allow users to paste code, select a programming language, and receive bugs,
risk score, improvements, test cases, and optional fixed code.
Keep the app useful without an API key by adding fallback review logic.
```

## Lessons

AI can accelerate development, but it does not remove engineering responsibility. The strongest portfolio version of this project includes working code, clear documentation, graceful fallback behavior, and evidence that the developer tested the system.
