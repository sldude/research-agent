export function tokenizeMath(text: string): Array<
  { kind: 'text'; text: string } |
  { kind: 'math'; text: string; display: boolean; raw: string }
>
