"""El grafo jerárquico: Supervisor -> especialistas -> Supervisor -> ... -> Síntesis -> END.

    START -> supervisor --route_from_supervisor--> researcher  -> supervisor
                                              |--> analyst     -> supervisor
                                              |--> synthesizer -> END

- El supervisor es el único que decide el flujo (topología jerárquica): los especialistas
  siempre le devuelven el control, nunca se llaman entre sí.
- `route_from_supervisor` es la arista condicional; su retorno `Literal[...]` le dice a
  LangGraph cuáles son los destinos posibles (y así aparecen en el diagrama).
- Dos frenos contra el bucle infinito: `max_steps` en el supervisor (fuerza la síntesis)
  y `recursion_limit` en la invocación (red de seguridad de LangGraph).
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.analyst_agent import build_analyst_agent, make_analyst_node
from agents.common import RETRY_MODELO
from agents.research_agent import build_research_agent, make_research_node
from agents.supervisor import MAX_STEPS, build_decider, make_supervisor_node, route_from_supervisor
from agents.synthesizer import make_synthesizer_node
from state import OrchestratorState

# Cada turno supervisor + especialista son 2 pasos del grafo: 6 turnos = 12, más el turno
# que fuerza el cierre y la síntesis = 14. Con 20 sobra margen y un bug igual se corta.
RECURSION_LIMIT = 20


def build_graph(
    supervisor_model: BaseChatModel,
    research_model: BaseChatModel,
    analyst_model: BaseChatModel,
    synth_model: BaseChatModel,
    search_tool: BaseTool,
    max_steps: int = MAX_STEPS,
    structured_method: str = "json_schema",
    retry_policy=RETRY_MODELO,
) -> CompiledStateGraph:
    """Arma y compila el orquestador. Los modelos y el buscador se inyectan (fakes en los tests)."""
    research_agent = build_research_agent(research_model, search_tool)
    analyst_agent = build_analyst_agent(analyst_model)

    builder = StateGraph(OrchestratorState)
    builder.add_node(
        "supervisor",
        make_supervisor_node(build_decider(supervisor_model, structured_method), max_steps),
        retry_policy=retry_policy,
    )
    builder.add_node("researcher", make_research_node(research_agent), retry_policy=retry_policy)
    builder.add_node("analyst", make_analyst_node(analyst_agent), retry_policy=retry_policy)
    builder.add_node("synthesizer", make_synthesizer_node(synth_model), retry_policy=retry_policy)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_from_supervisor)
    builder.add_edge("researcher", "supervisor")
    builder.add_edge("analyst", "supervisor")
    builder.add_edge("synthesizer", END)
    return builder.compile(name="orquestador")


def run_config(recursion_limit: int = RECURSION_LIMIT) -> RunnableConfig:
    return {"recursion_limit": recursion_limit}
