import { useCallback, useState, useRef, useEffect } from 'react'
import './index.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
const TOKEN_KEY = 'certifake_token'
const EMAIL_KEY = 'certifake_email'

function AuthPanel({ token, email, onAuthenticated, onLogout }) {
  const [mode, setMode] = useState('login') // 'login' | 'register'
  const [formEmail, setFormEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (token) {
    return (
      <div className="card panel">
        <h2>Account</h2>
        <div className="evidence-list">
          <div><span>Signed in as</span><strong>{email}</strong></div>
        </div>
        <button className="btn btn-secondary mt-4" onClick={onLogout}>Log out</button>
      </div>
    )
  }

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const endpoint = mode === 'login' ? '/auth/login' : '/auth/register'
      const res = await fetch(`${API_URL}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: formEmail, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Authentication failed')
      onAuthenticated(data.access_token, formEmail)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card panel">
      <h2>{mode === 'login' ? 'Log in' : 'Create account'}</h2>
      <form onSubmit={submit} className="grid">
        <input
          type="email"
          placeholder="Email"
          value={formEmail}
          onChange={e => setFormEmail(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="Password (min 8 characters)"
          value={password}
          onChange={e => setPassword(e.target.value)}
          minLength={8}
          required
        />
        {error && <div className="pill bad">{error}</div>}
        <button className="btn" type="submit" disabled={busy}>
          {busy ? 'Please wait...' : mode === 'login' ? 'Log in' : 'Sign up'}
        </button>
      </form>
      <button
        className="btn btn-secondary mt-4"
        onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}
      >
        {mode === 'login' ? "Need an account? Sign up" : 'Already have an account? Log in'}
      </button>
    </div>
  )
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [email, setEmail] = useState(() => localStorage.getItem(EMAIL_KEY) || '')
  const [file, setFile] = useState(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const fileInputRef = useRef(null)

  useEffect(() => {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
    if (email) localStorage.setItem(EMAIL_KEY, email)
    else localStorage.removeItem(EMAIL_KEY)
  }, [token, email])

  // Memoized on `token` because the heatmap/report useEffect below lists this
  // in its dependency array: an inline closure would get a new identity every
  // render, which exhaustive-deps would then (correctly) flag as an effect
  // that re-runs forever. Its only input is `token`, so its identity changes
  // exactly when the effect should re-run anyway.
  const authHeaders = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  )

  const handleLogout = () => {
    setToken('')
    setEmail('')
    setResult(null)
  }

  const handleAnalyze = async () => {
    if (!token) return setError('Log in first to analyze a certificate')
    if (!file) return setError('Choose a certificate file first')

    setAnalyzing(true)
    setError('')
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: 'POST',
        headers: authHeaders(),
        body: formData,
      })
      const data = await res.json()
      if (res.status === 401) {
        handleLogout()
        throw new Error('Session expired, please log in again')
      }
      if (!res.ok) throw new Error(data.detail || 'Analysis failed')

      const analysisId = data.analysis_id

      const poll = setInterval(async () => {
        try {
          const statusRes = await fetch(`${API_URL}/status/${analysisId}`, { headers: authHeaders() })
          const statusData = await statusRes.json()

          if (statusData.status === 'completed') {
            clearInterval(poll)
            setResult({ ...statusData, analysis_id: analysisId })
            setAnalyzing(false)
          } else if (statusData.status === 'failed') {
            clearInterval(poll)
            setError('Analysis failed during distributed processing')
            setAnalyzing(false)
          }
        } catch (e) {
          clearInterval(poll)
          setError(e.message)
          setAnalyzing(false)
        }
      }, 2000)
    } catch (err) {
      setError(err.message)
      setAnalyzing(false)
    }
  }

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0])
      setResult(null)
    }
  }

  const getPillClass = (verdict) => {
    if (!verdict) return 'idle'
    if (verdict.includes('Genuine')) return 'good'
    if (verdict.includes('Review')) return 'warn'
    if (verdict.includes('Fake')) return 'bad'
    return 'idle'
  }

  const getRingColor = (score) => {
    if (!score && score !== 0) return 'var(--accent)'
    if (score >= 80) return 'var(--good)'
    if (score >= 55) return 'var(--warn)'
    return 'var(--bad)'
  }

  // Heatmap/report are protected endpoints now, so plain <img src> / <a
  // href> tags (which cannot send an Authorization header) no longer work
  // -- fetch them as blobs instead and point the element at an object URL.
  const [heatmapUrl, setHeatmapUrl] = useState(null)
  useEffect(() => {
    let objectUrl
    if (result?.status === 'completed' && token) {
      fetch(`${API_URL}/heatmap/${result.analysis_id}`, { headers: authHeaders() })
        .then(res => (res.ok ? res.blob() : null))
        .then(blob => {
          if (blob) {
            objectUrl = URL.createObjectURL(blob)
            setHeatmapUrl(objectUrl)
          }
        })
        .catch(() => {})
    } else {
      setHeatmapUrl(null)
    }
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [result, token, authHeaders])

  const downloadReport = async () => {
    if (!result) return
    try {
      const res = await fetch(`${API_URL}/report/${result.analysis_id}`, { headers: authHeaders() })
      if (!res.ok) throw new Error('Could not fetch report')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `CertiFake_Report_${result.analysis_id}.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err.message)
    }
  }

  const scoreValue = result?.authenticity_score ?? 0
  const ringOffset = 326.7 - (326.7 * scoreValue / 100)

  return (
    <>
      <div className="bg-grid"></div>
      <div className="ambient ambient-1"></div>
      <div className="ambient ambient-2"></div>

      <main className="shell">
        <section className="hero card">
          <div>
            <div className="badge">AI Certificate Intelligence</div>
            <h1>CertiFake Pro</h1>
            <p className="subtitle">Fast authenticity scoring, OCR extraction, and tamper detection.</p>
          </div>
          <div className="ring-wrap">
            <svg viewBox="0 0 120 120" className="ring">
              <circle cx="60" cy="60" r="52" className="ring-track"></circle>
              <circle
                cx="60" cy="60" r="52"
                className="ring-fill"
                style={{ strokeDashoffset: ringOffset, stroke: getRingColor(scoreValue) }}
              ></circle>
            </svg>
            <div className="ring-text">
              <span>{result ? Math.round(scoreValue) : '--'}</span>
              <small>Authenticity</small>
            </div>
          </div>
        </section>

        {error && <div className="pill bad" style={{width: '100%', marginBottom: 24, justifyContent: 'center'}}>{error}</div>}

        <section className="grid two-col">
          {/* Left Column */}
          <div className="grid">
            <AuthPanel
              token={token}
              email={email}
              onAuthenticated={(newToken, newEmail) => { setToken(newToken); setEmail(newEmail); setError('') }}
              onLogout={handleLogout}
            />

            <div className="card panel">
              <h2>Upload Document</h2>
              <div
                className={`dropzone ${isDragActive ? 'active' : ''}`}
                onDragOver={e => { e.preventDefault(); setIsDragActive(true); }}
                onDragLeave={() => setIsDragActive(false)}
                onDrop={e => {
                  e.preventDefault()
                  setIsDragActive(false)
                  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                    setFile(e.dataTransfer.files[0])
                    setResult(null)
                  }
                }}
                onClick={() => fileInputRef.current.click()}
              >
                <input
                  type="file"
                  ref={fileInputRef}
                  hidden
                  accept="image/*,application/pdf"
                  onChange={handleFileChange}
                />
                <strong>{file ? file.name : 'Drop certificate here'}</strong>
                <p>or click to browse JPG, PNG, WEBP, or PDF</p>
              </div>

              <button
                className="btn mt-4"
                onClick={handleAnalyze}
                disabled={!file || analyzing || !token}
              >
                {!token ? 'Log in to analyze' : analyzing ? 'Analyzing...' : 'Analyze Document'}
              </button>
            </div>
          </div>

          {/* Right Column */}
          <div className="grid">
            <div className="card panel">
              <h2>Analysis Result</h2>
              <div className={`pill ${getPillClass(result?.verdict)}`}>
                {result ? result.verdict : 'Waiting for upload'}
              </div>

              {result && (
                <>
                  <div className="evidence-list">
                    <div><span>Confidence</span><strong>{Math.round((result.confidence ?? 0) * 100)}%</strong></div>
                    <div>
                      <span>Suspicious Signals</span>
                      <strong style={{color: result.suspicious_signals?.length > 0 ? 'var(--warn)' : 'var(--good)'}}>
                        {result.suspicious_signals?.length > 0 ? result.suspicious_signals.join(', ') : 'None Detected'}
                      </strong>
                    </div>
                  </div>

                  <details open>
                    <summary>Extracted Fields</summary>
                    <pre>{JSON.stringify(result.extracted_fields, null, 2)}</pre>
                  </details>

                  <details>
                    <summary>Raw OCR Text</summary>
                    <pre>{result.ocr_text || 'No text extracted'}</pre>
                  </details>

                  {result.status === 'completed' && heatmapUrl && (
                    <div className="preview-wrap mt-4">
                      <img src={heatmapUrl} alt="Forensic Heatmap" />
                    </div>
                  )}

                  {result.status === 'completed' && (
                    <button className="btn btn-secondary mt-4" onClick={downloadReport}>
                      Download PDF Report
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        </section>
      </main>
    </>
  )
}

export default App
