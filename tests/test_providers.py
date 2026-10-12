import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from providers import EXTRA_CONTENT_KEY, ChatOpenAICompatible, build_model, model_name_for

FIRMA = {"google": {"thought_signature": "firma-opaca"}}
VARIABLES = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "LLM_RPM",
             "SUPERVISOR_MODEL", "RESEARCH_MODEL", "ANALYST_MODEL", "SYNTH_MODEL")


@pytest.fixture
def entorno(monkeypatch):
    for var in VARIABLES:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_build_model_sin_key_falla_con_mensaje_claro(entorno):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_model("supervisor")


def test_modelo_por_rol_con_fallback_a_openai_model(entorno):
    entorno.setenv("OPENAI_API_KEY", "clave-de-prueba")
    entorno.setenv("OPENAI_MODEL", "modelo-base")
    entorno.setenv("ANALYST_MODEL", "modelo-analista")
    entorno.setenv("OPENAI_BASE_URL", "http://localhost:9999/v1")
    assert model_name_for("supervisor") == "modelo-base"
    assert model_name_for("analyst") == "modelo-analista"
    model = build_model("analyst")
    assert isinstance(model, ChatOpenAICompatible)
    assert model.model_name == "modelo-analista"
    assert model.openai_api_base == "http://localhost:9999/v1"
    assert model.temperature == 0 and model.max_retries == 0


def test_llm_rpm_comparte_un_limitador_entre_roles(entorno):
    entorno.setenv("OPENAI_API_KEY", "clave-de-prueba")
    entorno.setenv("LLM_RPM", "6")
    a, b = build_model("supervisor"), build_model("research")
    assert a.rate_limiter.requests_per_second == pytest.approx(0.1)
    assert a.rate_limiter is b.rate_limiter


def test_conserva_y_reenvia_la_thought_signature(entorno):
    model = ChatOpenAICompatible(model="m", api_key="clave-de-prueba")
    respuesta = {
        "id": "r1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{
            "index": 0, "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "calcular", "arguments": '{"expresion": "1+1"}'},
                "extra_content": FIRMA,
            }]},
        }],
    }
    mensaje = model._create_chat_result(respuesta).generations[0].message
    assert mensaje.additional_kwargs[EXTRA_CONTENT_KEY] == {"call_1": FIRMA}
    payload = model._get_request_payload([HumanMessage("1+1"), mensaje, ToolMessage("{}", tool_call_id="call_1")])
    assert payload["messages"][1]["tool_calls"][0]["extra_content"] == FIRMA
    assert EXTRA_CONTENT_KEY not in payload["messages"][1]
