"""Supervisor: router inteligente y controlador de flujo del orquestador.

En cada turno lee la pregunta del usuario y las contribuciones acumuladas (con autor y
estado) y decide, con salida estructurada Pydantic (`SupervisorDecision`):
- a quién delega ahora (`researcher` / `analyst`) y con qué instrucción puntual, o
- `FINISH`, si la rúbrica de validación se cumple y se puede sintetizar la respuesta.

Antes de terminar aplica una RÚBRICA (cobertura, evidencia, cálculos, consistencia): si
algo falla, devuelve la tarea al especialista que corresponde para que refine su output.

Protección contra el supervisor infinito: `step_count` cuenta sus turnos y, al llegar a
`max_steps`, el nodo NO llama al LLM y fuerza la síntesis (`cierre_forzado=True`).
"""

from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from state import Contribution, Decision, Destino, OrchestratorState

MAX_STEPS = 6

PROMPT = """Sos el Supervisor de un equipo de análisis e investigación. Coordinás a dos especialistas:
- "researcher": Agente Investigador. Busca en la documentación oficial de la librería Caudal
  (base vectorial) y devuelve datos con su fuente. No calcula.
- "analyst": Agente Analista. Hace cálculos con herramientas (aritmética segura, estimación
  de lotes y tiempos) SOLO sobre los datos que le pasa el equipo. No busca información.

Dada la conversación actual (la pregunta y lo que ya aportó cada especialista), decidí
¿quién debe intervenir ahora o es momento de finalizar?

Cómo delegar:
- Elegí UN especialista por turno y dale una `instruccion` autocontenida y específica
  (qué dato buscar o qué cuenta hacer, con los números ya conocidos). El especialista NO ve
  la conversación: solo tu instrucción (y el analista, además, los hallazgos del investigador).
- Primero los datos documentales (researcher), después los cálculos (analyst).

RÚBRICA DE VALIDACIÓN (evaluala siempre y completá `rubrica`):
1. cobertura: cada parte de la pregunta tiene respuesta en alguna contribución.
2. evidencia: cada dato tomado de la documentación tiene una fuente citada por el researcher.
3. calculos: cada número derivado (cuentas, tiempos) lo calculó el analyst con una
   herramienta, usando datos del researcher o de la pregunta (no supuestos inventados).
4. consistencia: los datos que usó el analyst coinciden con los del researcher y no hay
   contradicciones entre contribuciones.

Decisión:
- Si los 4 criterios se cumplen -> next = "FINISH".
- Si alguno falla -> delegá al especialista que puede arreglarlo, con una instrucción que
  diga exactamente qué falta o qué hay que corregir (esto es pedir un refinamiento).
- Si un especialista ya devolvió "error" dos veces en lo mismo, no insistas: FINISH y que
  la síntesis declare el faltante.
- Vas por el turno {paso} de {max_steps}. En el último turno disponible, elegí FINISH."""


class Rubrica(BaseModel):
    cobertura: bool = Field(description="Cada parte de la pregunta tiene respuesta.")
    evidencia: bool = Field(description="Cada dato documental tiene fuente citada.")
    calculos: bool = Field(description="Cada número derivado lo calculó el analista con herramienta.")
    consistencia: bool = Field(description="No hay contradicciones entre contribuciones.")

    def cumplida(self) -> bool:
        return all(self.model_dump().values())


class SupervisorDecision(BaseModel):
    """Decisión estructurada del supervisor en un turno."""

    rubrica: Rubrica = Field(description="Evaluación de la rúbrica sobre las contribuciones actuales.")
    razon: str = Field(description="Por qué se elige este destino (breve).")
    next: Literal["researcher", "analyst", "FINISH"] = Field(description="Quién interviene ahora, o FINISH.")
    instruccion: str = Field(
        default="",
        description="Instrucción autocontenida para el especialista elegido. Vacía si next es FINISH.",
    )


def render_contribuciones(contributions: list[Contribution]) -> str:
    if not contributions:
        return "(todavía ningún especialista intervino)"
    bloques = []
    for i, c in enumerate(contributions, start=1):
        bloques.append(
            f"#{i} {c['agente']} (turno {c['paso']}, estado: {c['estado']})\n"
            f"Tarea: {c['tarea']}\n"
            f"Herramientas: {', '.join(c['herramientas']) or 'ninguna'} | Fuentes: {', '.join(c['fuentes']) or 'ninguna'}\n"
            f"Resultado: {c['resultado']}"
        )
    return "\n\n".join(bloques)


def build_decider(model: BaseChatModel, method: str = "json_schema") -> Runnable:
    """El LLM del supervisor con salida estructurada (devuelve un SupervisorDecision)."""
    return model.with_structured_output(SupervisorDecision, method=method)


def make_supervisor_node(decider: Runnable, max_steps: int = MAX_STEPS):
    async def supervisor(state: OrchestratorState) -> dict[str, Any]:
        paso = state.get("step_count", 0) + 1
        if paso > max_steps:
            # Tope duro: sin llamar al LLM, se cierra con lo que haya.
            razon = f"Se alcanzó el máximo de {max_steps} turnos del supervisor: cierre forzado."
            return {
                "next_agent": "synthesizer",
                "instruccion_actual": "",
                "cierre_forzado": True,
                "decisiones": [_registro(paso, "FINISH", "", razon, {})],
                "messages": [AIMessage(content=f"[supervisor] -> synthesizer: {razon}", name="supervisor")],
            }

        decision: SupervisorDecision = await decider.ainvoke(
            [
                SystemMessage(PROMPT.format(paso=paso, max_steps=max_steps)),
                HumanMessage(
                    f"Pregunta del usuario:\n{state['pregunta']}\n\n"
                    f"Contribuciones hasta ahora:\n{render_contribuciones(state.get('contributions', []))}"
                ),
            ]
        )
        destino: Destino = "synthesizer" if decision.next == "FINISH" else decision.next
        # Un especialista sin instrucción recibiría una tarea vacía: se usa la pregunta.
        instruccion = decision.instruccion.strip() or (state["pregunta"] if destino != "synthesizer" else "")
        return {
            "next_agent": destino,
            "instruccion_actual": instruccion,
            "step_count": paso,
            "decisiones": [_registro(paso, decision.next, instruccion, decision.razon, decision.rubrica.model_dump())],
            "messages": [
                AIMessage(content=f"[supervisor] -> {destino}: {decision.razon}", name="supervisor")
            ],
        }

    return supervisor


def _registro(paso: int, destino: str, instruccion: str, razon: str, rubrica: dict[str, bool]) -> Decision:
    return {"paso": paso, "next": destino, "instruccion": instruccion, "razon": razon, "rubrica": rubrica}


def route_from_supervisor(state: OrchestratorState) -> Literal["researcher", "analyst", "synthesizer"]:
    """Arista condicional: el `Literal` del retorno define los destinos posibles del grafo."""
    destino = state.get("next_agent", "synthesizer")
    if destino in ("researcher", "analyst"):
        return destino
    return "synthesizer"
