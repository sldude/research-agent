import { useEffect, useState, type FormEvent } from 'react'
import { fetchAuthSession, getCurrentUser, signIn, signOut } from 'aws-amplify/auth'
import './App.css'

type Corpus = {
  id: string
  name: string
  corpus_type: string
  owner_id: string | null
}

function App() {
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [apiStatus, setApiStatus] = useState('Not checked')
  const [isCheckingApi, setIsCheckingApi] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [signedInUser, setSignedInUser] = useState<string | null>(null)
  const [authMessage, setAuthMessage] = useState('Checking sign-in status...')
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [corpora, setCorpora] = useState<Corpus[]>([])
  const [corporaMessage, setCorporaMessage] = useState('Not loaded')
  const [isLoadingCorpora, setIsLoadingCorpora] = useState(false)

  useEffect(() => {
    getCurrentUser()
      .then((user) => {
        setSignedInUser(user.signInDetails?.loginId ?? user.username)
        setAuthMessage('Signed in')
      })
      .catch(() => setAuthMessage('Not signed in'))
  }, [])

  async function handleSignIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsAuthenticating(true)
    setAuthMessage('Signing in...')

    try {
      const result = await signIn({ username: email, password })
      if (!result.isSignedIn) {
        setAuthMessage(`Additional step required: ${result.nextStep.signInStep}`)
        return
      }

      const user = await getCurrentUser()
      setSignedInUser(user.signInDetails?.loginId ?? user.username)
      setPassword('')
      setAuthMessage('Signed in')
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Sign-in failed')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleSignOut() {
    await signOut()
    setSignedInUser(null)
    setAuthMessage('Not signed in')
    setCorpora([])
    setCorporaMessage('Not loaded')
  }

  async function loadCorpora() {
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl) {
      setCorporaMessage('Missing VITE_API_URL')
      return
    }

    setIsLoadingCorpora(true)
    setCorporaMessage('Loading...')
    try {
      const session = await fetchAuthSession()
      const accessToken = session.tokens?.accessToken.toString()
      if (!accessToken) {
        throw new Error('No Cognito access token is available')
      }

      const response = await fetch(`${apiUrl}/api/corpora`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      })
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`)
      }

      const result: Corpus[] = await response.json()
      setCorpora(result)
      setCorporaMessage(`Loaded ${result.length} corpora`)
    } catch (error) {
      setCorpora([])
      setCorporaMessage(error instanceof Error ? error.message : 'Request failed')
    } finally {
      setIsLoadingCorpora(false)
    }
  }

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

      <section className="auth-card">
        <h2>Account</h2>
        {signedInUser ? (
          <div className="signed-in-row">
            <p>Signed in as {signedInUser}</p>
            <button type="button" onClick={handleSignOut}>
              Sign out
            </button>
          </div>
        ) : (
          <form className="sign-in-form" onSubmit={handleSignIn}>
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? 'Signing in...' : 'Sign in'}
            </button>
          </form>
        )}
        <p className="auth-message">{authMessage}</p>
      </section>

      {signedInUser && (
        <section className="corpora-card">
          <div className="corpora-heading">
            <div>
              <h2>Available corpora</h2>
              <p>{corporaMessage}</p>
            </div>
            <button type="button" onClick={loadCorpora} disabled={isLoadingCorpora}>
              {isLoadingCorpora ? 'Loading...' : 'Load corpora'}
            </button>
          </div>
          {corpora.length > 0 && (
            <ul>
              {corpora.map((corpus) => (
                <li key={corpus.id}>
                  <strong>{corpus.name}</strong>
                  <span>{corpus.corpus_type}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

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
