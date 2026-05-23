import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { reviewCode, type ReviewResponse } from './api'

const defaultCode = `def divide_numbers(a, b):
    return a / b

print(divide_numbers(10, 0))`

const languageOptions = ['Python', 'JavaScript', 'TypeScript', 'React', 'Java', 'C++', 'SQL']

type Theme = 'light' | 'dark'

function getInitialTheme(): Theme {
  const savedTheme = window.localStorage.getItem('theme')
  if (savedTheme === 'light' || savedTheme === 'dark') {
    return savedTheme
  }

  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getRiskMeta(score: number) {
  if (score >= 76) {
    return { label: 'Critical risk', className: 'riskCritical' }
  }
  if (score >= 51) {
    return { label: 'High risk', className: 'riskHigh' }
  }
  if (score >= 26) {
    return { label: 'Medium risk', className: 'riskMedium' }
  }
  return { label: 'Low risk', className: 'riskLow' }
}

function getReviewSourceLabel(result: ReviewResponse) {
  if (result.cache_hit) {
    return 'Cached review'
  }
  if (result.review_source.includes('cache_context')) {
    return result.used_ai ? 'AI review + cache context' : 'Fallback review + cache context'
  }
  return result.used_ai ? 'AI review' : 'Fallback review'
}

function getLanguageRows(result: ReviewResponse) {
  return [
    ['Selected', result.selected_language],
    ['Detected', result.detected_language],
    ['Reviewed as', result.reviewed_language],
  ].filter((row): row is [string, string] => Boolean(row[1]))
}

function App() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [language, setLanguage] = useState('Python')
  const [focus, setFocus] = useState('bugs, security, performance, readability')
  const [code, setCode] = useState(defaultCode)
  const [result, setResult] = useState<ReviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const riskMeta = useMemo(() => (result ? getRiskMeta(result.risk_score) : null), [result])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('theme', theme)
  }, [theme])

  async function handleReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLoading(true)
    setError('')
    setResult(null)

    try {
      const data = await reviewCode({ language, code, focus })
      setResult(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="page">
      <header className="appHeader">
        <div>
          <p className="eyebrow">Applied AI Portfolio Project</p>
          <h1>AI Code Review Assistant</h1>
          <p className="subtitle">Bug finder, risk scoring, improvement notes, and test ideas for pasted code.</p>
        </div>
        <div className="headerActions">
          <button
            className="themeToggle"
            type="button"
            aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
            aria-pressed={theme === 'dark'}
            onClick={() => setTheme((currentTheme) => (currentTheme === 'dark' ? 'light' : 'dark'))}
          >
            <span className="themeToggleTrack" aria-hidden="true">
              <span className="themeToggleThumb" />
            </span>
            <span>{theme === 'dark' ? 'Dark' : 'Light'}</span>
          </button>
          <div className="statusBadge">OpenAI optional</div>
        </div>
      </header>

      <section className="layout">
        <form className="panel inputPanel" onSubmit={handleReview}>
          <div className="panelHeader">
            <h2>Review Input</h2>
            <span>{code.trim().split(/\r?\n/).length} lines</span>
          </div>

          <label htmlFor="language">Language</label>
          <select id="language" value={language} onChange={(e) => setLanguage(e.target.value)}>
            {languageOptions.map((option) => (
              <option key={option}>{option}</option>
            ))}
          </select>

          <label htmlFor="focus">Review Focus</label>
          <input
            id="focus"
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            placeholder="bugs, security, performance"
          />

          <label htmlFor="code">Code</label>
          <textarea
            id="code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            rows={18}
            spellCheck={false}
          />

          <div className="buttonRow">
            <button className="primaryButton" type="submit" disabled={loading || code.trim().length < 5}>
              {loading ? 'Reviewing...' : 'Review Code'}
            </button>
            <button className="secondaryButton" type="button" onClick={() => setCode(defaultCode)} disabled={loading}>
              Reset Sample
            </button>
          </div>

          {error && <p className="error" role="alert">{error}</p>}
        </form>

        <section className="panel resultPanel">
          <div className="panelHeader">
            <h2>Review Result</h2>
            {result && <span>{getReviewSourceLabel(result)}</span>}
          </div>

          {!result && !loading && (
            <div className="emptyState">
              <p>Run a review to see the analysis.</p>
            </div>
          )}

          {loading && (
            <div className="emptyState">
              <p>Running review...</p>
              <span>Free AI review can take 10-30 seconds. Repeated identical code returns from cache.</span>
            </div>
          )}

          {result && riskMeta && (
            <div className="result">
              <div className={`scoreBox ${riskMeta.className}`}>
                <span>Risk Score</span>
                <strong>{result.risk_score}/100</strong>
                <em>{riskMeta.label}</em>
              </div>

              <section className="resultSection">
                <h3>Summary</h3>
                {getLanguageRows(result).length > 0 && (
                  <dl className="languageMeta" aria-label="Review language details">
                    {getLanguageRows(result).map(([label, value]) => (
                      <div key={label}>
                        <dt>{label}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                <p>{result.summary}</p>
                {result.cache_hit && (
                  <p className="resultNote">Returned instantly from cache because the language, focus, and code matched a previous review.</p>
                )}
                {!result.cache_hit && result.similarity_used && (
                  <p className="resultNote">
                    Used a previous similar review as context ({Math.round(result.similarity_used * 100)}% match), then generated a fresh result.
                  </p>
                )}
              </section>

              <section className="resultSection">
                <h3>Possible Bugs</h3>
                {result.bugs.length === 0 ? (
                  <p>No concrete bug findings were returned. Review the improvement notes and test ideas below.</p>
                ) : (
                  <div className="findingList">
                    {result.bugs.map((bug, index) => {
                      const severityClass = bug.severity.toLowerCase().replace(/[^a-z]/g, '')
                      return (
                        <article className="finding" key={`${bug.title}-${index}`}>
                          <div className="findingHeader">
                            <strong>{bug.title}</strong>
                            <span className={`severity severity-${severityClass}`}>{bug.severity}</span>
                          </div>
                          <p>{bug.explanation}</p>
                          <p><b>Fix:</b> {bug.suggested_fix}</p>
                        </article>
                      )
                    })}
                  </div>
                )}
              </section>

              <section className="resultSection">
                <h3>Improvements</h3>
                <ul>
                  {result.improvements.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </section>

              <section className="resultSection">
                <h3>Test Case Ideas</h3>
                <ul>
                  {result.test_cases.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              </section>

              {result.fixed_code && (
                <section className="resultSection">
                  <h3>Suggested Fixed Code</h3>
                  <pre>{result.fixed_code}</pre>
                </section>
              )}
            </div>
          )}
        </section>
      </section>
    </main>
  )
}

export default App
