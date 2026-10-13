"""Punto de entrada: arma el orquestador con Gemini + Pinecone reales y corre una consulta.

    uv run python main.py                      # consulta demo
    uv run python main.py "tu pregunta"        # otra consulta
    uv run python main.py --mermaid            # solo imprime el diagrama Mermaid del grafo

`ejecutar` recorre el grafo con `astream(stream_mode=["updates", "values"])`: cada
"update" es lo que devolvió un nodo apenas terminó, así se arma la TRAZA DE DELEGACIÓN
(qué nodo corrió, qué decidió el supervisor, qué aportó cada especialista). La traza se
guarda en `trazas/` como .json y .log. De los mensajes se guarda solo el contenido:
nunca `response_metadata` ni `additional_kwargs` (pueden traer datos del proveedor).
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from graph import RECURSION_LIMIT, build_graph, run_config
from state import estado_inicial

BASE = Path(__file__).parent
TRAZA_JSON = BASE / "trazas" / "traza_demo.json"
TRAZA_LOG = BASE / "trazas" / "traza_demo.log"

PREGUNTA_DEMO = (
    "Según la documentación de Caudal, ¿cuál es el tamaño de lote (tamano_lote) por defecto y cuál "
    "se recomienda para cargas de ingesta masiva? Si tengo que procesar 2.000.000 de eventos con 4 "
    "workers y cada worker procesa 5 lotes por segundo, ¿cuántos lotes se forman y cuánto tardaría "
    "con cada uno de esos dos tamaños de lote?"
)


def _paso_de_traza(nodo: str, update: dict[str, Any], inicio: float) -> dict[str, Any]:
    paso: dict[str, Any] = {"nodo": nodo, "t_s": round(time.monotonic() - inicio, 1)}
    if nodo == "supervisor":
        d = update["decisiones"][-1]
        paso.update(
            turno=d["paso"],
            decision=d["next"],
            destino=update["next_agent"],
            instruccion=d["instruccion"],
            razon=d["razon"],
            rubrica=d["rubrica"],
            cierre_forzado=update.get("cierre_forzado", False),
        )
    elif nodo in ("researcher", "analyst"):
        c = update["contributions"][-1]
        paso.update(
            turno=c["paso"],
            tarea=c["tarea"],
            llamadas=c["llamadas"],
            fuentes=c["fuentes"],
            estado=c["estado"],
            contribucion=c["resultado"],
        )
    elif nodo == "synthesizer":
        paso["respuesta"] = update["final_answer"]
    return paso


async def ejecutar(
    graph: CompiledStateGraph, pregunta: str, recursion_limit: int = RECURSION_LIMIT, verbose: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Corre el orquestador y devuelve (estado final, traza de delegación)."""
    traza: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    inicio = time.monotonic()
    async for modo, data in graph.astream(
        estado_inicial(pregunta), run_config(recursion_limit), stream_mode=["updates", "values"]
    ):
        if modo == "values":
            final = data
            continue
        for nodo, update in data.items():
            paso = _paso_de_traza(nodo, update, inicio)
            traza.append(paso)
            if verbose:
                print(paso_a_texto(paso), end="\n\n", flush=True)
    return final, traza


def paso_a_texto(paso: dict[str, Any]) -> str:
    """Un paso de la traza en formato legible (para la consola, el .log y el notebook)."""
    cabecera = f"[{paso['t_s']:>6.1f}s] {paso['nodo'].upper()}"
    if paso["nodo"] == "supervisor":
        rubrica = ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in paso["rubrica"].items()) or "-"
        lineas = [
            f"{cabecera} (turno {paso['turno']}) -> {paso['destino']}"
            + ("  [CIERRE FORZADO]" if paso["cierre_forzado"] else ""),
            f"  razón: {paso['razon']}",
            f"  rúbrica: {rubrica}",
        ]
        if paso["instruccion"]:
            lineas.append(f"  instrucción: {paso['instruccion']}")
        return "\n".join(lineas)
    if paso["nodo"] in ("researcher", "analyst"):
        lineas = [f"{cabecera} (turno {paso['turno']}, estado {paso['estado']})", f"  tarea: {paso['tarea']}"]
        lineas += [
            f"  herramienta: {ll['herramienta']}({json.dumps(ll['args'], ensure_ascii=False)})" for ll in paso["llamadas"]
        ]
        lineas.append(f"  fuentes: {', '.join(paso['fuentes']) or '-'}")
        lineas.append("  contribución: " + paso["contribucion"].replace("\n", "\n    "))
        return "\n".join(lineas)
    return f"{cabecera}\n  respuesta final:\n    " + paso.get("respuesta", "").replace("\n", "\n    ")


def guardar_traza(pregunta: str, final: dict[str, Any], traza: list[dict[str, Any]], modelos: dict[str, str]) -> None:
    TRAZA_JSON.parent.mkdir(exist_ok=True)
    registro = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "modelos": modelos,
        "pregunta": pregunta,
        "ruta": [p["nodo"] for p in traza],
        "traza": traza,
        "contributions": final.get("contributions", []),
        "task_completed": final.get("task_completed"),
        "cierre_forzado": final.get("cierre_forzado"),
        "respuesta_final": final.get("final_answer"),
    }
    TRAZA_JSON.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    texto = [f"Fecha: {registro['fecha']}", f"Modelos: {modelos}", f"Pregunta: {pregunta}", ""]
    texto += [paso_a_texto(p) + "\n" for p in traza]
    texto.append("Ruta: " + " -> ".join(registro["ruta"]))
    TRAZA_LOG.write_text("\n".join(texto) + "\n", encoding="utf-8")


def build_from_env() -> tuple[CompiledStateGraph, dict[str, str]]:
    """Grafo real: un modelo por rol (Gemini u OpenAI) y el RAG híbrido de la PE4 sobre Pinecone."""
    from dotenv import load_dotenv

    from agents.research_agent import crear_herramienta_busqueda
    from providers import build_model, model_name_for
    from rag_system import RAGSystem

    load_dotenv(BASE / ".env")
    roles = ("supervisor", "research", "analyst", "synth")
    modelos = {rol: model_name_for(rol) for rol in roles}
    graph = build_graph(
        supervisor_model=build_model("supervisor"),
        research_model=build_model("research"),
        analyst_model=build_model("analyst"),
        synth_model=build_model("synth"),
        search_tool=crear_herramienta_busqueda(RAGSystem.from_env()),
        structured_method=os.environ.get("SUPERVISOR_STRUCTURED_METHOD", "").strip() or "json_schema",
    )
    return graph, modelos


async def main() -> None:
    graph, modelos = build_from_env()
    if "--mermaid" in sys.argv:
        print(graph.get_graph().draw_mermaid())
        return
    pregunta = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or PREGUNTA_DEMO
    print(f"Modelos: {modelos}\nPregunta: {pregunta}\n")
    final, traza = await ejecutar(graph, pregunta, verbose=True)
    guardar_traza(pregunta, final, traza, modelos)
    print("Ruta:", " -> ".join(p["nodo"] for p in traza))
    print(f"Traza guardada en {TRAZA_JSON.relative_to(BASE)} y {TRAZA_LOG.relative_to(BASE)}")


if __name__ == "__main__":
    asyncio.run(main())
