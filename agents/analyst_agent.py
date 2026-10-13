"""Agente de Análisis/Cómputo.

Herramientas:
- `calcular(expresion)`: aritmética con un evaluador seguro que recorre el AST de Python
  y solo acepta números y operadores (+ - * / // % **). NO usa `eval`: un nombre, una
  llamada a función o un atributo (`__import__("os")`) se rechazan antes de ejecutar nada.
- `estimar_tiempo_procesamiento(...)`: cuántos lotes y cuánto tiempo lleva procesar N
  registros. Sus argumentos se validan con un esquema Pydantic (enteros/reales positivos):
  si el modelo manda un valor inválido, recibe el error de validación como dato y corrige.

Contexto que recibe: la instrucción del supervisor + los RESULTADOS del investigador
(texto y fuentes), que son los datos sobre los que tiene que calcular. Nada más.
"""

import ast
import json
import math
import operator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, tool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field, ValidationError

from agents.common import crear_agente_react, mensaje_de_contribucion, run_specialist
from state import OrchestratorState

NOMBRE = "analyst"
MAX_LARGO_EXPRESION = 200
MAX_EXPONENTE = 100  # 10**100 alcanza; 9**9**9 colgaría el proceso

PROMPT = """Sos el Agente Analista de un equipo de análisis. Hacés cálculos sobre datos que
te pasan; no buscás información ni inventás datos.

Reglas:
1. TODO número que des tiene que salir de una herramienta: `calcular` para aritmética y
   `estimar_tiempo_procesamiento` para lotes y tiempos. No hagas cuentas de cabeza.
2. Usá solo los datos del Contexto o de la Tarea. Si falta un dato necesario, decí cuál
   falta en vez de suponerlo. Si dos datos se contradicen, señalalo explícitamente.
3. Si una herramienta devuelve "error", corregí los argumentos y reintentá una vez.
4. Respuesta breve, en español: cada resultado con la operación que lo produjo y los
   supuestos usados (por ejemplo, el tamaño de lote y de qué fuente salió)."""

_BINARIOS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARIOS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class ExpresionInvalida(ValueError):
    pass


