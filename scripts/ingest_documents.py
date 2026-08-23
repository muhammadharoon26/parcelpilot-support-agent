"""Chunk the six source PDFs by heading and store them in Chroma with the authority metadata retrieval depends on."""

import re
import sys
from pathlib import Path

import chromadb
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CHROMA_COLLECTION, CHROMA_PATH, DOCUMENT_REGISTRY, PROCESSED_DIR, RAW_DIR

# Numbered headings ("1. Order cancellation") and known-issue headings ("KI-208 - ...") both start sections.
HEADING = re.compile(r"^(?:\d+\.\s+\S|KI-\d+\s*[-–])", re.MULTILINE)
MAX_CHARS = 1800


def extract_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def chunk_by_heading(text: str) -> list[str]:
    """Split on section headings so each chunk is a self-contained rule, then hard-split anything oversized."""
    starts = [m.start() for m in HEADING.finditer(text)]
    bounds = [0] + starts if starts and starts[0] != 0 else starts or [0]
    sections = [text[a:b].strip() for a, b in zip(bounds, bounds[1:] + [len(text)])]

    chunks: list[str] = []
    for section in sections:
        if not section:
            continue
        while len(section) > MAX_CHARS:
            cut = section.rfind("\n", 0, MAX_CHARS) or MAX_CHARS
            chunks.append(section[:cut].strip())
            section = section[cut:].strip()
        chunks.append(section)
    return chunks


def main() -> None:
    missing = [name for name in DOCUMENT_REGISTRY if not (RAW_DIR / name).exists()]
    if missing:
        raise SystemExit(f"Missing PDFs in data/raw/: {missing}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    if CHROMA_COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(CHROMA_COLLECTION)
    collection = client.create_collection(CHROMA_COLLECTION)

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    for source_file, meta in DOCUMENT_REGISTRY.items():
        # Prepending the filename keeps the source visible to the embedding, which helps
        # queries that name a document ("what does the Northstar agreement say").
        header = source_file.replace("_", " ").removesuffix(".pdf")
        for i, chunk in enumerate(chunk_by_heading(extract_text(RAW_DIR / source_file))):
            documents.append(f"[{header}]\n{chunk}")
            metadatas.append({"source_file": source_file, **meta})
            ids.append(f"{source_file}::{i}")
        print(f"{source_file}: {sum(1 for m in ids if m.startswith(source_file))} chunks")

    collection.add(documents=documents, metadatas=metadatas, ids=ids)
    print(f"indexed {len(documents)} chunks into {CHROMA_PATH}")


if __name__ == "__main__":
    main()
