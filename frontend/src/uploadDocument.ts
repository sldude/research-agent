export const MAX_UPLOAD_BYTES = 5_000_000

type UploadAuthorization = {
  document_id: string
  upload_url: string
  fields: Record<string, string>
}

export async function uploadDocument(
  apiUrl: string,
  corpusId: string,
  accessToken: string,
  file: File,
): Promise<UploadAuthorization> {
  const authorization = await fetch(
    `${apiUrl}/api/corpora/${encodeURIComponent(corpusId)}/documents`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ filename: file.name, size_bytes: file.size }),
    },
  )
  if (!authorization.ok) {
    const body = await authorization.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string'
      ? body.detail : `Could not authorize ${file.name} (${authorization.status}).`)
  }
  const upload: UploadAuthorization = await authorization.json()
  const form = new FormData()
  for (const [key, value] of Object.entries(upload.fields)) form.append(key, value)
  form.append('file', file)

  // The signed fields authorize S3. Do not send the Cognito token or set the
  // multipart Content-Type: the browser supplies its boundary.
  let response: Response
  try {
    response = await fetch(upload.upload_url, { method: 'POST', body: form })
  } catch {
    throw new Error(`Could not confirm the upload of ${file.name}. Check its status before retrying. Unfinished uploads can be removed after 10 minutes.`)
  }
  if (!response.ok) {
    throw new Error(`${file.name} failed to upload (${response.status}). Try again. Unfinished uploads can be removed after 10 minutes.`)
  }
  return upload
}
