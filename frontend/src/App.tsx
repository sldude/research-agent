import { useEffect, useRef, useState, type SubmitEvent} from 'react'
import {
  confirmResetPassword,
  confirmSignUp,
  deleteUser,
  fetchAuthSession,
  getCurrentUser,
  resendSignUpCode,
  resetPassword,
  signIn,
  signOut,
  signUp,
} from 'aws-amplify/auth'
import './App.css'
import { MAX_UPLOAD_BYTES, uploadDocument } from './uploadDocument'
import { deleteAccount } from './deleteAccount'

function ProjectLinks() {
  return (
    <nav className="project-links" aria-label="Project information">
      <a className="github-link" href="https://github.com/sldude/research-agent" target="_blank" rel="noopener noreferrer">
        Github
        <span className="github-link-hint"> (opens in a new tab)</span>
      </a>
      <a className="github-link" href="/privacy-policy/index.html" target="_blank" rel="noopener noreferrer">
        Privacy policy
        <span className="github-link-hint"> (opens in a new tab)</span>
      </a>
      <a className="github-link" href="mailto:steven.r.liu20@gmail.com">Contact</a>
    </nav>
  )
}

type Corpus = {
  id: string
  name: string
  corpus_type: string
  owner_id: string | null
  document_count?: number | null
  count_unavailable?: boolean
}

function corpusDocumentCount(corpus: Corpus) {
  if (corpus.document_count == null) return corpus.count_unavailable ? 'Document count unavailable' : 'Counting documents…'
  return `${corpus.document_count.toLocaleString()} document${corpus.document_count === 1 ? '' : 's'}`
}

type UploadedDocument = {
  document_id: string
  filename: string
  status: string
  created_at: string
  chunks_saved: number | null
}

type UploadTracking = {
  corpusId: string
  documentId: string
  filename: string
}

type DocumentPreview = {
  url: string
  text: string | null
  isPdf: boolean
}

type OpenPreview = DocumentPreview & { name: string; revokeOnClose?: boolean }

type RagSource = {
  number: number
  reference_type: 'cited' | 'additional'
  document_id: string
  external_id: string | null
  title: string
  source_url: string | null
  distance: number | null
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

type WorkspaceTab = 'ask' | 'manage' | 'settings'
type CorporaTab = 'manage' | 'create'

function fileKind(filename: string) {
  if (filename.toLowerCase().endsWith('.pdf')) return 'PDF'
  if (filename.toLowerCase().endsWith('.md')) return 'MD'
  return 'TXT'
}

function corpusTypeLabel(corpusType: string) {
  if (corpusType === 'research_abstract') return 'Abstracts ingested from arXiv'
  if (corpusType === 'user_upload') return 'My corpus'
  return 'Research corpus'
}

function corpusDisplayName(corpus: Corpus) {
  return corpus.corpus_type === 'research_abstract' && corpus.name === 'My arXiv research corpus'
    ? 'My ArXiv Research Corpus'
    : corpus.name
}

function FileTypeIcon({ filename }: { filename: string }) {
  const kind = fileKind(filename)
  return <span className={`file-icon file-icon-${kind.toLowerCase()}`}>
    {kind === 'PDF' ? <strong>PDF</strong> : <>
      <svg viewBox="0 0 48 58" aria-hidden="true">
        <path d="M9 2h21l9 9v45H9z" />
        <path d="M30 2v10h9M16 24h16M16 31h16M16 38h12" />
      </svg>
      <strong>{kind}</strong>
    </>}
  </span>
}

function DeleteIcon() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
}

function LocalFileTile({ file, onRemove, onOpen }: {
  file: File
  onRemove: () => void
  onOpen: (preview: OpenPreview) => void
}) {
  const [preview, setPreview] = useState<DocumentPreview | null>(null)
  useEffect(() => {
    const url = URL.createObjectURL(file)
    const isPdf = file.name.toLowerCase().endsWith('.pdf')
    if (isPdf) setPreview({ url, isPdf: true, text: null })
    else void file.text().then((text) => setPreview({ url, isPdf: false, text }))
    return () => URL.revokeObjectURL(url)
  }, [file])
  return <article className="document-tile pending-tile">
    <button type="button" className="remove-document" aria-label={`Remove ${file.name}`} onClick={onRemove}><DeleteIcon /></button>
    <button type="button" className="preview-trigger" onClick={() => preview && onOpen({ ...preview, name: file.name })}>
      <div className="document-preview">
        <FileTypeIcon filename={file.name} />
      </div>
      <strong>{file.name}</strong><span>Ready to upload</span>
    </button>
  </article>
}

