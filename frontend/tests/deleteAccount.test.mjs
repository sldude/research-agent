import assert from 'node:assert/strict'
import { test } from 'node:test'
import { deleteAccount } from '../src/deleteAccount.ts'

test('deletes only owned corpora before deleting the identity', async () => {
  const calls = []
  const responses = [Response.json([
    { id: 'shared', owner_id: null }, { id: 'private/a', owner_id: 'me' },
    { id: 'other', owner_id: 'someone-else' },
  ]), new Response(null, { status: 204 }), Response.json([])]
  await deleteAccount({
    apiUrl: 'https://api.example', userId: 'me', getToken: async () => 'token',
    request: async (url, options) => {
      assert.equal(options.headers.Authorization, 'Bearer token')
      calls.push(`${options.method} ${url}`)
      return responses.shift()
    },
    removeUser: async () => { calls.push('delete identity') },
  })
  assert.deepEqual(calls, [
    'GET https://api.example/api/corpora',
    'DELETE https://api.example/api/corpora/private%2Fa',
    'GET https://api.example/api/corpora', 'delete identity',
  ])
})

for (const status of [409, 502]) {
  test(`cleanup failure (${status}) preserves the identity for retry`, async () => {
    let removed = false
    let count = 0
    await assert.rejects(deleteAccount({
      apiUrl: '', userId: 'me', getToken: async () => 'token',
      request: async () => ++count === 1
        ? Response.json([{ id: 'private', owner_id: 'me' }])
        : Response.json({ detail: 'Cleanup failed' }, { status }),
      removeUser: async () => { removed = true },
    }), /Cleanup failed/)
    assert.equal(removed, false)
  })
}

test('corpora created during cleanup prevent identity deletion', async () => {
  let removed = false
  let count = 0
  await assert.rejects(deleteAccount({
    apiUrl: '', userId: 'me', getToken: async () => 'token',
    request: async () => Response.json(++count === 1 ? [] : [{ id: 'new', owner_id: 'me' }]),
    removeUser: async () => { removed = true },
  }), /changed during deletion/)
  assert.equal(removed, false)
})

test('identity deletion failures are reported for retry', async () => {
  await assert.rejects(deleteAccount({
    apiUrl: '', userId: 'me', getToken: async () => 'token',
    request: async () => Response.json([]),
    removeUser: async () => { throw new Error('Identity deletion unavailable') },
  }), /Identity deletion unavailable/)
})