def evaluar_expresion(expresion: str) -> float | int:
    """Evalúa aritmética pura recorriendo el AST. Cualquier otro nodo es un error."""
    if len(expresion) > MAX_LARGO_EXPRESION:
        raise ExpresionInvalida(f"la expresión supera {MAX_LARGO_EXPRESION} caracteres")
    try:
        arbol = ast.parse(expresion.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise ExpresionInvalida(f"sintaxis inválida: {exc.msg}") from exc

    def _eval(nodo: ast.AST) -> float | int:
        if isinstance(nodo, ast.Expression):
            return _eval(nodo.body)
        if isinstance(nodo, ast.Constant) and type(nodo.value) in (int, float):
            return nodo.value
        if isinstance(nodo, ast.BinOp) and type(nodo.op) in _BINARIOS:
            izq, der = _eval(nodo.left), _eval(nodo.right)
            if isinstance(nodo.op, ast.Pow) and abs(der) > MAX_EXPONENTE:
                raise ExpresionInvalida(f"exponente demasiado grande (máximo {MAX_EXPONENTE})")
            return _BINARIOS[type(nodo.op)](izq, der)
        if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _UNARIOS:
            return _UNARIOS[type(nodo.op)](_eval(nodo.operand))
        raise ExpresionInvalida(f"elemento no permitido: {type(nodo).__name__} (solo números y + - * / // % **)")

    try:
        return _eval(arbol)
    except ZeroDivisionError as exc:
        raise ExpresionInvalida("división por cero") from exc


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


@tool(parse_docstring=True)
def calcular(expresion: str) -> str:
    """Calcula una expresión aritmética de forma segura.

    Acepta números, paréntesis y los operadores + - * / // % ** (también ^ como potencia).
    No acepta variables, funciones ni texto. Devuelve JSON {"expresion", "resultado"} o
    {"error", "sugerencia"}.

    Args:
        expresion: La cuenta a resolver, solo con números. Ejemplo: "2000000 / 500".
    """
    try:
        resultado = evaluar_expresion(expresion)
    except ExpresionInvalida as exc:
        return _json({"error": str(exc), "sugerencia": "Reescribí la cuenta usando solo números y operadores."})
    if isinstance(resultado, float) and resultado.is_integer():
        resultado = int(resultado)
    return _json({"expresion": expresion, "resultado": resultado})


class ParametrosEstimacion(BaseModel):
    """Esquema de entrada de la estimación: todo positivo, validado antes de calcular."""

    registros: int = Field(gt=0, description="Cantidad total de registros/eventos a procesar.")
    tamano_lote: int = Field(gt=0, description="Eventos por lote.")
    lotes_por_segundo_por_worker: float = Field(gt=0, description="Lotes que procesa un worker por segundo.")
    workers: int = Field(default=1, gt=0, description="Workers en paralelo.")


def estimar(params: ParametrosEstimacion) -> dict[str, Any]:
    lotes = math.ceil(params.registros / params.tamano_lote)
    lotes_por_segundo = params.lotes_por_segundo_por_worker * params.workers
    segundos = lotes / lotes_por_segundo
    return {
        "entrada": params.model_dump(),
        "lotes": lotes,
        "lotes_por_segundo_total": lotes_por_segundo,
        "segundos": round(segundos, 2),
        "minutos": round(segundos / 60, 2),
        "formula": "lotes = ceil(registros / tamano_lote); segundos = lotes / (lotes_por_segundo_por_worker * workers)",
    }


@tool(parse_docstring=True)
def estimar_tiempo_procesamiento(
    registros: int, tamano_lote: int, lotes_por_segundo_por_worker: float, workers: int = 1
) -> str:
    """Estima cuántos lotes se forman y cuánto tarda procesar un volumen de registros.

    Valida los argumentos (todos > 0) y devuelve JSON con lotes, segundos, minutos y la
    fórmula usada; o {"error", "sugerencia"} si algún argumento es inválido.

    Args:
        registros: Cantidad total de registros o eventos. Ejemplo: 2000000.
        tamano_lote: Eventos por lote (tamano_lote de Caudal). Ejemplo: 500.
        lotes_por_segundo_por_worker: Lotes que procesa un worker por segundo. Ejemplo: 5.
        workers: Cantidad de workers en paralelo. Por defecto 1.
    """
    try:
        params = ParametrosEstimacion(
            registros=registros,
            tamano_lote=tamano_lote,
            lotes_por_segundo_por_worker=lotes_por_segundo_por_worker,
            workers=workers,
        )
    except ValidationError as exc:
        detalles = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
        return _json({"error": f"argumentos inválidos: {detalles}", "sugerencia": "Todos los valores deben ser > 0."})
    return _json(estimar(params))


ANALYST_TOOLS: list[BaseTool] = [calcular, estimar_tiempo_procesamiento]


def build_analyst_agent(model: BaseChatModel) -> CompiledStateGraph:
    return crear_agente_react(model, ANALYST_TOOLS, PROMPT, NOMBRE)


def contexto_para_analista(state: OrchestratorState) -> str:
    """Contexto mínimo: los resultados del investigador (con fuentes), no el historial."""
    hallazgos = [c for c in state.get("contributions", []) if c["agente"] == "researcher" and c["estado"] == "ok"]
    return "\n\n".join(
        f"Hallazgo del investigador (paso {c['paso']}, fuentes: {', '.join(c['fuentes']) or 'sin fuente'}):\n{c['resultado']}"
        for c in hallazgos
    )


def make_analyst_node(agent: CompiledStateGraph):
    async def analyst(state: OrchestratorState) -> dict[str, Any]:
        contribution, _ = await run_specialist(
            agent,
            NOMBRE,
            paso=state["step_count"],
            instruccion=state["instruccion_actual"],
            contexto=contexto_para_analista(state),
        )
        return {"contributions": [contribution], "messages": [mensaje_de_contribucion(contribution)]}

    return analyst
