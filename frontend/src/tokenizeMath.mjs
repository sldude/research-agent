// Parse math first so bracketed numbers inside equations never become citations.
export function tokenizeMath(text) {
  const pattern = /(?<!\\)(\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$((?:\\.|[^$\n\\])+)\$)/g
  const tokens = []
  let start = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > start) tokens.push({ kind: 'text', text: text.slice(start, match.index) })
    tokens.push({ kind: 'math', text: match[2] ?? match[3] ?? match[4] ?? match[5],
      display: match[2] !== undefined || match[3] !== undefined, raw: match[0] })
    start = match.index + match[0].length
  }
  if (start < text.length) tokens.push({ kind: 'text', text: text.slice(start) })
  return tokens
}
