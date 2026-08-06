import { useState, useRef } from 'react'
import './index.css'

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

function App() {
  const [file, setFile] = useState(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const fileInputRef = useRef(null)

  const handleAnalyze = async () => {
    if (!file) return setError('Choose a certificate file first')
      
    setAnalyzing(true)
    setError('')
    setResult(null)
    
    const formData = new FormData()
    formData.append('file', file)
    
    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: 'POST',
        body: formData
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Analysis failed')
      setResult(data)
    } catch (err) {
      setError(err.message)
    } finally {
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
            {/* Authentication panel removed */}

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
                disabled={!file || analyzing}
              >
                {analyzing ? 'Analyzing...' : 'Analyze Document'}
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
                    <div><span>Confidence</span><strong>{Math.round(result.confidence * 100)}%</strong></div>
                    <div><span>File Type</span><strong>{result.content_type}</strong></div>
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

                  {result.preview_path && (
                    <div className="preview-wrap mt-4">
                      <img src={result.preview_path} alt="Forensic Heatmap" />
                    </div>
                  )}

                  {result.report_id && (
                    <a 
                      href={`${API_URL}/report/${result.report_id}`} 
                      target="_blank" 
                      rel="noreferrer"
                      className="btn btn-secondary mt-4"
                    >
                      Download PDF Report
                    </a>
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
