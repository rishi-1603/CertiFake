let token = ''
let selectedFile = null

const $ = (id) => document.getElementById(id)
const setText = (id, value) => { $(id).textContent = value ?? '--' }

const dropzone = $('dropzone')
const fileInput = $('fileInput')
const fileName = $('fileName')
const analyzeBtn = $('analyzeBtn')
const loginBtn = $('loginBtn')

function setVerdict(verdict) {
  const pill = $('verdictPill')
  pill.textContent = verdict
  pill.className = 'pill '
  if (verdict.includes('Likely Genuine')) pill.classList.add('good')
  else if (verdict.includes('Needs Review')) pill.classList.add('warn')
  else if (verdict.includes('Likely Fake')) pill.classList.add('bad')
  else pill.classList.add('idle')
}

function setRing(score) {
  const fill = $('ringFill')
  const value = Math.max(0, Math.min(100, Number(score) || 0))
  const offset = 326.7 - (326.7 * value / 100)
  fill.style.strokeDashoffset = offset
  fill.style.stroke = value >= 80 ? 'var(--good)' : value >= 55 ? 'var(--warn)' : 'var(--bad)'
  $('ringValue').textContent = value ? `${value.toFixed(0)}` : '--'
}

dropzone.addEventListener('click', () => fileInput.click())
fileInput.addEventListener('change', () => {
  selectedFile = fileInput.files[0] || null
  fileName.textContent = selectedFile ? selectedFile.name : 'No file selected'
})
['dragenter','dragover'].forEach(evt => dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add('drag') }))
;['dragleave','drop'].forEach(evt => dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove('drag') }))
dropzone.addEventListener('drop', e => {
  const file = e.dataTransfer.files[0]
  if (!file) return
  selectedFile = file
  fileInput.files = e.dataTransfer.files
  fileName.textContent = file.name
})

loginBtn.addEventListener('click', async () => {
  const username = $('username').value || 'admin'
  const password = $('password').value || 'password'
  const r = await fetch('/auth/login', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({username, password})
  })
  const data = await r.json()
  if (!r.ok) { $('tokenStatus').textContent = data.detail || 'Login failed'; return }
  token = data.access_token
  $('tokenStatus').textContent = token
})

analyzeBtn.addEventListener('click', async () => {
  if (!selectedFile) { alert('Choose a certificate file first'); return }
  if (!token) { alert('Login first and get token'); return }

  const form = new FormData()
  form.append('file', selectedFile)

  analyzeBtn.disabled = true
  analyzeBtn.textContent = 'Analyzing...'

  try {
    const r = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: form
    })
    const data = await r.json()
    if (!r.ok) throw new Error(data.detail || 'Analysis failed')

    setRing(data.authenticity_score)
    setVerdict(data.verdict)
    setText('statScore', `${data.authenticity_score}`)
    setText('statVerdict', data.verdict)
    setText('statConfidence', `${Math.round((data.confidence || 0) * 100)}%`)
    setText('scoreText', `${data.authenticity_score}`)
    setText('confidenceText', `${Math.round((data.confidence || 0) * 100)}%`)
    setText('resultCopy', data.verdict + '. ' + (data.suspicious_signals?.length ? data.suspicious_signals.join(', ') : 'No major warning signals.'))
    setText('eFile', data.file_name || '--')
    setText('eType', data.content_type || '--')
    setText('eSignals', (data.suspicious_signals && data.suspicious_signals.length) ? data.suspicious_signals.join(' • ') : 'None')
    $('fieldsBox').textContent = JSON.stringify(data.extracted_fields || {}, null, 2)
    $('ocrBox').textContent = data.ocr_text || 'No OCR text returned'
    if (data.preview_path) {
      $('previewImage').src = data.preview_path
      $('previewWrap').hidden = false
    } else {
      $('previewWrap').hidden = true
    }
    if (data.report_id) {
      $('reportLink').href = `/report/${data.report_id}`
      $('reportLink').classList.remove('hidden')
    }
  } catch (err) {
    alert(err.message)
  } finally {
    analyzeBtn.disabled = false
    analyzeBtn.textContent = 'Analyze'
  }
})