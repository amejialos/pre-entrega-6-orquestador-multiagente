"""Recuperador híbrido (copiado de la Pre-entrega 4): Pinecone + BM25.

Es la "fuente externa" del Agente Investigador: la herramienta `buscar_documentacion`
llama a `RAGSystem.retrieve`.

`RAGSystem` encapsula un `EnsembleRetriever` que fusiona las dos listas con Reciprocal
Rank Fusion (RRF): cada documento suma `peso / (c + posición)` por cada lista en la que
aparece. Así un chunk que está arriba en las dos gana, y uno que solo encuentra BM25
(un código de error exacto como `CAU-417`) igual puede entrar en el top-5.

Uso rápido: uv run python rag_system.py "¿Qué es el error CAU-417?"
"""

import re
import sys
import unicodedata
from typing import Any, Literal

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore

from corpus import load_chunks

Mode = Literal["hibrido", "vector", "bm25"]
TOP_K = 5
# Pinecone guarda todos los números de la metadata como float (page=1 vuelve como 1.0);
# BM25 los conserva como int. Se normalizan para que ambos caminos devuelvan lo mismo.
_INT_FIELDS = ("page", "chunk_index")
DEFAULT_WEIGHTS = (0.5, 0.5)  # (vector, bm25)

# Stopwords mínimas en español: BM25 ya penaliza términos frecuentes con el IDF, pero
# en un corpus chico "de", "la" o "que" igual meten ruido en el puntaje.
_STOPWORDS = frozenset(
    "a al algo como con cual cuando de del el en es esa ese eso esta este esto hay la las lo los mas me mi "
    "mis no o para pero por que se si sin sobre su sus tu un una uno unos y ya yo".split()
)
# Conserva palabras con guion o guion bajo como un único token: cau-417, caudal-prof,
# tamano_lote. Para BM25 esos son justamente los términos que más discriminan.
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def tokenize_es(text: str) -> list[str]:
    """Minúsculas, sin tildes, sin stopwords."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return [token for token in _TOKEN.findall(ascii_text) if token not in _STOPWORDS]


def _normalize(doc: Document) -> Document:
    for field in _INT_FIELDS:
        if isinstance(doc.metadata.get(field), float):
            doc.metadata[field] = int(doc.metadata[field])
    return doc


class RAGSystem:
    """Recibe una consulta y devuelve los top-5 chunks combinando léxico y semántico."""

    def __init__(
        self,
        vector_store: VectorStore,
        chunks: list[Document],
        k: int = TOP_K,
        weights: tuple[float, float] = DEFAULT_WEIGHTS,
    ) -> None:
        if not chunks:
            raise ValueError("BM25 necesita los chunks en memoria y la lista está vacía.")
        self.k = k
        self.vector_retriever: BaseRetriever = vector_store.as_retriever(search_kwargs={"k": k})
        self.bm25_retriever = BM25Retriever.from_documents(chunks, k=k, preprocess_func=tokenize_es)
        # id_key: el EnsembleRetriever deduplica por esta clave de la metadata. Los chunks
        # de Pinecone y los de BM25 comparten chunk_id, así que un mismo chunk encontrado
        # por las dos vías se fusiona en un único resultado con puntaje sumado.
        self.ensemble = EnsembleRetriever(
            retrievers=[self.vector_retriever, self.bm25_retriever],
            weights=list(weights),
            id_key="chunk_id",
        )

    def retrieve(self, query: str, mode: Mode = "hibrido") -> list[Document]:
        """Top-k para la consulta. `mode` permite comparar contra cada recuperador solo."""
        if not query.strip():
            raise ValueError("La consulta está vacía.")
        retriever = {"hibrido": self.ensemble, "vector": self.vector_retriever, "bm25": self.bm25_retriever}[mode]
        # El ensemble devuelve la unión de las dos listas (hasta 2k): nos quedamos con k.
        return [_normalize(doc) for doc in retriever.invoke(query)[: self.k]]

    @classmethod
    def from_pinecone(
        cls,
        index: Any,
        embeddings: Embeddings,
        namespace: str,
        chunks: list[Document] | None = None,
        **kwargs: Any,
    ) -> "RAGSystem":
        from langchain_pinecone import PineconeVectorStore

        store = PineconeVectorStore(index=index, embedding=embeddings, text_key="text", namespace=namespace)
        return cls(store, chunks if chunks is not None else load_chunks(), **kwargs)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "RAGSystem":
        from config import build_embeddings, build_pinecone_client, load_settings

        settings = load_settings()
        index = build_pinecone_client(settings).Index(settings.index_name)
        return cls.from_pinecone(index, build_embeddings(settings), settings.namespace, **kwargs)


def main() -> None:
    query = " ".join(sys.argv[1:]) or "¿Qué significa el error CAU-417?"
    rag = RAGSystem.from_env()
    print(f"Consulta: {query}\n")
    for position, doc in enumerate(rag.retrieve(query), start=1):
        meta = doc.metadata
        snippet = doc.page_content.replace("\n", " ")[:110]
        print(f"{position}. [{meta['doc_id']} | pág. {meta['page']} | {meta['category']}] {snippet}...")


if __name__ == "__main__":
    main()
