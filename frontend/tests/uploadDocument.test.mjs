import assert from 'node:assert/strict'
import { test } from 'node:test'
import { uploadDocument } from '../src/uploadDocument.ts'

test('authorizes through the API and sends signed fields and file to S3 without a token', async (t) => {
  const file = new File(['hello'], 'notes.txt')
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    return calls.length === 1
      ? Response.json({ document_id: 'doc', upload_url: 'https://s3.example/upload', fields: { key: 'uploads/doc', policy: 'signed' } })
      : new Response(null, { status: 204 })
  })
  const result = await uploadDocument('https://api.example', 'corpus', 'token', file)
  assert.equal(result.document_id, 'doc')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token')
  assert.deepEqual(JSON.parse(calls[0].options.body), { filename: 'notes.txt', size_bytes: 5 })
  assert.equal(calls[1].url, 'https://s3.example/upload')
  assert.equal(calls[1].options.headers, undefined)
  assert.deepEqual([...calls[1].options.body.keys()], ['key', 'policy', 'file'])
  assert.equal(await calls[1].options.body.get('file').text(), 'hello')
})

test('authorization rejection never sends a file to S3', async (t) => {
  const fetchMock = t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Not your corpus.' }, { status: 403 }))
  await assert.rejects(uploadDocument('https://api.example', 'corpus', 'token', new File(['a'], 'a.txt')), /Not your corpus/)
  assert.equal(fetchMock.mock.callCount(), 1)
})

test('S3 rejection is reported as upload failure', async (t) => {
  let count = 0
  t.mock.method(globalThis, 'fetch', async () => ++count === 1
    ? Response.json({ document_id: 'doc', upload_url: 'https://s3.example/upload', fields: {} })
    : new Response(null, { status: 403 }))
  await assert.rejects(uploadDocument('https://api.example', 'corpus', 'token', new File(['a'], 'a.txt')), /failed to upload \(403\)/)
})
