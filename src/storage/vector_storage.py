from pathlib import Path
from typing import Dict, List, Optional

from src.text_utils import token_counter


class VectorStorage:
    """A small retrieval store with optional vector-like behavior.

    The original project depended on ChromaDB and sentence-transformers. Those
    packages are useful, but they make the course project hard to run on a clean
    machine. This class keeps the same public API and implements a deterministic
    sparse-token fallback, so tests and evaluation can run offline.
    """

    def __init__(self, persist_dir: str, embedding_model_name: str = "offline-bm25"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model_name = embedding_model_name
        self.collection_name: Optional[str] = None
        self.collections: Dict[str, Dict[str, Dict]] = {}

    def create_collection(self, collection_name: str, recreate: bool = False):
        if recreate or collection_name not in self.collections:
            self.collections[collection_name] = {}
        self.collection_name = collection_name

    def add_documents(self, ids: List[str], documents: List[str], metadatas: Optional[List[Dict]] = None):
        if self.collection_name is None:
            raise ValueError("请先调用 create_collection")
        metadatas = metadatas or [{} for _ in documents]
        collection = self.collections[self.collection_name]
        for doc_id, document, metadata in zip(ids, documents, metadatas):
            collection[doc_id] = {
                "document": document,
                "metadata": metadata,
                "tokens": dict(token_counter(document)),
            }

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        if self.collection_name is None:
            raise ValueError("请先创建 collection")
        q_tokens = token_counter(query)
        hits = []
        for doc_id, payload in self.collections[self.collection_name].items():
            score = 0.0
            for token, count in q_tokens.items():
                if token in payload["tokens"]:
                    score += (len(token) + 1) * count * payload["tokens"][token]
            if score <= 0:
                continue
            hits.append(
                {
                    "id": doc_id,
                    "document": payload["document"],
                    "metadata": payload["metadata"],
                    "distance": 1.0 / (score + 1.0),
                }
            )
        hits.sort(key=lambda item: item["distance"])
        return hits[:top_k]

    def delete_collection(self, collection_name: str):
        self.collections.pop(collection_name, None)
        if self.collection_name == collection_name:
            self.collection_name = None

    def list_collections(self) -> List[str]:
        return sorted(self.collections)

