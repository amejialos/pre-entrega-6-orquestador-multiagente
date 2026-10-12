"""Carga y chunking de los documentos locales (copiado de la Pre-entrega 4).

Los vectores ya están en Pinecone (los subió la ingesta de la PE4); acá el chunking se
repite solo para armar el índice BM25 en memoria con los MISMOS `chunk_id`.

Lo usan los dos lados del sistema:
- la ingesta, para subir los chunks a Pinecone;
- el RAGSystem, para armar el índice BM25 en memoria.
Como el chunking es determinista (mismos archivos -> mismos chunks y mismos ids), los
dos lados ven exactamente los mismos `chunk_id` y el EnsembleRetriever puede fusionar
resultados de las dos fuentes sin duplicados.

Una "página" es una sección: un encabezado `## ` en Markdown o un elemento de
`secciones` en JSON. Es el equivalente a la página de un PDF y viaja en la metadata.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

DATA_DIR = Path(__file__).parent / "data"
# La consigna pide chunks de ~500-800 tokens. 700 deja margen para que una sección
# entera entre en un chunk; el solapamiento conserva contexto cuando hay que cortar.
CHUNK_SIZE_TOKENS = 700
CHUNK_OVERLAP_TOKENS = 100
SUPPORTED_SUFFIXES = (".md", ".json")

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Front matter mínimo `clave: valor` (sin depender de PyYAML)."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def _load_markdown(path: Path) -> list[Document]:
    meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
    doc_id = meta.get("doc_id") or path.stem
    title = meta.get("titulo") or doc_id
    category = meta.get("categoria") or "general"
    # Cada `## ` abre una página nueva; lo que hay antes (el `# título`) se descarta.
    parts = re.split(r"^## +", body, flags=re.MULTILINE)[1:]
    pages = []
    for page_number, part in enumerate(parts, start=1):
        heading, _, content = part.partition("\n")
        pages.append(
            Document(
                page_content=f"{title} - {heading.strip()}\n\n{content.strip()}",
                metadata={
                    "doc_id": doc_id,
                    "source": path.name,
                    "page": page_number,
                    "section": heading.strip(),
                    "category": category,
                    "title": title,
                },
            )
        )
    return pages


def _load_json(path: Path) -> list[Document]:
    data = json.loads(path.read_text(encoding="utf-8"))
    doc_id = data.get("doc_id") or path.stem
    title = data.get("titulo") or doc_id
    category = data.get("categoria") or "general"
    return [
        Document(
            page_content=f"{title} - {section['titulo']}\n\n{section['contenido'].strip()}",
            metadata={
                "doc_id": doc_id,
                "source": path.name,
                "page": page_number,
                "section": section["titulo"],
                "category": category,
                "title": title,
            },
        )
        for page_number, section in enumerate(data["secciones"], start=1)
    ]


def load_documents(data_dir: Path = DATA_DIR) -> list[Document]:
    """Una Document por página, en orden estable (archivos ordenados por nombre)."""
    pages: list[Document] = []
    for path in sorted(data_dir.iterdir()):
        if path.suffix == ".md":
            pages.extend(_load_markdown(path))
        elif path.suffix == ".json":
            pages.extend(_load_json(path))
    if not pages:
        raise ValueError(f"No encontré documentos {SUPPORTED_SUFFIXES} en {data_dir}.")
    return pages


def count_tokens(text: str) -> int:
    """Cuenta tokens con el tokenizador de OpenAI (cl100k_base)."""
    import tiktoken

    return len(tiktoken.get_encoding("cl100k_base").encode(text))


def split_documents(
    pages: list[Document],
    chunk_size: int = CHUNK_SIZE_TOKENS,
    chunk_overlap: int = CHUNK_OVERLAP_TOKENS,
    length_function: Callable[[str], int] | None = None,
) -> list[Document]:
    """Parte cada página con RecursiveCharacterTextSplitter y asigna ids estables.

    `chunk_size` se mide en tokens (tiktoken). Los tests pasan `length_function=len`
    para no depender de descargar el tokenizador.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=length_function or count_tokens,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks: list[Document] = []
    for page in pages:
        for index, text in enumerate(splitter.split_text(page.page_content)):
            meta = page.metadata
            chunk_id = f"{meta['doc_id']}-p{meta['page']}-c{index}"
            chunks.append(
                Document(
                    id=chunk_id,
                    page_content=text,
                    metadata={**meta, "chunk_id": chunk_id, "chunk_index": index},
                )
            )
    return chunks


def load_chunks(data_dir: Path = DATA_DIR, **split_kwargs) -> list[Document]:
    return split_documents(load_documents(data_dir), **split_kwargs)
