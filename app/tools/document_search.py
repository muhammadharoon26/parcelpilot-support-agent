"""Vector search over the policy corpus, scoped to the session's account and sorted by source authority."""

from functools import lru_cache

import chromadb

from app.config import AUTHORITY_LABELS, CHROMA_COLLECTION, CHROMA_PATH, GENERAL_SCOPE, SOURCE_NAMES

N_RESULTS = 8


@lru_cache(maxsize=1)
def _collection():
    if not CHROMA_PATH.exists():
        raise RuntimeError(f"{CHROMA_PATH} not found. Run: python scripts/ingest_documents.py")
    return chromadb.PersistentClient(path=str(CHROMA_PATH)).get_collection(CHROMA_COLLECTION)


def search(account_id: str, query: str) -> dict:
    """The scope filter is applied by Chroma, so another customer's agreement can never enter the result set."""
    results = _collection().query(
        query_texts=[query],
        n_results=N_RESULTS,
        where={"customer_scope": {"$in": [GENERAL_SCOPE, account_id]}},
    )

    chunks = [
        {
            "text": text,
            # The customer-facing name, never the filename. See config.SOURCE_NAMES.
            "source": SOURCE_NAMES[meta["source_file"]],
            "doc_type": meta["doc_type"],
            "status": meta["status"],
            "authority_rank": meta["authority_rank"],
            "authority": AUTHORITY_LABELS[meta["authority_rank"]],
            "applies_to": "this account only" if meta["customer_scope"] != GENERAL_SCOPE else "all customers",
        }
        for text, meta in zip(results["documents"][0], results["metadatas"][0])
    ]
    # Rank order, not similarity order: the model should read the governing source first.
    chunks.sort(key=lambda c: c["authority_rank"])

    return {
        "results": chunks,
        "resolution_rule": (
            "Lower authority_rank wins. A rank-1 customer agreement overrides general policy for this "
            "account on any point it addresses; where it is silent, fall back to the next rank. Never "
            "justify an answer with a deprecated (rank 5) source."
        ),
        "has_customer_agreement": any(c["authority_rank"] == 1 for c in chunks),
    }
