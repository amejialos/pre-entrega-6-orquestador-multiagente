"""Piezas compartidas por los especialistas: cómo se invoca a un agente ReAct aislado.

Cada especialista es un `create_react_agent` (su propio mini-grafo modelo <-> herramientas)
que se invoca con UN solo mensaje: la instrucción del supervisor más el contexto mínimo.
No ve `state["messages"]` del orquestador: ni la conversación del usuario, ni las
decisiones del supervisor, ni los tool calls del otro especialista. Lo que devuelve se
convierte en una `Contribution` con autor, tarea, resultado, fuentes y herramientas.
"""

import json
import warnings
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import RetryPolicy
from langgraph.warnings import LangGraphDeprecatedSinceV10

from state import Contribution, Especialista

# Tope de pasos del mini-grafo de cada especialista: cada vuelta modelo -> herramientas
# consume 2. Con 9 entran 4 rondas de herramientas más la respuesta final.
SPECIALIST_RECURSION_LIMIT = 9

# Reintentos de los nodos que llaman al LLM ante errores transitorios (429 por cuota
# por minuto, 5xx), con esperas largas para que se libere la ventana del free tier.
RETRY_MODELO = RetryPolicy(
    max_attempts=3,
    initial_interval=20.0,
    backoff_factor=2.0,
    retry_on=lambda exc: getattr(exc, "status_code", None) in {429, 500, 502, 503, 504},
)


def crear_agente_react(model: BaseChatModel, tools: Sequence[BaseTool], prompt: str, name: str) -> CompiledStateGraph:
    """`create_react_agent` de LangGraph (el que sugiere la consigna) con sus herramientas acotadas.

    En LangGraph 1.x está marcado como deprecado a favor de `langchain.agents.create_agent`
    (se elimina en la 2.0) pero funciona igual; se silencia solo ese aviso, solo acá.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LangGraphDeprecatedSinceV10)
        return create_react_agent(model, list(tools), prompt=prompt, name=name)


def armar_prompt(instruccion: str, contexto: str = "") -> str:
    """El único mensaje que recibe un especialista."""
    if not contexto:
        return f"Tarea: {instruccion}"
    return f"Tarea: {instruccion}\n\nContexto (datos que ya obtuvo el equipo):\n{contexto}"


def _fuentes_de(tool_messages: list[ToolMessage]) -> list[str]:
    """Las herramientas devuelven JSON; si traen `resultados[].fuente`, se registran."""
    fuentes: list[str] = []
    for msg in tool_messages:
        try:
            data = json.loads(msg.content) if isinstance(msg.content, str) else {}
        except json.JSONDecodeError:
            continue
        for item in data.get("resultados", []) if isinstance(data, dict) else []:
            fuente = item.get("fuente") if isinstance(item, dict) else None
            if fuente and fuente not in fuentes:
                fuentes.append(fuente)
    return fuentes


async def run_specialist(
    agent: CompiledStateGraph,
    nombre: Especialista,
    paso: int,
    instruccion: str,
    contexto: str = "",
) -> tuple[Contribution, list[Any]]:
    """Corre el especialista aislado y devuelve (contribución, mensajes internos)."""
    entrada = [HumanMessage(armar_prompt(instruccion, contexto))]
    try:
        resultado = await agent.ainvoke(
            {"messages": entrada}, config={"recursion_limit": SPECIALIST_RECURSION_LIMIT}
        )
        mensajes = resultado["messages"]
        finales = [m for m in mensajes if isinstance(m, AIMessage) and not m.tool_calls]
        texto = finales[-1].text if finales else ""
        estado = "ok" if texto.strip() else "error"
        if not texto.strip():
            texto = "El especialista no produjo una respuesta final."
    except GraphRecursionError:
        mensajes = entrada
        texto = f"El especialista superó el límite de {SPECIALIST_RECURSION_LIMIT} pasos sin responder."
        estado = "error"

    tool_messages = [m for m in mensajes if isinstance(m, ToolMessage)]
    herramientas = [m.name for m in tool_messages if m.name]
    fuentes = _fuentes_de(tool_messages) or sorted(set(herramientas))
    # Si todas las herramientas fallaron, la respuesta no tiene respaldo: se marca como
    # error para que el supervisor lo vea en la rúbrica y decida reintentar o cerrar.
    if tool_messages and all(_es_error(m) for m in tool_messages):
        estado = "error"
        fuentes = []
    contribution: Contribution = {
        "agente": nombre,
        "paso": paso,
        "tarea": instruccion,
        "resultado": texto,
        "fuentes": fuentes,
        "herramientas": herramientas,
        "llamadas": [
            {"herramienta": call["name"], "args": call["args"]}
            for m in mensajes
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        ],
        "estado": estado,
    }
    return contribution, mensajes


def _es_error(msg: ToolMessage) -> bool:
    try:
        data = json.loads(msg.content) if isinstance(msg.content, str) else {}
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and "error" in data


def mensaje_de_contribucion(c: Contribution) -> AIMessage:
    """Resumen de la contribución para el historial del orquestador (con autor en `name`)."""
    return AIMessage(content=f"[{c['agente']} | {c['estado']}] {c['resultado']}", name=c["agente"])
