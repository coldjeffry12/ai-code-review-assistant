export type BugFinding = {
  title: string
  severity: string
  explanation: string
  suggested_fix: string
}

export type ReviewResponse = {
  summary: string
  risk_score: number
  bugs: BugFinding[]
  improvements: string[]
  test_cases: string[]
  fixed_code?: string | null
  used_ai: boolean
}

export type ReviewRequest = {
  language: string
  code: string
  focus: string
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

async function getErrorMessage(response: Response) {
  try {
    const body = await response.clone().json()
    if (typeof body.detail === 'string') {
      return body.detail
    }
    if (Array.isArray(body.detail)) {
      return body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(' ')
    }
  } catch {
    const text = await response.text()
    if (text) {
      return text
    }
  }

  return 'Failed to review code. Check that the backend is running on port 8000.'
}

export async function reviewCode(payload: ReviewRequest): Promise<ReviewResponse> {
  const response = await fetch(`${API_BASE_URL}/api/review`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    throw new Error(await getErrorMessage(response))
  }

  return response.json()
}
