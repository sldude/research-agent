from pathlib import Path
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError
from app.clients.embeddings import MODEL_ID, embed_text
from app.database.repository import DynamoRepository


def extract_text(filename: str, contents: bytes) -> str:
    extension = Path(filename).suffix.lower()

    if extension in {".txt", ".md"}:
        try:
            text = contents.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("The file must contain UTF-8 text.") from error

    elif extension == ".pdf":
        try:
            reader = PdfReader(BytesIO(contents))

            if reader.is_encrypted:
                raise ValueError(
                    "Encrypted PDFs are not supported. "
                    "Upload an unencrypted copy."
                )

            pages = [
                page.extract_text() or ""
                for page in reader.pages
            ]
            text = "\n\n".join(pages)

        except PyPdfError as error:
            raise ValueError(
                "Could not read the PDF. It may be damaged or unsupported."
            ) from error

    else:
        raise ValueError("Supported files: TXT, Markdown, and PDF.")

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    if not text:
        if extension == ".pdf":
            raise ValueError(
                "The PDF contains no extractable text. "
                "If it is scanned, it needs OCR first."
            )
        raise ValueError("The document contains no text.")

    return text


def split_text(
    text: str,
    chunk_size: int = 2000,
    overlap: int = 200,
) -> list[str]:
    if not 0 <= overlap < chunk_size:
        raise ValueError("Overlap must be nonnegative and smaller than chunk size.")

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        start = end - overlap

    return chunks

def ingest_document(
    *,
    corpus_id: str,
    document_id: str,
    filename: str,
    contents: bytes,
    owner_id: str,
    repository: DynamoRepository | None = None,
) -> dict[str, str | int]:
    repository = repository or DynamoRepository()

    corpus = repository.get_corpus(corpus_id)
    if corpus is None:
        raise ValueError("Corpus not found.")

    if corpus.owner_id != owner_id:
        raise PermissionError("The document does not belong to this corpus owner.")

    text = extract_text(filename, contents)
    chunks = split_text(text)

    for index, chunk in enumerate(chunks):
        embedding = embed_text(chunk)

        repository.put_document(
            corpus_id=corpus_id,
            source="upload",
            external_id=document_id,
            chunk_index=index,
            title=filename,
            abstract=None,
            authors=[],
            publication_date=None,
            source_url=None,
            license_url=None,
            content=chunk,
            embedding=embedding,
            embedding_model=MODEL_ID,
        )

    return {
        "document_id": document_id,
        "corpus_id": corpus_id,
        "chunks_saved": len(chunks),
        "status": "ready",
    }