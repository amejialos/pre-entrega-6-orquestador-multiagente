"""Fakes para probar el orquestador sin red ni keys.

- `FakeToolModel`: modelo de chat que devuelve, en orden, AIMessages (con o sin tool_calls)
  y guarda lo que recibió en cada llamada (para verificar el aislamiento de contexto).
- `FakeSupervisorModel`: su `with_structured_output` devuelve decisiones `SupervisorDecision`
  guionadas, como haría el LLM con salida estructurada.
- `FakeRetriever`: reemplaza a RAGSystem (o falla, para probar el error como dato).
"""

import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from agents.supervisor import Rubrica, SupervisorDecision


def llamada(nombre: str, args: dict[str, Any], call_id: str | None = None) -> AIMessage:
    """AIMessage que pide una herramienta."""
    call_id = call_id or f"call-{uuid.uuid4().hex[:8]}"
    return AIMessage(content="", tool_calls=[{"name": nombre, "args": args, "id": call_id, "type": "tool_call"}])


class FakeToolModel(BaseChatModel):
    """Responde con `respuestas` en orden. Con `repetir_ultima=True` repite la última para siempre."""

    respuestas: list[AIMessage]
    repetir_ultima: bool = False
    recibidos: list[list[BaseMessage]] = Field(default_factory=list)
    herramientas_vinculadas: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolModel":
        self.herramientas_vinculadas = [t.name for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.recibidos.append(list(messages))
        indice = len(self.recibidos) - 1
        if indice >= len(self.respuestas):
            if not self.repetir_ultima:
                raise AssertionError(f"El modelo fake recibió una llamada de más (#{indice + 1}).")
            indice = len(self.respuestas) - 1
        # copia con id único: add_messages REEMPLAZA un mensaje si llega otro con el mismo id
        respuesta = self.respuestas[indice].model_copy(update={"id": f"fake-{uuid.uuid4()}"})
        return ChatResult(generations=[ChatGeneration(message=respuesta)])


RUBRICA_OK = Rubrica(cobertura=True, evidencia=True, calculos=True, consistencia=True)
RUBRICA_PENDIENTE = Rubrica(cobertura=False, evidencia=False, calculos=False, consistencia=True)


def decision(next_: str, instruccion: str = "", razon: str = "fake", rubrica: Rubrica | None = None) -> SupervisorDecision:
    if rubrica is None:
        rubrica = RUBRICA_OK if next_ == "FINISH" else RUBRICA_PENDIENTE
    return SupervisorDecision(next=next_, instruccion=instruccion, razon=razon, rubrica=rubrica)


class FakeSupervisorModel(BaseChatModel):
    """Modelo cuyo `with_structured_output` devuelve las `decisiones` guionadas en orden."""

    decisiones: list[SupervisorDecision]
    repetir_ultima: bool = False
    recibidos: list[list[BaseMessage]] = Field(default_factory=list)
    metodo: str | None = None

    @property
    def _llm_type(self) -> str:
        return "fake-supervisor"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # pragma: no cover
        raise AssertionError("El supervisor solo se usa con salida estructurada.")

    def with_structured_output(self, schema: Any, *, method: str = "json_schema", **kwargs: Any):
        assert schema is SupervisorDecision
        self.metodo = method

        def _decidir(messages: list[BaseMessage]) -> SupervisorDecision:
            self.recibidos.append(list(messages))
            indice = len(self.recibidos) - 1
            if indice >= len(self.decisiones):
                if not self.repetir_ultima:
                    raise AssertionError(f"El supervisor fake recibió una llamada de más (#{indice + 1}).")
                indice = len(self.decisiones) - 1
            return self.decisiones[indice]

        return RunnableLambda(_decidir)


class FakeRetriever:
    """Devuelve `docs` para cualquier consulta; con `falla` levanta la excepción indicada."""

    def __init__(self, docs: list[Document] | None = None, falla: Exception | None = None) -> None:
        self.docs = docs or []
        self.falla = falla
        self.consultas: list[str] = []

    def retrieve(self, query: str) -> list[Document]:
        self.consultas.append(query)
        if self.falla:
            raise self.falla
        return self.docs


DOC_LOTE = Document(
    page_content="Configuración - tamano_lote (cuántos eventos se procesan juntos, por defecto 500).",
    metadata={"doc_id": "caudal-instalacion", "section": "Configuración", "page": 3},
)
DOC_INGESTA = Document(
    page_content="Caudal recomienda lotes de 500 eventos para baja latencia y de 5000 para ingesta masiva.",
    metadata={"doc_id": "caudal-rendimiento", "section": "Backpressure y tamaño de lote", "page": 2},
)
