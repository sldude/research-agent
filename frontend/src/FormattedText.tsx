import { type ReactNode } from 'react'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { tokenizeMath } from './tokenizeMath.mjs'

type Props = {
  text: string
  renderCitation?: (number: number, key: string) => ReactNode
}

export function FormattedText({ text, renderCitation }: Props) {
  return <>{tokenizeMath(text).map((token, index) => {
    if (token.kind === 'math') {
      try {
        const html = katex.renderToString(token.text, {
          displayMode: token.display, throwOnError: true, trust: false,
          maxSize: 20, maxExpand: 1000, strict: 'ignore',
        })
        // Only KaTeX output is inserted as HTML; prose remains React text.
        return <span key={index} dangerouslySetInnerHTML={{ __html: html }} />
      } catch {
        return token.raw
      }
    }
    if (!renderCitation) return token.text
    return token.text.split(/(\[[1-9][0-9]*\])/g).map((part, partIndex) => {
      const key = `${index}-${partIndex}`
      return /^\[[1-9][0-9]*\]$/.test(part)
        ? renderCitation(Number(part.slice(1, -1)), key) : part
    })
  })}</>
}
