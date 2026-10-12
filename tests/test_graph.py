"""El orquestador completo con modelos y buscador fake: rutas, tope de pasos, refinamiento,
trazabilidad de contribuciones y aislamiento de contexto de los especialistas."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import RetryPolicy

from graph import build_graph, run_config
from main import ejecutar
from state import estado_inicial
from tests.fakes import (
    DOC_INGESTA,
    DOC_LOTE,
    FakeRetriever,
    FakeSupervisorModel,
    FakeToolModel,
    decision,
    llamada,
)
from agents.research_agent import crear_herramienta_busqueda

PREGUNTA = (
    "MARCADOR-PREGUNTA: según la documentación de Caudal, ¿cuál es el tamaño de lote por defecto? "
    "¿Cuántos lotes son 2 millones de eventos?"
)
SIN_REINTENTOS = RetryPolicy(max_attempts=1)


def _investigador(respuestas_extra: int = 0) -> FakeToolModel:
    """Busca una vez y responde con el dato citado (cada ronda extra repite el patrón)."""
    ronda = [
        llamada("buscar_documentacion", {"consulta": "tamano_lote por defecto"}),
        AIMessage("tamano_lote por defecto: 500 eventos [caudal-instalacion › Configuración]."),
    ]
    return FakeToolModel(respuestas=ronda * (1 + respuestas_extra))


def _analista(rondas: int = 1) -> FakeToolModel:
    ronda = [
        llamada("calcular", {"expresion": "2000000 / 500"}),
        AIMessage("2.000.000 / 500 = 4000 lotes (calcular)."),
    ]
    return FakeToolModel(respuestas=ronda * rondas)


def _grafo(supervisor, investigador=None, analista=None, sintesis=None, retriever=None, **kwargs):
    retriever = retriever or FakeRetriever([DOC_LOTE, DOC_INGESTA])
    return build_graph(
        supervisor_model=supervisor,
        research_model=investigador or _investigador(),
        analyst_model=analista or _analista(),
        synth_model=sintesis or FakeToolModel(respuestas=[AIMessage("Respuesta final: 500 por defecto, 4000 lotes.")]),
        search_tool=crear_herramienta_busqueda(retriever),
        retry_policy=SIN_REINTENTOS,
        **kwargs,
    )


async def test_ruta_supervisor_investigador_analista_cierre():
    supervisor = FakeSupervisorModel(
        decisiones=[
            decision("researcher", "Buscá el tamaño de lote por defecto (tamano_lote)."),
            decision("analyst", "Calculá cuántos lotes son 2.000.000 eventos con el lote por defecto."),
            decision("FINISH"),
        ]
    )
    sintesis = FakeToolModel(respuestas=[AIMessage("Respuesta final: 500 por defecto, 4000 lotes.")])
    graph = _grafo(supervisor, sintesis=sintesis)

    final, traza = await ejecutar(graph, PREGUNTA)

    assert [paso["nodo"] for paso in traza] == [
        "supervisor", "researcher", "supervisor", "analyst", "supervisor", "synthesizer",
    ]
    assert final["task_completed"] is True
    assert final["cierre_forzado"] is False
    assert final["final_answer"] == "Respuesta final: 500 por defecto, 4000 lotes."
    assert [d["next"] for d in final["decisiones"]] == ["researcher", "analyst", "FINISH"]
    # La síntesis recibió las contribuciones con autor y fuentes, no los mensajes internos.
    entrada_sintesis = sintesis.recibidos[0][-1].content
    assert "researcher" in entrada_sintesis and "caudal-instalacion › Configuración" in entrada_sintesis
    assert "4000 lotes" in entrada_sintesis


async def test_contribuciones_se_acumulan_con_autor_tarea_y_fuentes():
    supervisor = FakeSupervisorModel(
        decisiones=[decision("researcher", "Buscá tamano_lote."), decision("analyst", "Calculá 2000000/500."), decision("FINISH")]
    )
    final, _ = await ejecutar(_grafo(supervisor), PREGUNTA)

    investigador, analista = final["contributions"]
    assert investigador["agente"] == "researcher" and investigador["paso"] == 1
    assert investigador["tarea"] == "Buscá tamano_lote."
    assert investigador["herramientas"] == ["buscar_documentacion"]
    assert investigador["fuentes"] == ["caudal-instalacion › Configuración", "caudal-rendimiento › Backpressure y tamaño de lote"]
    assert analista["agente"] == "analyst" and analista["paso"] == 2
    assert analista["herramientas"] == ["calcular"]
    assert analista["estado"] == "ok"
    # En el historial del orquestador cada aporte queda firmado con `name`.
    nombres = [m.name for m in final["messages"] if isinstance(m, AIMessage)]
    assert nombres == ["supervisor", "researcher", "supervisor", "analyst", "supervisor", "synthesizer"]


async def test_especialistas_no_reciben_el_historial_completo():
    supervisor = FakeSupervisorModel(
        decisiones=[decision("researcher", "Buscá tamano_lote."), decision("analyst", "Calculá 2000000/500."), decision("FINISH")]
    )
    investigador, analista = _investigador(), _analista()
    await ejecutar(_grafo(supervisor, investigador=investigador, analista=analista), PREGUNTA)

    # Primera llamada de cada especialista: system prompt + UN mensaje humano con la tarea.
    primera_inv = investigador.recibidos[0]
    assert [type(m).__name__ for m in primera_inv] == ["SystemMessage", "HumanMessage"]
    assert primera_inv[1].content == "Tarea: Buscá tamano_lote."

    primera_ana = analista.recibidos[0]
    assert [type(m).__name__ for m in primera_ana] == ["SystemMessage", "HumanMessage"]
    pedido = primera_ana[1].content
    assert pedido.startswith("Tarea: Calculá 2000000/500.")
    assert "500 eventos [caudal-instalacion › Configuración]" in pedido  # el contexto necesario sí llega
    for llamada_modelo in investigador.recibidos + analista.recibidos:
        texto = " ".join(str(m.content) for m in llamada_modelo)
        assert "MARCADOR-PREGUNTA" not in texto  # no ven la conversación del usuario
        assert "[supervisor]" not in texto  # ni las decisiones del supervisor
    # El analista no ve los tool calls / ToolMessages del investigador.
    assert not any(
        isinstance(m, ToolMessage) and m.name == "buscar_documentacion" for llamada_modelo in analista.recibidos for m in llamada_modelo
    )


async def test_rubrica_pide_refinamiento_y_despues_termina():
    supervisor = FakeSupervisorModel(
        decisiones=[
            decision("researcher", "Buscá tamano_lote."),
            decision("analyst", "Calculá 2000000/500."),
            decision("analyst", "Refiná: faltó calcular con el lote de ingesta masiva (5000): 2000000/5000.", razon="calculos incompletos"),
            decision("FINISH"),
        ]
    )
    final, traza = await ejecutar(_grafo(supervisor, analista=_analista(rondas=2)), PREGUNTA)

    assert [p["nodo"] for p in traza] == [
        "supervisor", "researcher", "supervisor", "analyst", "supervisor", "analyst", "supervisor", "synthesizer",
    ]
    assert [c["agente"] for c in final["contributions"]] == ["researcher", "analyst", "analyst"]
    assert final["contributions"][2]["tarea"].startswith("Refiná")
    assert final["decisiones"][2]["rubrica"]["calculos"] is False
    assert final["task_completed"] is True and final["cierre_forzado"] is False
    # En el turno de refinamiento el supervisor vio las contribuciones previas con su autor.
    vio = supervisor.recibidos[2][-1].content
    assert "#1 researcher" in vio and "#2 analyst" in vio


async def test_tope_de_pasos_corta_el_bucle_del_supervisor():
    # Un supervisor que nunca da FINISH: sin el tope, el bucle sería infinito.
    supervisor = FakeSupervisorModel(decisiones=[decision("researcher", "Buscá de nuevo.")], repetir_ultima=True)
    investigador = FakeToolModel(
        respuestas=[llamada("buscar_documentacion", {"consulta": "x"}), AIMessage("dato [fuente]")], repetir_ultima=False
    )
    investigador.respuestas = investigador.respuestas * 10
    sintesis = FakeToolModel(respuestas=[AIMessage("Respuesta parcial.")])
    graph = _grafo(supervisor, investigador=investigador, sintesis=sintesis, max_steps=3)

    final, traza = await ejecutar(graph, PREGUNTA)

    assert len(supervisor.recibidos) == 3  # el 4.º turno no llama al LLM
    assert [p["nodo"] for p in traza].count("researcher") == 3
    assert traza[-1]["nodo"] == "synthesizer"
    assert final["cierre_forzado"] is True and final["task_completed"] is True
    assert "cierre fue forzado" in sintesis.recibidos[0][-1].content


async def test_error_de_busqueda_queda_como_contribucion_con_error():
    supervisor = FakeSupervisorModel(decisiones=[decision("researcher", "Buscá tamano_lote."), decision("FINISH")])
    investigador = FakeToolModel(
        respuestas=[llamada("buscar_documentacion", {"consulta": "tamano_lote"}), AIMessage("La documentación no está disponible.")]
    )
    graph = _grafo(supervisor, investigador=investigador, retriever=FakeRetriever(falla=ConnectionError("timeout")))

    final, _ = await ejecutar(graph, PREGUNTA)

    contribucion = final["contributions"][0]
    assert contribucion["estado"] == "error" and contribucion["fuentes"] == []
    assert "estado: error" in supervisor.recibidos[1][-1].content  # el supervisor lo ve


async def test_recursion_limit_y_mermaid():
    graph = _grafo(FakeSupervisorModel(decisiones=[decision("FINISH")]))
    assert run_config()["recursion_limit"] == 20
    mermaid = graph.get_graph().draw_mermaid()
    for nodo in ("supervisor", "researcher", "analyst", "synthesizer"):
        assert nodo in mermaid
    final = await graph.ainvoke(estado_inicial("hola"), run_config())
    assert final["contributions"] == [] and final["task_completed"] is True
    assert isinstance(final["messages"][0], HumanMessage)


async def test_supervisor_usa_salida_estructurada_con_el_metodo_configurado():
    supervisor = FakeSupervisorModel(decisiones=[decision("FINISH")])
    _grafo(supervisor, structured_method="function_calling")
    assert supervisor.metodo == "function_calling"
