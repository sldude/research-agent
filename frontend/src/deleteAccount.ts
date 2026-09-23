type AccountCorpus = { id: string; owner_id: string | null }

// Keep the identity available until every private corpus has been removed.
export async function deleteAccount({ apiUrl, userId, getToken, removeUser, request = fetch }: {
  apiUrl: string
  userId: string
  getToken: () => Promise<string>
  removeUser: () => Promise<void>
  request?: typeof fetch
}) {
  async function call(path: string, method = 'GET') {
    const response = await request(`${apiUrl}${path}`, {
      method,
      headers: { Authorization: `Bearer ${await getToken()}` },
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new Error(typeof body?.detail === 'string' ? body.detail : `Account cleanup failed (${response.status}).`)
    }
    return response
  }

  const corpora: AccountCorpus[] = await (await call('/api/corpora')).json()
  for (const corpus of corpora.filter((item) => item.owner_id === userId)) {
    await call(`/api/corpora/${encodeURIComponent(corpus.id)}`, 'DELETE')
  }
  // Catch corpora created by another open tab during cleanup.
  const remaining: AccountCorpus[] = await (await call('/api/corpora')).json()
  if (remaining.some((item) => item.owner_id === userId)) {
    throw new Error('Your corpora changed during deletion. Close other tabs and retry.')
  }
  await removeUser()
}
