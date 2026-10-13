import json

import pytest

from agents.analyst_agent import ExpresionInvalida, calcular, estimar_tiempo_procesamiento, evaluar_expresion
from agents.research_agent import crear_herramienta_busqueda
from tests.fakes import DOC_INGESTA, DOC_LOTE, FakeRetriever


# --- calcular: evaluador seguro por AST ---


@pytest.mark.parametrize(
    ("expresion", "esperado"),
    [("2000000 / 500", 4000), ("(2 + 3) * 4", 20), ("2 ^ 10", 1024), ("-7 // 2", -4), ("10 % 3", 1), ("1.5 * 2", 3.0)],
)
def test_calcular_aritmetica(expresion, esperado):
    assert json.loads(calcular.invoke({"expresion": expresion}))["resultado"] == esperado


@pytest.mark.parametrize(
    "maliciosa",
    [
        "__import__('os').system('echo hackeado')",
        "open('/etc/passwd').read()",
        "().__class__.__bases__[0].__subclasses__()",
        "x + 1",
        "lambda: 1",
        "[1, 2, 3]",
        "'a' * 3",
    ],
)
def test_calcular_rechaza_codigo(maliciosa):
    salida = json.loads(calcular.invoke({"expresion": maliciosa}))
    assert "error" in salida and "resultado" not in salida


def test_calcular_frena_potencias_gigantes_y_division_por_cero():
    with pytest.raises(ExpresionInvalida, match="exponente"):
        evaluar_expresion("9 ** 9 ** 9")
    assert "división por cero" in json.loads(calcular.invoke({"expresion": "1 / 0"}))["error"]
    assert "error" in json.loads(calcular.invoke({"expresion": "1 +" * 100}))


# --- estimar_tiempo_procesamiento: validación con Pydantic ---


def test_estimar_tiempo_calcula_lotes_y_segundos():
    salida = json.loads(
        estimar_tiempo_procesamiento.invoke(
            {"registros": 2_000_000, "tamano_lote": 500, "lotes_por_segundo_por_worker": 5, "workers": 4}
        )
    )
    assert salida["lotes"] == 4000
    assert salida["segundos"] == 200
    assert salida["minutos"] == pytest.approx(3.33)


def test_estimar_tiempo_redondea_lotes_para_arriba():
    salida = json.loads(
        estimar_tiempo_procesamiento.invoke({"registros": 1001, "tamano_lote": 500, "lotes_por_segundo_por_worker": 1})
    )
    assert salida["lotes"] == 3


def test_estimar_tiempo_rechaza_valores_invalidos_como_dato():
    salida = json.loads(
        estimar_tiempo_procesamiento.invoke({"registros": 1000, "tamano_lote": 0, "lotes_por_segundo_por_worker": 5})
    )
    assert "tamano_lote" in salida["error"]


# --- buscar_documentacion ---


async def test_buscar_devuelve_fragmentos_con_fuente():
    retriever = FakeRetriever([DOC_LOTE, DOC_INGESTA])
    herramienta = crear_herramienta_busqueda(retriever)
    salida = json.loads(await herramienta.ainvoke({"consulta": "tamaño de lote por defecto"}))
    assert [r["fuente"] for r in salida["resultados"]] == [
        "caudal-instalacion › Configuración",
        "caudal-rendimiento › Backpressure y tamaño de lote",
    ]
    assert retriever.consultas == ["tamaño de lote por defecto"]


async def test_buscar_devuelve_error_como_dato_si_pinecone_falla():
    herramienta = crear_herramienta_busqueda(FakeRetriever(falla=ConnectionError("Pinecone no responde")))
    salida = json.loads(await herramienta.ainvoke({"consulta": "tamano_lote"}))
    assert "Pinecone no responde" in salida["error"]
    assert "no inventes" in salida["sugerencia"]


async def test_buscar_consulta_vacia():
    herramienta = crear_herramienta_busqueda(FakeRetriever([DOC_LOTE]))
    assert "error" in json.loads(await herramienta.ainvoke({"consulta": "  "}))
