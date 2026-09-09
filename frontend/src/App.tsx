import { useState, type FormEvent } from 'react'
import './App.css'

function App() {
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [apiStatus, setApiStatus] = useState('Not checked')
  const [isCheckingApi, setIsCheckingApi] = useState(false)

  async function checkApiHealth() {
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl) {
      setApiStatus('Missing VITE_API_URL')
      return
    }

    setIsCheckingApi(true)
    setApiStatus('Checking...')
    try {
      const response = await fetch(`${apiUrl}/health`)
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`)
      }

      const result: { status: string } = await response.json()
      setApiStatus(result.status)
    } catch (error) {
      setApiStatus(error instanceof Error ? error.message : 'Request failed')
    } finally {
      setIsCheckingApi(false)
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmittedQuestion(question.trim())
  }

  return (
    <main className="app">
      <h1>Research Agent</h1>
      <p>Ask questions grounded in scientific literature.</p>

      <section className="api-status">
        <div>
          <h2>Backend connection</h2>
          <p>Status: {apiStatus}</p>
        </div>
        <button type="button" onClick={checkApiHealth} disabled={isCheckingApi}>
          {isCheckingApi ? 'Checking...' : 'Check API'}
        </button>
      </section>

      <form className="question-form" onSubmit={handleSubmit}>
        <label htmlFor="question">Research question</label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="What are some limitations of RAG systems?"
          rows={4}
        />
        <button type="submit" disabled={!question.trim()}>
          Ask
        </button>
      </form>

      {submittedQuestion && (
        <section className="submitted-question">
          <h2>Submitted question</h2>
          <p>{submittedQuestion}</p>
        </section>
      )}
    </main>
  )
}

export default App
