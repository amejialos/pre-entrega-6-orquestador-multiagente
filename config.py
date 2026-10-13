"""Configuración de Pinecone y embeddings (copiada de la Pre-entrega 4).

Es el único módulo, junto con `providers.py`, que lee variables de entorno.

El resto del código recibe el cliente de Pinecone, el índice y los embeddings por
parámetro (inyección de dependencias), así los tests los reemplazan por fakes sin red.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings

# Dimensión del índice. text-embedding-3-small devuelve 1536 de forma nativa y
# gemini-embedding-001 la acepta como `dimensions` (Matryoshka). Si el modelo y el
# índice no coinciden, Pinecone rechaza el upsert: por eso es una sola constante.
DIMENSION = 1536
METRIC = "cosine"
CLOUD = "aws"
REGION = "us-east-1"  # la región del plan gratuito (Starter) de Pinecone Serverless

DEFAULT_INDEX_NAME = "pre-entrega-4"
DEFAULT_NAMESPACE = "caudal-docs"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"


@dataclass(frozen=True)
class Settings:
    pinecone_api_key: str
    index_name: str
    namespace: str
    embedding_model: str
    openai_api_key: str
    openai_base_url: str | None


def _require_env(var: str) -> str:
    value = os.environ.get(var, "").strip()
    if not value:
        raise ValueError(f"Falta la variable de entorno {var}. Copiá .env.example a .env y completala.")
    return value


def load_settings() -> Settings:
    """Lee el .env (si existe) y devuelve la configuración validada."""
    load_dotenv()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    default_model = GEMINI_EMBEDDING_MODEL if base_url else OPENAI_EMBEDDING_MODEL
    return Settings(
        pinecone_api_key=_require_env("PINECONE_API_KEY"),
        index_name=os.environ.get("INDEX_NAME", "").strip() or DEFAULT_INDEX_NAME,
        namespace=os.environ.get("PINECONE_NAMESPACE", "").strip() or DEFAULT_NAMESPACE,
        embedding_model=os.environ.get("EMBEDDING_MODEL", "").strip() or default_model,
        openai_api_key=_require_env("OPENAI_API_KEY"),
        openai_base_url=base_url,
    )


def build_embeddings(settings: Settings) -> Embeddings:
    """OpenAIEmbeddings contra OpenAI o contra el endpoint compatible de Gemini."""
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        dimensions=DIMENSION,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        # Con un endpoint que no es OpenAI no se puede tokenizar con tiktoken antes de
        # mandar: el endpoint de Gemini espera texto, no listas de ids de tokens.
        check_embedding_ctx_length=settings.openai_base_url is None,
        # El endpoint de Gemini acepta como mucho 100 textos por pedido.
        chunk_size=100,
    )


def build_pinecone_client(settings: Settings):
    from pinecone import Pinecone

    return Pinecone(api_key=settings.pinecone_api_key)
