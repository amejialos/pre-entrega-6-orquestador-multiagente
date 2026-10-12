"""Síntesis final: arma la respuesta al usuario a partir de las contribuciones.

Es el único nodo que va a END. Recibe la pregunta y las contribuciones (con autor y
fuentes), no los mensajes internos de los especialistas. Si el cierre fue forzado por el
tope de pasos, o la última rúbrica del supervisor quedó incompleta, se le avisa para que
declare qué faltó en vez de rellenar con supuestos.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.supervisor import render_contribuciones
from state import OrchestratorState

PROMPT = """Sos el redactor final de un equipo de análisis. Escribí la respuesta al usuario
usando EXCLUSIVAMENTE las contribuciones de los especialistas.

Reglas:
- Respondé cada parte de la pregunta, en español, de forma clara y breve.
- Citá la fuente documental de cada dato entre corchetes (como la dio el investigador) y
  atribuí los cálculos al analista, mostrando la operación.
- Si hay datos contradictorios entre contribuciones, priorizá el dato documental con fuente
  y mencioná la discrepancia.
- Si algo quedó sin resolver, decilo explícitamente. No agregues datos que no estén en las
  contribuciones."""


def make_synthesizer_node(model: BaseChatModel):
    async def synthesizer(state: OrchestratorState) -> dict[str, Any]:
        avisos = []
        if state.get("cierre_forzado"):
            avisos.append("ATENCIÓN: el cierre fue forzado por el límite de pasos; la tarea puede estar incompleta.")
        decisiones = state.get("decisiones", [])
        rubrica = decisiones[-1]["rubrica"] if decisiones else {}
        incumplidos = [criterio for criterio, ok in rubrica.items() if not ok]
        if incumplidos:
            avisos.append(f"ATENCIÓN: la validación del supervisor no aprobó: {', '.join(incumplidos)}.")
        respuesta = await model.ainvoke(
            [
                SystemMessage(PROMPT),
                HumanMessage(
                    f"Pregunta del usuario:\n{state['pregunta']}\n\n"
                    f"Contribuciones:\n{render_contribuciones(state.get('contributions', []))}"
                    + ("\n\n" + "\n".join(avisos) if avisos else "")
                ),
            ]
        )
        texto = respuesta.text
        return {
            "final_answer": texto,
            "task_completed": True,
            "messages": [AIMessage(content=texto, name="synthesizer")],
        }

    return synthesizer
