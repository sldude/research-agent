import { useEffect, useState, type FormEvent } from 'react'
import {
  confirmResetPassword,
  confirmSignUp,
  fetchAuthSession,
  getCurrentUser,
  resendSignUpCode,
  resetPassword,
  signIn,
  signOut,
  signUp,
} from 'aws-amplify/auth'
import './App.css'

type Corpus = {
  id: string
  name: string
  corpus_type: string
  owner_id: string | null
}

type RagSource = {
  number: number
  document_id: string
  external_id: string | null
  title: string
  source_url: string | null
  distance: number
}

type RagAnswer = {
  question: string
  answer: string
  sources: RagSource[]
}

type AuthMode =
  | 'signIn'
  | 'signUp'
  | 'confirmSignUp'
  | 'resetPassword'
  | 'confirmResetPassword'

function App() {
  const [question, setQuestion] = useState('')
  const [apiStatus, setApiStatus] = useState('Not checked')
  const [isCheckingApi, setIsCheckingApi] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmationCode, setConfirmationCode] = useState('')
  const [authMode, setAuthMode] = useState<AuthMode>('signIn')
  const [signedInUser, setSignedInUser] = useState<string | null>(null)
  const [authMessage, setAuthMessage] = useState('Checking sign-in status...')
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [corpora, setCorpora] = useState<Corpus[]>([])
  const [corporaMessage, setCorporaMessage] = useState('Not loaded')
  const [isLoadingCorpora, setIsLoadingCorpora] = useState(false)
  const [selectedCorpusId, setSelectedCorpusId] = useState('')
  const [ragAnswer, setRagAnswer] = useState<RagAnswer | null>(null)
  const [ragMessage, setRagMessage] = useState('')
  const [isAsking, setIsAsking] = useState(false)

  useEffect(() => {
    getCurrentUser()
      .then((user) => {
        setSignedInUser(user.signInDetails?.loginId ?? user.username)
        setAuthMessage('Signed in')
      })
      .catch(() => setAuthMessage('Not signed in'))
  }, [])

  function showAuthMode(mode: AuthMode, message: string) {
    setAuthMode(mode)
    setAuthMessage(message)
    setPassword('')
    setConfirmPassword('')
    setNewPassword('')
    setConfirmationCode('')
  }

  async function handleSignIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsAuthenticating(true)
    setAuthMessage('Signing in...')

    try {
      const result = await signIn({ username: email, password })
      if (!result.isSignedIn) {
        if (result.nextStep.signInStep === 'CONFIRM_SIGN_UP') {
          showAuthMode(
            'confirmSignUp',
            'Your email is not confirmed. Enter the verification code sent to your email.',
          )
          return
        }

        setAuthMessage(`Additional step required: ${result.nextStep.signInStep}`)
        return
      }

      const user = await getCurrentUser()
      setSignedInUser(user.signInDetails?.loginId ?? user.username)
      setPassword('')
      setAuthMessage('Signed in')
    } catch (error) {
      if (error instanceof Error && error.name === 'UserNotConfirmedException') {
        showAuthMode(
          'confirmSignUp',
          'Your email is not confirmed. Enter the verification code sent to your email.',
        )
        return
      }

      setAuthMessage(error instanceof Error ? error.message : 'Sign-in failed')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleSignUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (password !== confirmPassword) {
      setAuthMessage('Passwords do not match.')
      return
    }

    setIsAuthenticating(true)
    setAuthMessage('Creating account...')

    try {
      const result = await signUp({
        username: email,
        password,
        options: { userAttributes: { email } },
      })
      setPassword('')
      setConfirmPassword('')
      if (result.isSignUpComplete) {
        setAuthMode('signIn')
        setAuthMessage('Account created. Sign in to continue.')
      } else {
        setAuthMode('confirmSignUp')
        setAuthMessage('Enter the confirmation code sent to your email.')
      }
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Sign-up failed')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleConfirmSignUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsAuthenticating(true)
    setAuthMessage('Confirming account...')

    try {
      await confirmSignUp({ username: email, confirmationCode })
      setConfirmationCode('')
      setAuthMode('signIn')
      setAuthMessage('Email confirmed. Sign in to continue.')
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Confirmation failed')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleResendSignUpCode() {
    if (!email) {
      setAuthMessage('Enter the email address for the account.')
      return
    }

    setIsAuthenticating(true)
    setAuthMessage('Sending a new confirmation code...')
    try {
      await resendSignUpCode({ username: email })
      setAuthMessage('A new confirmation code was sent to your email.')
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Could not resend code')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleResetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsAuthenticating(true)
    setAuthMessage('Requesting reset code...')

    try {
      const result = await resetPassword({ username: email })
      if (result.nextStep.resetPasswordStep === 'CONFIRM_RESET_PASSWORD_WITH_CODE') {
        setAuthMode('confirmResetPassword')
        setAuthMessage('Enter the reset code sent to your email.')
      } else {
        setAuthMode('signIn')
        setAuthMessage('Password reset is complete. Sign in to continue.')
      }
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Reset request failed')
    } finally {
      setIsAuthenticating(false)
    }
  }

  async function handleConfirmResetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsAuthenticating(true)
    setAuthMessage('Updating password...')

    try {
      await confirmResetPassword({
        username: email,
        confirmationCode,
        newPassword,
      })
      setConfirmationCode('')
      setNewPassword('')
      setAuthMode('signIn')
      setAuthMessage('Password updated. Sign in to continue.')
    } catch (error) {
      setAuthMessage(error instanceof Error ? error.message : 'Password reset failed')
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
    setSelectedCorpusId('')
    setRagAnswer(null)
    setRagMessage('')
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
      setSelectedCorpusId((current) => current || result[0]?.id || '')
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl || !selectedCorpusId || !question.trim()) {
      setRagMessage('Choose a corpus and enter a question.')
      return
    }

    setIsAsking(true)
    setRagAnswer(null)
    setRagMessage('Retrieving sources and generating an answer...')
    try {
      const session = await fetchAuthSession()
      const accessToken = session.tokens?.accessToken.toString()
      if (!accessToken) {
        throw new Error('No Cognito access token is available')
      }

      const response = await fetch(`${apiUrl}/api/rag/answer`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          corpus_id: selectedCorpusId,
          question: question.trim(),
          limit: 5,
          max_tokens: 600,
        }),
      })
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`)
      }

      const result: RagAnswer = await response.json()
      setRagAnswer(result)
      setRagMessage('')
    } catch (error) {
      setRagMessage(error instanceof Error ? error.message : 'Request failed')
    } finally {
      setIsAsking(false)
    }
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
        ) : authMode === 'signIn' ? (
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
            <div className="auth-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => showAuthMode('signUp', 'Create a new account.')}
              >
                Create account
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  showAuthMode('resetPassword', 'Enter your account email.')
                }
              >
                Forgot password?
              </button>
            </div>
          </form>
        ) : authMode === 'signUp' ? (
          <form className="sign-in-form" onSubmit={handleSignUp}>
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
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <label htmlFor="confirm-password">Confirm password</label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
            <button type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? 'Creating...' : 'Create account'}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => showAuthMode('signIn', 'Sign in to your account.')}
            >
              Back to sign in
            </button>
          </form>
        ) : authMode === 'confirmSignUp' ? (
          <form className="sign-in-form" onSubmit={handleConfirmSignUp}>
            <p>Confirming {email}</p>
            <label htmlFor="confirmation-code">Confirmation code</label>
            <input
              id="confirmation-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={confirmationCode}
              onChange={(event) => setConfirmationCode(event.target.value)}
              required
            />
            <button type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? 'Confirming...' : 'Confirm email'}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={handleResendSignUpCode}
              disabled={isAuthenticating}
            >
              Resend code
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => showAuthMode('signIn', 'Sign in to your account.')}
            >
              Back to sign in
            </button>
          </form>
        ) : authMode === 'resetPassword' ? (
          <form className="sign-in-form" onSubmit={handleResetPassword}>
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
            <button type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? 'Sending...' : 'Send reset code'}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => showAuthMode('signIn', 'Sign in to your account.')}
            >
              Back to sign in
            </button>
          </form>
        ) : (
          <form className="sign-in-form" onSubmit={handleConfirmResetPassword}>
            <p>Resetting password for {email}</p>
            <label htmlFor="confirmation-code">Reset code</label>
            <input
              id="confirmation-code"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={confirmationCode}
              onChange={(event) => setConfirmationCode(event.target.value)}
              required
            />
            <label htmlFor="new-password">New password</label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              required
            />
            <button type="submit" disabled={isAuthenticating}>
              {isAuthenticating ? 'Updating...' : 'Set new password'}
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => showAuthMode('signIn', 'Sign in to your account.')}
            >
              Back to sign in
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

      {signedInUser && (
        <form className="question-form" onSubmit={handleSubmit}>
          <label htmlFor="corpus">Corpus</label>
          <select
            id="corpus"
            value={selectedCorpusId}
            onChange={(event) => setSelectedCorpusId(event.target.value)}
            disabled={corpora.length === 0 || isAsking}
          >
            {corpora.length === 0 ? (
              <option value="">Load a corpus first</option>
            ) : (
              corpora.map((corpus) => (
                <option key={corpus.id} value={corpus.id}>
                  {corpus.name}
                </option>
              ))
            )}
          </select>

          <label htmlFor="question">Research question</label>
          <textarea
            id="question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="What are some limitations of RAG systems?"
            rows={4}
            disabled={isAsking}
          />
          <button
            type="submit"
            disabled={!question.trim() || !selectedCorpusId || isAsking}
          >
            {isAsking ? 'Generating...' : 'Ask'}
          </button>
          {ragMessage && <p>{ragMessage}</p>}
        </form>
      )}

      {ragAnswer && (
        <section className="answer-card">
          <h2>Answer</h2>
          <p className="answer-text">{ragAnswer.answer}</p>

          <h3>Sources</h3>
          <ol>
            {ragAnswer.sources.map((source) => (
              <li key={`${source.number}-${source.document_id}`}>
                {source.source_url ? (
                  <a href={source.source_url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                ) : (
                  source.title
                )}
                <span>Distance: {source.distance.toFixed(4)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </main>
  )
}

export default App