function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('ask')
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [isDeletingAccount, setIsDeletingAccount] = useState(false)
  const [accountMessage, setAccountMessage] = useState('')
  const [corporaTab, setCorporaTab] = useState<CorporaTab>('manage')
  const [question, setQuestion] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmationCode, setConfirmationCode] = useState('')
  const [authMode, setAuthMode] = useState<AuthMode>('signIn')
  const [signedInUser, setSignedInUser] = useState<string | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [authMessage, setAuthMessage] = useState('Checking sign-in status...')
  const [isAuthenticating, setIsAuthenticating] = useState(false)
  const [corpora, setCorpora] = useState<Corpus[]>([])
  const corporaRequest = useRef<AbortController | null>(null)
  useEffect(() => () => corporaRequest.current?.abort(), [])
  const [corporaMessage, setCorporaMessage] = useState('Not loaded')
  const [isLoadingCorpora, setIsLoadingCorpora] = useState(false)
  const [selectedCorpusId, setSelectedCorpusId] = useState('')
  const [documentCorpusId, setDocumentCorpusId] = useState('')
  const [documents, setDocuments] = useState<UploadedDocument[]>([])
  const [documentsMessage, setDocumentsMessage] = useState('')
  const [documentsRefresh, setDocumentsRefresh] = useState(0)
  const [ragAnswer, setRagAnswer] = useState<RagAnswer | null>(null)
  const [ragMessage, setRagMessage] = useState('')
  const [isAsking, setIsAsking] = useState(false)
  const [newCorpusName, setNewCorpusName] = useState('')
  const [documentFiles, setDocumentFiles] = useState<File[]>([])
  const [additionalFiles, setAdditionalFiles] = useState<File[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [addFilesMessage, setAddFilesMessage] = useState('')
  const [uploadTracking, setUploadTracking] = useState<UploadTracking | null>(null)
  const [openPreview, setOpenPreview] = useState<OpenPreview | null>(null)
  const [previewLoadingId, setPreviewLoadingId] = useState('')
  const [deletingDocumentId, setDeletingDocumentId] = useState('')
  const [pendingDeletionIds, setPendingDeletionIds] = useState<string[]>([])
  const [deletingCorpusId, setDeletingCorpusId] = useState('')

  useEffect(() => {
    if (!uploadTracking) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const startedAt = Date.now()
    let lastStatus = 'uploaded'

    async function checkStatus() {
      if (!uploadTracking) return
      try {
        const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
        if (!apiUrl) throw new Error('API URL is not configured.')
        const session = await fetchAuthSession()
        const token = session.tokens?.accessToken.toString()
        if (!token) throw new Error('Sign in to check document status.')

        const response = await fetch(
          `${apiUrl}/api/corpora/${encodeURIComponent(uploadTracking.corpusId)}/documents/${encodeURIComponent(uploadTracking.documentId)}/status`,
          { headers: { Authorization: `Bearer ${token}` } },
        )
        if (!response.ok) throw new Error(`Status check failed (${response.status}).`)
        const result: { status: string; chunks_saved: number | null } = await response.json()
        if (cancelled) return
        lastStatus = result.status

        if (result.status === 'ready') {
          setUploadMessage(`${uploadTracking.filename} is ready for questions (${result.chunks_saved ?? 0} chunks).`)
          setDocumentsRefresh((current) => current + 1)
          return
        }
        if (result.status === 'failed' || result.status === 'upload_failed') {
          setUploadMessage(`${uploadTracking.filename} processing failed. Retrying may still occur; check the ingestion worker logs.`)
        } else {
          setUploadMessage(`${uploadTracking.filename} uploaded. Processing ${result.status}...`)
        }
      } catch (error) {
        if (cancelled) return
        setUploadMessage(error instanceof Error ? error.message : 'Could not check document status.')
      }

      if (Date.now() - startedAt < 5 * 60 * 1000) {
        timer = setTimeout(checkStatus, 3000)
      } else if (!cancelled) {
        setUploadMessage(
          lastStatus === 'failed' || lastStatus === 'upload_failed'
            ? `${uploadTracking.filename} could not be processed. Check the ingestion worker logs.`
            : `${uploadTracking.filename} is taking longer than expected. Check the ingestion worker logs.`,
        )
      }
    }

    void checkStatus()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [uploadTracking])

  useEffect(() => {
    getCurrentUser()
      .then((user) => {
        setSignedInUser(user.signInDetails?.loginId ?? user.username)
        setAuthMessage('Signed in')
        void loadCorpora()
      })
      .catch(() => setAuthMessage('Not signed in'))
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!documentCorpusId) {
      setDocuments([])
      setDocumentsMessage('')
      return
    }

    const controller = new AbortController()

    async function loadDocuments() {
      setDocuments([])
      setDocumentsMessage('Loading documents...')

      try {
        const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
        if (!apiUrl) throw new Error('API URL is not configured.')

        const session = await fetchAuthSession()
        const token = session.tokens?.accessToken.toString()
        if (!token) throw new Error('Sign in to load documents.')

        const response = await fetch(
          `${apiUrl}/api/corpora/${encodeURIComponent(documentCorpusId)}/documents`,
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
          },
        )
        if (!response.ok) {
          throw new Error(`Could not load documents (${response.status}).`)
        }

        const result: UploadedDocument[] = await response.json()
        if (controller.signal.aborted) return

        setDocuments(result)
        setCorpora((current) => current.map((corpus) =>
          corpus.id === documentCorpusId ? { ...corpus, document_count: result.length } : corpus,
        ))
        setDocumentsMessage(
          result.length === 0 ? 'No documents in this corpus yet.' : '',
        )
      } catch (error) {
        if (controller.signal.aborted) return
        setDocumentsMessage(
          error instanceof Error ? error.message : 'Could not load documents.',
        )
      }
    }

    void loadDocuments()
    return () => controller.abort()
  }, [documentCorpusId, documentsRefresh])

  function showAuthMode(mode: AuthMode, message: string) {
    setAuthMode(mode)
    setAuthMessage(message)
    setPassword('')
    setConfirmPassword('')
    setNewPassword('')
    setConfirmationCode('')
  }

  async function handleSignIn(event: SubmitEvent <HTMLFormElement>) {
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
      void loadCorpora()
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

  async function handleSignUp(event: SubmitEvent <HTMLFormElement>) {
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

  async function handleConfirmSignUp(event: SubmitEvent <HTMLFormElement>) {
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

  async function handleResetPassword(event: SubmitEvent <HTMLFormElement>) {
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

  async function handleConfirmResetPassword(event: SubmitEvent <HTMLFormElement>) {
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
    clearAccountState()
  }

  function clearAccountState() {
    corporaRequest.current?.abort()
    closePreview()
    setUploadTracking(null)
    setDocumentFiles([])
    setQuestion('')
    setPassword('')
    setConfirmPassword('')
    setNewPassword('')
    setConfirmationCode('')
    setDeleteConfirmation('')
    setAccountMessage('')
    setActiveTab('ask')
    setSignedInUser(null)
    setAuthMessage('Not signed in')
    setCorpora([])
    setCorporaMessage('Not loaded')
    setSelectedCorpusId('')
    setDocumentCorpusId('')
    setDocuments([])
    setAdditionalFiles([])
    setPendingDeletionIds([])
    setRagAnswer(null)
    setRagMessage('')
  }

  async function handleDeleteAccount(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (deleteConfirmation !== 'DELETE' || isDeletingAccount || isUploading || isAsking || deletingCorpusId || deletingDocumentId) return
    setIsDeletingAccount(true)
    setAccountMessage('Removing your corpora and uploads, then deleting your account. Keep this page open.')
    corporaRequest.current?.abort()
    setUploadTracking(null)
    setRagAnswer(null)
    try {
      const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
      if (!apiUrl) throw new Error('API URL is not configured.')
      const user = await getCurrentUser()
      await deleteAccount({
        apiUrl,
        userId: user.userId,
        getToken: async () => {
          const session = await fetchAuthSession()
          const token = session.tokens?.accessToken.toString()
          if (!token) throw new Error('Sign in again before deleting your account.')
          return token
        },
        removeUser: deleteUser,
      })
      clearAccountState()
      setAuthMode('signIn')
      setAuthMessage('Your account has been deleted.')
    } catch (error) {
      setAccountMessage(`${error instanceof Error ? error.message : 'Account deletion failed.'} Deletion did not complete. Some corpora may already have been removed. You can retry; removed data cannot be restored.`)
      void loadCorpora()
    } finally {
      setIsDeletingAccount(false)
    }
  }

  async function loadCorpora() {
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl) {
      setCorporaMessage('Missing VITE_API_URL')
      return
    }

    corporaRequest.current?.abort()
    const controller = new AbortController()
    corporaRequest.current = controller
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
        signal: controller.signal,
      })
      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`)
      }

      const result: Corpus[] = await response.json()
      if (controller.signal.aborted) return
      setCorpora(result)
      setSelectedCorpusId((current) => current || result[0]?.id || '')
      setDocumentCorpusId((current) =>
        current || result.find((corpus) => corpus.corpus_type === 'user_upload')?.id || '',
      )
      setCorporaMessage(`Loaded ${result.length} corpora`)
      // Counts must never hold up corpus selection. Limit concurrency and stop
      // stale requests on reload/sign-out; document-list updates take priority.
      const pending = result.filter((corpus) => corpus.document_count == null)
      async function loadCounts() {
        while (pending.length && !controller.signal.aborted) {
          const corpus = pending.shift()!
          try {
            const response = await fetch(`${apiUrl}/api/corpora/${encodeURIComponent(corpus.id)}/document-count`, {
              headers: { Authorization: `Bearer ${accessToken}` },
              signal: controller.signal,
            })
            if (!response.ok) throw new Error('Count unavailable')
            const body = await response.json()
            if (!Number.isSafeInteger(body.document_count) || body.document_count < 0) throw new Error('Invalid count')
            if (controller.signal.aborted) return
            setCorpora((current) => current.map((item) =>
              item.id === corpus.id && item.document_count == null
                ? { ...item, document_count: body.document_count } : item,
            ))
          } catch {
            if (controller.signal.aborted) return
            setCorpora((current) => current.map((item) =>
              item.id === corpus.id ? { ...item, count_unavailable: true } : item,
            ))
          }
        }
      }
      void Promise.all([loadCounts(), loadCounts()])
    } catch (error) {
      if (controller.signal.aborted) return
      setCorpora([])
      setCorporaMessage(error instanceof Error ? error.message : 'Request failed')
    } finally {
      if (!controller.signal.aborted) setIsLoadingCorpora(false)
    }
  }

  async function handleSubmit(event: SubmitEvent <HTMLFormElement>) {
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
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string'
          ? body.detail
          : `Answer request failed with status ${response.status}`)
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

  async function handleCreateAndUpload(
    event: SubmitEvent <HTMLFormElement>,
  ) {
    event.preventDefault()

    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    const name = newCorpusName.trim()
    const files = documentFiles

    if (!apiUrl || !name || files.length === 0) {
      setUploadMessage('Configure the API, enter a name, and choose at least one file.')
      return
    }

    const invalidFile = files.find(
      (file) => file.size === 0 || file.size > MAX_UPLOAD_BYTES,
    )
    if (invalidFile) {
      setUploadMessage(`${invalidFile.name} must be nonempty and no larger than 5 MB.`)
      return
    }

    setIsUploading(true)
    setUploadTracking(null)
    setUploadMessage('Creating corpus...')

    // Gives useful messages for HTTP errors, including validation errors.
    async function checkResponse(response: Response) {
      if (response.ok) return

      const body = await response.json().catch(() => null)
      throw new Error(
        typeof body?.detail === 'string'
          ? body.detail
          : `Request failed (${response.status})`,
      )
    }

    let createdCorpus: Corpus | null = null

    try {
      const session = await fetchAuthSession()
      const accessToken = session.tokens?.accessToken.toString()

      if (!accessToken) {
        throw new Error('Sign in before uploading.')
      }

      const createResponse = await fetch(`${apiUrl}/api/corpora`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name }),
      })

      await checkResponse(createResponse)
      const corpus: Corpus = await createResponse.json()
      createdCorpus = corpus

      // Add or update this corpus in the existing selector.
      setCorpora((current) => [
        ...current.filter((item) => item.id !== corpus.id),
        corpus,
      ])
      setSelectedCorpusId(corpus.id)
      setDocumentCorpusId(corpus.id)
      setRagAnswer(null)
      setRagMessage('')

      let lastUploaded: UploadTracking | null = null
      for (const [index, file] of files.entries()) {
        setUploadMessage(`Uploading ${index + 1} of ${files.length}: ${file.name}`)
        const uploaded = await uploadDocument(apiUrl, corpus.id, accessToken, file)
        setDocumentFiles((current) => current.filter((item) => item !== file))
        lastUploaded = {
          corpusId: corpus.id,
          documentId: uploaded.document_id,
          filename: file.name,
        }
      }
      setDocumentsRefresh((current) => current + 1)
      setDocumentFiles([])
      setUploadMessage(`${files.length} file${files.length === 1 ? '' : 's'} uploaded. Processing has started.`)
      setUploadTracking(lastUploaded)
      setCorporaTab('manage')
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Request failed.'

      setUploadMessage(
        createdCorpus
          ? `Corpus "${createdCorpus.name}" exists, but upload failed: ${message}`
          : message,
      )
    } finally {
      setDocumentsRefresh((current) => current + 1)
      setIsUploading(false)
    }
  }

  async function handleSaveCorpusChanges(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl || !documentCorpusId) {
      setAddFilesMessage('Choose a corpus first.')
      return
    }
    if (additionalFiles.length === 0 && pendingDeletionIds.length === 0) {
      setAddFilesMessage('Add or remove documents before saving changes.')
      return
    }

    const invalidFile = additionalFiles.find(
      (file) => file.size === 0 || file.size > MAX_UPLOAD_BYTES,
    )
    if (invalidFile) {
      setAddFilesMessage(`${invalidFile.name} must be nonempty and no larger than 5 MB.`)
      return
    }
    if (pendingDeletionIds.length > 0 && !window.confirm(
      `Save changes and permanently delete ${pendingDeletionIds.length} document${pendingDeletionIds.length === 1 ? '' : 's'}?`,
    )) return

    setIsUploading(true)
    setUploadTracking(null)
    try {
      const session = await fetchAuthSession()
      const accessToken = session.tokens?.accessToken.toString()
      if (!accessToken) throw new Error('Sign in before saving changes.')

      const deletionCount = pendingDeletionIds.length
      const additionCount = additionalFiles.length
      for (const [index, documentId] of pendingDeletionIds.entries()) {
        const document = documents.find((item) => item.document_id === documentId)
        setDeletingDocumentId(documentId)
        setAddFilesMessage(`Deleting ${index + 1} of ${deletionCount}: ${document?.filename ?? 'document'}`)
        const response = await fetch(
          `${apiUrl}/api/corpora/${encodeURIComponent(documentCorpusId)}/documents/${encodeURIComponent(documentId)}`,
          { method: 'DELETE', headers: { Authorization: `Bearer ${accessToken}` } },
        )
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(typeof body?.detail === 'string' ? body.detail : `Delete failed (${response.status}).`)
        }
        setPendingDeletionIds((current) => current.filter((id) => id !== documentId))
        setRagAnswer(null)
        setRagMessage('')
      }
      setDeletingDocumentId('')

      let lastUploaded: UploadTracking | null = null
      for (const [index, file] of additionalFiles.entries()) {
        setAddFilesMessage(`Uploading ${index + 1} of ${additionCount}: ${file.name}`)
        const uploaded = await uploadDocument(apiUrl, documentCorpusId, accessToken, file)
        lastUploaded = {
          corpusId: documentCorpusId,
          documentId: uploaded.document_id,
          filename: file.name,
        }
        setAdditionalFiles((current) => current.filter((item) => item !== file))
      }

      setDocumentsRefresh((current) => current + 1)
      setAddFilesMessage(`Changes saved: ${additionCount} added, ${deletionCount} deleted.`)
      setUploadTracking(lastUploaded)
    } catch (error) {
      setAddFilesMessage(error instanceof Error ? error.message : 'Could not save changes.')
      setDocumentsRefresh((current) => current + 1)
    } finally {
      setIsUploading(false)
      setDeletingDocumentId('')
    }
  }

  function handleDocumentCorpusChange(nextCorpusId: string) {
    if (
      (additionalFiles.length > 0 || pendingDeletionIds.length > 0) &&
      !window.confirm('Discard your unsaved document changes?')
    ) return
    setDocumentCorpusId(nextCorpusId)
    setAdditionalFiles([])
    setPendingDeletionIds([])
    setAddFilesMessage('')
  }

  function toggleDocumentDeletion(documentId: string) {
    setPendingDeletionIds((current) =>
      current.includes(documentId)
        ? current.filter((id) => id !== documentId)
        : [...current, documentId],
    )
  }

  async function handleDeleteCorpus(corpus: Corpus) {
    if ((additionalFiles.length > 0 || pendingDeletionIds.length > 0) && corpus.id === documentCorpusId) {
      setDocumentsMessage('Save or discard your document changes before deleting this corpus.')
      return
    }
    if (!window.confirm(`Delete "${corpus.name}" and all of its documents? This cannot be undone.`)) return
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl) return
    setDeletingCorpusId(corpus.id)
    setDocumentsMessage(`Deleting ${corpus.name}...`)
    try {
      const session = await fetchAuthSession()
      const token = session.tokens?.accessToken.toString()
      if (!token) throw new Error('Sign in before deleting a corpus.')
      const response = await fetch(`${apiUrl}/api/corpora/${encodeURIComponent(corpus.id)}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(typeof body?.detail === 'string' ? body.detail : `Delete failed (${response.status}).`)
      }
      const remainingCorpora = corpora.filter((item) => item.id !== corpus.id)
      const nextUserCorpus = remainingCorpora.find((item) => item.corpus_type === 'user_upload')
      setCorpora(remainingCorpora)
      if (documentCorpusId === corpus.id) {
        setDocumentCorpusId(nextUserCorpus?.id ?? '')
        setDocuments([])
      }
      if (selectedCorpusId === corpus.id) setSelectedCorpusId(remainingCorpora[0]?.id ?? '')
      setDocumentsMessage(`${corpus.name} was deleted.`)
    } catch (error) {
      setDocumentsMessage(error instanceof Error ? error.message : 'Could not delete corpus.')
    } finally {
      setDeletingCorpusId('')
    }
  }

  async function handleOpenStoredDocument(document: UploadedDocument) {
    const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, '')
    if (!apiUrl || !documentCorpusId) return
    setPreviewLoadingId(document.document_id)
    try {
      const session = await fetchAuthSession()
      const token = session.tokens?.accessToken.toString()
      if (!token) throw new Error('Sign in to preview this document.')
      const response = await fetch(
        `${apiUrl}/api/corpora/${encodeURIComponent(documentCorpusId)}/documents/${encodeURIComponent(document.document_id)}/content`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!response.ok) throw new Error(`Could not load preview (${response.status}).`)
      const blob = await response.blob()
      const isPdf = document.filename.toLowerCase().endsWith('.pdf')
      setOpenPreview({
        name: document.filename,
        isPdf,
        url: URL.createObjectURL(blob),
        text: isPdf ? null : await blob.text(),
        revokeOnClose: true,
      })
    } catch (error) {
      setDocumentsMessage(error instanceof Error ? error.message : 'Could not load preview.')
    } finally {
      setPreviewLoadingId('')
    }
  }

  function closePreview() {
    if (openPreview?.revokeOnClose) URL.revokeObjectURL(openPreview.url)
    setOpenPreview(null)
  }

  if (!authChecked) return <main className="auth-loading" role="status">Opening Research Agent...</main>

  const userCorpora = corpora.filter((corpus) => corpus.corpus_type === 'user_upload')
  const selectedCorpus = corpora.find((corpus) => corpus.id === selectedCorpusId)

  return (
    <div className={`app-shell ${signedInUser ? 'workspace-shell' : 'login-shell'}`}>
    <main className={signedInUser ? 'app workspace-layout' : 'app login-layout'}>
      {!signedInUser && <div className="login-intro">
        <span className="brand-mark">R<span>·</span>A</span>
        <p className="eyebrow">YOUR RESEARCH WORKSPACE</p>
        <h1>Research Starts with a Better Question.</h1>
        <p>Explore scientific literature and your own documents with answers grounded in sources you can inspect.</p>
        <div className="intro-features">
          <span>01 <strong>Ask across your corpora</strong></span>
          <span>02 <strong>Bring your own documents</strong></span>
          <span>03 <strong>Trace answers to sources</strong></span>
        </div>
      </div>}

      {signedInUser && <aside className="sidebar">
        <div className="sidebar-brand"><span className="brand-mark">R<span>·</span>A</span><strong>Research Agent</strong></div>
        <p className="sidebar-label">WORKSPACE</p>
        <nav className="workspace-tabs" aria-label="Workspace pages" inert={isDeletingAccount}>
          <button type="button" className={activeTab === 'ask' ? 'active' : ''} aria-current={activeTab === 'ask' ? 'page' : undefined} onClick={() => setActiveTab('ask')}>Ask</button>
          <button type="button" className={activeTab === 'manage' ? 'active' : ''} aria-current={activeTab === 'manage' ? 'page' : undefined} onClick={() => setActiveTab('manage')}>My Corpora</button>
          <button type="button" className={activeTab === 'settings' ? 'active' : ''} aria-current={activeTab === 'settings' ? 'page' : undefined} onClick={() => setActiveTab('settings')}>Settings</button>
        </nav>
        <div className="sidebar-account"><span title={signedInUser}>{signedInUser}</span><button type="button" disabled={isDeletingAccount} onClick={handleSignOut}>Sign out</button></div>
      </aside>}

      <div className={signedInUser ? 'workspace-content' : 'login-content'}>
      {signedInUser && activeTab === 'settings' && <>
        <header className="page-header"><p className="eyebrow">ACCOUNT</p><h1>Settings</h1><p>Manage your Research Agent account.</p></header>
        <section className="corpora-card account-settings" aria-labelledby="delete-account-heading">
          <h2 id="delete-account-heading">Delete account</h2>
          <p>Signed in as <strong>{signedInUser}</strong></p>
          <p>This permanently deletes your account, private corpora, and uploaded documents. This cannot be undone.</p>
          <p>Finish any uploads first and close other Research Agent tabs. Keep this page open until deletion completes. Logs, backups, and retained file versions may remain as described in our <a href="/privacy-policy/index.html" target="_blank" rel="noopener noreferrer">privacy policy</a>.</p>
          <form onSubmit={handleDeleteAccount}>
            <label htmlFor="delete-account-confirmation">Type DELETE to confirm</label>
            <input id="delete-account-confirmation" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} autoComplete="off" spellCheck={false} disabled={isDeletingAccount} />
            <button className="delete-account-button" type="submit" disabled={deleteConfirmation !== 'DELETE' || isDeletingAccount || isUploading || isAsking || !!deletingCorpusId || !!deletingDocumentId}>{isDeletingAccount ? 'Deleting account…' : 'Permanently delete account'}</button>
          </form>
          {(isUploading || isAsking || !!deletingCorpusId || !!deletingDocumentId) && <p role="status">Wait for your current operation to finish before deleting your account.</p>}
          <p role="status" aria-live="polite">{accountMessage}</p>
        </section>
      </>}
      {signedInUser && activeTab !== 'settings' && <header className="page-header"><p className="eyebrow">RESEARCH WORKSPACE</p><h1>{activeTab === 'ask' ? 'Ask Research Agent' : 'My Corpora'}</h1><p>{activeTab === 'ask' ? 'An arXiv corpus is available for searching by default. Select a corpus from Your Corpora, then ask about findings, methods, themes, or other information contained in it. Click “Ask” to run a RAG semantic search and generate an answer grounded in the most relevant sources. To create a corpus from your own uploaded documents, click “My Corpora” in the left sidebar, where you can also manage and edit your corpora.' : 'Organize and explore your research documents and manage the corpora used by Research Agent.'}</p></header>}
      {signedInUser && activeTab === 'manage' && <nav className="corpora-subtabs" aria-label="Corpus management">
        <button type="button" className={corporaTab === 'manage' ? 'active' : ''} onClick={() => setCorporaTab('manage')}>Manage Corpora</button>
        <button type="button" className={corporaTab === 'create' ? 'active' : ''} onClick={() => setCorporaTab('create')}>Create Corpus</button>
      </nav>}
      {!signedInUser && <section className="auth-card">
        <p className="eyebrow">WELCOME TO RESEARCH AGENT</p>
        <h2>{authMode === 'signIn' ? 'Sign In to Continue' : authMode === 'signUp' ? 'Create Your Account' : authMode === 'resetPassword' || authMode === 'confirmResetPassword' ? 'Reset Your Password' : 'Confirm Your Email'}</h2>
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
      </section>}

      {signedInUser && activeTab === 'ask' && (
        <section className="corpora-card">
          <div className="corpora-heading">
            <div>
              <h2>Your Corpora</h2>
              <p>{corporaMessage}</p>
            </div>
          </div>
          {corpora.length > 0 && (
            <ul className="available-corpora-list">
              {corpora.map((corpus) => (
                <li className={selectedCorpusId === corpus.id ? 'active' : ''} key={corpus.id}>
                  <button type="button" onClick={() => {
                    setSelectedCorpusId(corpus.id)
                    setRagAnswer(null)
                    setRagMessage('')
                  }}>
                    <strong>{corpusDisplayName(corpus)}</strong>
                    <span>{corpusTypeLabel(corpus.corpus_type)} · {corpusDocumentCount(corpus)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {signedInUser && activeTab === 'manage' && corporaTab === 'manage' && (
        <section className="corpora-card">
          <div className="corpora-heading">
            <div>
              <h2>Edit Existing Corpus Documents</h2>
              <p>Select a corpus from Your Corpora on the right, then add, preview, or remove its documents. Click “Save Changes” to apply your updates.</p>
            </div>
          </div>
          {isLoadingCorpora ? <p className="loading-corpora" role="status">Loading your corpora…</p> : userCorpora.length === 0 ? <div className="empty-corpora">
            <span className="empty-corpora-icon">+</span>
            <h2>Create Your First Corpus</h2>
            <p>A corpus is a collection of documents that Research Agent can search when answering your questions.</p>
            <ol>
              <li>Name your corpus.</li>
              <li>Add one or more PDF, TXT, or Markdown files.</li>
              <li>Create it, then ask questions from the Ask page.</li>
            </ol>
            <button type="button" onClick={() => setCorporaTab('create')}>Create a corpus</button>
          </div> : <div className="manage-corpora-layout">
          <div className="corpus-document-editor">
          <h3>{userCorpora.find((corpus) => corpus.id === documentCorpusId)?.name ?? 'Select a Corpus'}</h3>
          <form className="add-files-form" onSubmit={handleSaveCorpusChanges}>
            <input
              className="file-input"
              id="additional-files"
              key={additionalFiles.length === 0 ? 'empty' : 'selected'}
              type="file"
              accept=".pdf,.txt,.md"
              multiple
              onChange={(event) =>
                setAdditionalFiles(Array.from(event.target.files ?? []))
              }
              disabled={!documentCorpusId || isUploading}
            />
            <div className="document-grid">
              <label className="add-document-tile" htmlFor="additional-files"><span>+</span><strong>Add files</strong></label>
              {additionalFiles.map((file, index) => <LocalFileTile
                key={`${file.name}-${file.lastModified}-${index}`}
                file={file}
                onRemove={() => setAdditionalFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                onOpen={setOpenPreview}
              />)}
              {documents.map((document) => {
                const pendingDeletion = pendingDeletionIds.includes(document.document_id)
                return <article className={`document-tile${pendingDeletion ? ' pending-deletion' : ''}`} key={document.document_id}>
                  <button type="button" className={pendingDeletion ? 'undo-deletion' : 'remove-document'} aria-label={pendingDeletion ? `Keep ${document.filename}` : `Remove ${document.filename}`} disabled={deletingDocumentId === document.document_id || (!pendingDeletion && document.status === 'processing')} onClick={() => toggleDocumentDeletion(document.document_id)}>{pendingDeletion ? 'Undo' : <DeleteIcon />}</button>
                  <button type="button" className="preview-trigger" disabled={pendingDeletion || previewLoadingId === document.document_id} onClick={() => void handleOpenStoredDocument(document)}>
                    <div className="document-preview">
                      <FileTypeIcon filename={document.filename} />
                    </div>
                    <strong>{document.filename}</strong><span>{pendingDeletion ? 'Will be deleted' : previewLoadingId === document.document_id ? 'Opening…' : document.status}</span>
                  </button>
                </article>
              })}
            </div>
            <button
              type="submit"
              disabled={!documentCorpusId || (additionalFiles.length === 0 && pendingDeletionIds.length === 0) || isUploading}
            >
              {isUploading ? 'Saving Changes...' : 'Save Changes'}
            </button>
            <p role="status">{addFilesMessage}</p>
          </form>
          <p role="status">{documentsMessage}</p>
          </div>
          <aside className="corpus-list-panel">
            <h3>Your Corpora</h3>
            <ul>
              {userCorpora.map((corpus) => <li className={documentCorpusId === corpus.id ? 'active' : ''} key={corpus.id}>
                <button type="button" className="select-corpus" onClick={() => handleDocumentCorpusChange(corpus.id)}>
                  {corpus.name}
                  <span className="corpus-document-count">{corpusDocumentCount(corpus)}</span>
                </button>
                <button type="button" className="delete-corpus" aria-label={`Delete ${corpus.name}`} disabled={deletingCorpusId === corpus.id} onClick={() => void handleDeleteCorpus(corpus)}><DeleteIcon /></button>
              </li>)}
            </ul>
          </aside>
          </div>}
        </section>
      )}

      {signedInUser && activeTab === 'manage' && corporaTab === 'create' && (
        <form className="corpora-card create-corpus-form" onSubmit={handleCreateAndUpload}>
          <div className="corpora-heading">
            <div>
              <h2>Create a Document Corpus</h2>
              <p>Name the corpus and add one or more PDF, TXT, or Markdown files. Click “Create Corpus and Upload Files” to create the corpus.</p>
            </div>
          </div>

          <label htmlFor="new-corpus-name">Corpus name</label>
          <input
            id="new-corpus-name"
            value={newCorpusName}
            onChange={(event) => setNewCorpusName(event.target.value)}
            placeholder="My research documents"
            maxLength={100}
            disabled={isUploading}
            required
          />

          <div className="document-grid pending-grid">
            <label className="add-document-tile" htmlFor="document-file"><span>+</span><strong>Choose files</strong></label>
            {documentFiles.map((file, index) => <LocalFileTile
              key={`${file.name}-${file.lastModified}-${index}`}
              file={file}
              onRemove={() => setDocumentFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}
              onOpen={setOpenPreview}
            />)}
          </div>

          <input
            className="file-input"
            id="document-file"
            key={documentFiles.length === 0 ? 'empty' : 'selected'}
            type="file"
            accept=".pdf,.txt,.md"
            multiple
            onChange={(event) =>
              setDocumentFiles(Array.from(event.target.files ?? []))
            }
            disabled={isUploading}
          />

          <p>PDF, TXT, or Markdown. Select one or more files, up to 5 MB each.</p>

          <button
            type="submit"
            disabled={isUploading || !newCorpusName.trim() || documentFiles.length === 0}
          >
            {isUploading ? 'Creating Corpus...' : 'Create Corpus and Upload Files'}
          </button>

          <p role="status">{uploadMessage}</p>
        </form>
      )}

      {signedInUser && activeTab === 'ask' && (
        <form className="question-form" onSubmit={handleSubmit}>
          <div className="selected-corpus-field">
            <span>Selected Corpus:</span>
            <strong>{selectedCorpus ? corpusDisplayName(selectedCorpus) : 'Select a corpus above'}</strong>
          </div>
          <label htmlFor="question">Research Question</label>
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

      {signedInUser && activeTab === 'ask' && ragAnswer && (
        <section className="answer-card">
          <h2>Answer</h2>
          <p className="answer-text">{ragAnswer.answer}</p>

          {(['cited', 'additional'] as const).map((referenceType) => {
            const sources = ragAnswer.sources.filter((source) =>
              (source.reference_type ?? 'cited') === referenceType)
            if (!sources.length) return null
            return (
              <div key={referenceType}>
                <h3>{referenceType === 'cited' ? 'Sources' : 'Additional relevant sources'}</h3>
                <ol>
                  {sources.map((source) => (
                    <li value={source.number} key={`${source.number}-${source.document_id}`}>
                      {source.source_url ? (
                        <a href={source.source_url} target="_blank" rel="noreferrer">
                          {source.title}
                        </a>
                      ) : (
                        source.title
                      )}
                      <span>Distance: {source.distance === null ? 'Overview' : source.distance.toFixed(4)}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )
          })}
        </section>
      )}
      {openPreview && <div className="preview-backdrop" role="presentation" onMouseDown={closePreview}>
        <section className="preview-modal" role="dialog" aria-modal="true" aria-label={`Preview ${openPreview.name}`} onMouseDown={(event) => event.stopPropagation()}>
          <header><h2>{openPreview.name}</h2><button type="button" aria-label="Close preview" onClick={closePreview}>×</button></header>
          {openPreview.isPdf
            ? <iframe title={openPreview.name} src={openPreview.url} />
            : <pre className="text-preview">{openPreview.text}</pre>}
        </section>
      </div>}
      </div>
    </main>
    <footer className="app-footer">
      <ProjectLinks />
    </footer>
    </div>
  )
}

export default App
