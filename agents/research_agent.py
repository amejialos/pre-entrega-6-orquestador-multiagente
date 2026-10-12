"""Agente de Búsqueda/Investigación.

Herramienta: `buscar_documentacion(consulta)`, búsqueda híbrida (Pinecone + BM25) sobre la
documentación de la librería ficticia Caudal que se indexó en la Pre-entrega 4. Devuelve
fragmentos con su fuente (`doc_id`, sección) para que la respuesta se pueda citar.

Contexto que recibe: SOLO la instrucción del supervisor. No necesita nada más para buscar.
"""

import asyncio
import json
from typing import Any, Protocol

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph

from agents.common import crear_agente_react, mensaje_de_contribucion, run_specialist
from state import OrchestratorState

NOMBRE = "researcher"
MAX_CHARS_FRAGMENTO = 900  # recorte por fragmento: alcanza para el dato y no infla el contexto

PROMPT = """Sos el Agente Investigador de un equipo de análisis. Tu única fuente es la
documentación oficial de la librería Caudal, que consultás con la herramienta
`buscar_documentacion`.

Reglas:
1. Buscá SIEMPRE antes de responder. Si la primera búsqueda no trae el dato, reformulá
   la consulta (otras palabras, el nombre del parámetro) y buscá de nuevo (máximo 3 búsquedas).
2. Respondé solo con lo que dicen los fragmentos. Citá la fuente de cada dato entre
   corchetes, así: [caudal-instalacion › Configuración]. Si un dato no aparece, decí
   explícitamente "no figura en la documentación"; nunca lo inventes.
3. Si la herramienta devuelve "error", informalo tal cual: no supongas el contenido.
4. No hagas cálculos: tu trabajo es extraer datos. Los cálculos los hace otro agente.
5. Respuesta breve, en español: una lista de datos con su valor numérico exacto y su fuente."""


class Retriever(Protocol):
    def retrieve(self, query: str) -> list[Document]: ...


def _fuente(doc: Document) -> str:
    meta = doc.metadata
    return f"{meta.get('doc_id', meta.get('source', '?'))} › {meta.get('section', '?')}"


def crear_herramienta_busqueda(retriever: Retriever) -> BaseTool:
    """Arma la herramienta sobre un recuperador inyectado (RAGSystem real o un fake)."""

    @tool(parse_docstring=True)
    async def buscar_documentacion(consulta: str) -> str:
        """Busca en la documentación oficial de Caudal (búsqueda híbrida semántica + palabras clave).

        Devuelve JSON con hasta 5 fragmentos: {"resultados": [{"fuente", "contenido"}]}.
        Si algo falla devuelve {"error": ..., "sugerencia": ...} en vez de fragmentos.

        Args:
            consulta: Qué buscar, en español y con los términos técnicos exactos si los conocés. Ejemplo: "tamaño de lote por defecto tamano_lote".
        """
        if not consulta.strip():
            return _json({"error": "consulta vacía", "sugerencia": "Indicá qué dato buscar."})
        try:
            # RAGSystem es síncrono (Pinecone + BM25): se corre en un hilo para no bloquear el loop.
            docs = await asyncio.to_thread(retriever.retrieve, consulta)
        except Exception as exc:  # la caída de la fuente vuelve como dato, no rompe el grafo
            return _json(
                {
                    "error": f"la búsqueda falló: {type(exc).__name__}: {exc}"[:300],
                    "sugerencia": "Informá que la documentación no está disponible; no inventes datos.",
                }
            )
        if not docs:
            return _json({"resultados": [], "sugerencia": "Sin resultados: reformulá la consulta."})
        return _json(
            {
                "resultados": [
                    {"fuente": _fuente(d), "contenido": d.page_content[:MAX_CHARS_FRAGMENTO]} for d in docs
                ]
            }
        )

    return buscar_documentacion


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def build_research_agent(model: BaseChatModel, search_tool: BaseTool) -> CompiledStateGraph:
    """Agente ReAct con una sola herramienta: la búsqueda en la documentación."""
    return crear_agente_react(model, [search_tool], PROMPT, NOMBRE)


def make_research_node(agent: CompiledStateGraph):
    """Nodo del orquestador: corre al investigador con la instrucción del supervisor y nada más."""

    async def researcher(state: OrchestratorState) -> dict[str, Any]:
        contribution, _ = await run_specialist(
            agent, NOMBRE, paso=state["step_count"], instruccion=state["instruccion_actual"]
        )
        return {"contributions": [contribution], "messages": [mensaje_de_contribucion(contribution)]}

    return researcher
