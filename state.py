"""Estado compartido del orquestador.

`OrchestratorState` hereda de `MessagesState` (la conversación, con el reducer
`add_messages`) y agrega los campos de control y de trazabilidad:

- `next_agent`: a quién delega el supervisor en este turno (lo lee la arista condicional).
- `instruccion_actual`: la instrucción puntual que el supervisor le da al especialista.
  Es lo ÚNICO que el especialista recibe del supervisor (más el contexto mínimo que
  necesite): así se evita la contaminación de contexto.
- `contributions`: qué agente aportó qué. Con `operator.add` como reducer, cada nodo
  devuelve solo su contribución nueva y LangGraph la concatena: nadie pisa lo de otro.
- `decisiones`: el registro de cada decisión del supervisor (destino, razón, rúbrica).
- `step_count`: turnos del supervisor. Al llegar a `MAX_STEPS` se fuerza el cierre.
- `task_completed` / `cierre_forzado` / `final_answer`: el resultado.
"""

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import MessagesState

Especialista = Literal["researcher", "analyst"]
# Nombres de nodo a los que puede ir el supervisor (la síntesis es el cierre).
Destino = Literal["researcher", "analyst", "synthesizer"]


class Contribution(TypedDict):
    """Una entrega de un especialista: quién, qué se le pidió, qué devolvió y con qué."""

    agente: Especialista
    paso: int  # turno del supervisor que la pidió
    tarea: str  # la instrucción que recibió
    resultado: str  # su respuesta final
    fuentes: list[str]  # doc_id/sección (investigador) o herramienta de cálculo (analista)
    herramientas: list[str]  # herramientas que efectivamente llamó
    llamadas: list[dict]  # cada tool call: {"herramienta", "args"} (para la traza)
    estado: Literal["ok", "error"]


class Decision(TypedDict):
    """Registro de una decisión del supervisor (la versión serializable de SupervisorDecision)."""

    paso: int
    next: str
    instruccion: str
    razon: str
    rubrica: dict[str, bool]


class OrchestratorState(MessagesState):
    pregunta: str
    next_agent: Destino
    instruccion_actual: str
    contributions: Annotated[list[Contribution], operator.add]
    decisiones: Annotated[list[Decision], operator.add]
    step_count: int
    task_completed: bool
    cierre_forzado: bool
    final_answer: str


def estado_inicial(pregunta: str) -> dict:
    """Entrada del grafo para una pregunta nueva."""
    return {
        "messages": [HumanMessage(pregunta)],
        "pregunta": pregunta,
        "contributions": [],
        "decisiones": [],
        "step_count": 0,
        "task_completed": False,
        "cierre_forzado": False,
        "final_answer": "",
    }
