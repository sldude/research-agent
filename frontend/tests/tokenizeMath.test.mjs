import test from 'node:test'
import assert from 'node:assert/strict'
import { tokenizeMath } from '../src/tokenizeMath.mjs'

test('math keeps bracketed numbers separate from prose citations', () => {
  const tokens = tokenizeMath('Result $x[1] + y$ [2]')
  assert.equal(tokens[1].text, 'x[1] + y')
  assert.equal(tokens[1].kind, 'math')
  assert.equal(tokens[2].text, ' [2]')
})

test('supports all four delimiter styles and multiline display math', () => {
  const tokens = tokenizeMath(String.raw`$x$ $$a
+ b$$ \(y\) \[z\]`).filter(token => token.kind === 'math')
  assert.deepEqual(tokens.map(token => token.display), [false, true, false, true])
  assert.deepEqual(tokens.map(token => token.text), ['x', 'a\n+ b', 'y', 'z'])
})

test('escaped dollars and incomplete expressions remain plain text', () => {
  const text = String.raw`Price \$5; unfinished $x`
  assert.deepEqual(tokenizeMath(text), [{ kind: 'text', text }])
})
