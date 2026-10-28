"""Testa o loop de function calling sem rede: validação/coerção de argumentos contra o schema,
JSON quebrado nos arguments virando erro instrutivo (e não {} silencioso) e a última chamada sem
ferramentas quando as iterações esgotam (em vez do erro seco que jogava o turno fora).

    docker compose exec api-1 python /app/tools/teste_function_calling.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from funcoes import ia_provedores as ip  # noqa: E402

SCHEMA_BUSCA = {
    "name": "busca",
    "description": "busca de teste",
    "input_schema": {
        "type": "object",
        "properties": {
            "termo": {"type": "string"},
            "limite": {"type": "integer"},
            "fonte": {"type": "string", "enum": ["todos", "wpp", "sms"]},
            "ativo": {"type": "boolean"},
            "ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["termo"],
    },
}


def teste_coercion():
    args, erros = ip._validar_argumentos(SCHEMA_BUSCA["input_schema"], {
        "termo": 123, "limite": "10", "fonte": "WPP", "ativo": "sim", "ids": "[1, 2]",
    })
    assert erros == [], erros
    assert args == {"termo": "123", "limite": 10, "fonte": "wpp", "ativo": True, "ids": [1, 2]}, args

    args, erros = ip._validar_argumentos(SCHEMA_BUSCA["input_schema"], {"termo": "x", "limite": "7,5"})
    assert erros and "inteiro" in erros[0], erros
    _, erros = ip._validar_argumentos(SCHEMA_BUSCA["input_schema"], {"termo": "x", "fonte": "telegram"})
    assert erros and "telegram" in erros[0] and "wpp" in erros[0], erros
    print("coerção: string->int/bool/lista, enum case-insensitive; valores sem conserto viram erro")


def teste_estrutura():
    _, erros = ip._validar_argumentos(SCHEMA_BUSCA["input_schema"], {"limite": 5})
    assert erros and "obrigatório" in erros[0].lower() and "termo" in erros[0], erros
    _, erros = ip._validar_argumentos(SCHEMA_BUSCA["input_schema"], {"termo": "x", "id_inquerito": 9})
    assert erros and "id_inquerito" in erros[0] and "termo" in erros[0], erros
    args, erros = ip._validar_argumentos(None, {"qualquer": 1})
    assert erros == [] and args == {"qualquer": 1}
    print("estrutura: obrigatório faltando e campo fora do schema viram erro com os válidos")


def teste_parsear_argumentos():
    assert ip._parsear_argumentos({"a": 1}) == {"a": 1}
    assert ip._parsear_argumentos('{"a": 1}') == {"a": 1}
    assert ip._parsear_argumentos(None) == {}
    assert ip._parsear_argumentos("") == {}
    assert ip._parsear_argumentos("{'a': ") is None
    assert ip._parsear_argumentos("[1, 2]") is None
    print("parse: JSON quebrado ou não-objeto vira None (erro instrutivo), não {} silencioso")


def teste_executar_ferramenta_validada():
    chamadas = []
    funcoes = {"busca": lambda **kw: chamadas.append(kw) or {"ok": True}}

    r = ip._executar_ferramenta("busca", None, funcoes, esquema=SCHEMA_BUSCA["input_schema"])
    assert "JSON" in r["erro"] and not chamadas, r

    r = ip._executar_ferramenta("busca", {"limite": "abc"}, funcoes, esquema=SCHEMA_BUSCA["input_schema"])
    assert "inválidos" in r["erro"] and not chamadas, r

    r = ip._executar_ferramenta("busca", {"termo": "x", "limite": "3"}, funcoes,
                                esquema=SCHEMA_BUSCA["input_schema"])
    assert r == {"ok": True} and chamadas == [{"termo": "x", "limite": 3}], (r, chamadas)
    print("execução: chamada inválida não roda; argumentos chegam corrigidos à função")


class _PostFalso:
    """Substitui _post_json devolvendo respostas na ordem; grava os corpos recebidos."""

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.corpos = []

    def __call__(self, url, cabecalhos, corpo, timeout=None, contexto_ssl=None):
        self.corpos.append(json.loads(json.dumps(corpo, default=str)))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def _openai_tool_call(argumentos, id_chamada="c1"):
    return {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": id_chamada, "type": "function", "function": {
            "name": "busca", "arguments": argumentos if isinstance(argumentos, str)
            else json.dumps(argumentos)}}],
    }


def teste_loop_openai_args_invalidos():
    chamadas = []
    funcoes = {"busca": lambda **kw: chamadas.append(kw) or {"ok": True}}
    post = _PostFalso([
        {"choices": [{"message": _openai_tool_call("{'termo': quebrado")}]},
        {"choices": [{"message": _openai_tool_call({"termo": 123, "limite": "5"}, id_chamada="c2")}]},
        {"choices": [{"message": {"role": "assistant", "content": "fim"}}]},
    ])
    original, ip._post_json = ip._post_json, post
    try:
        texto = ip._chamar_openai_compativel(
            "http://fake", "", "m", [{"role": "user", "content": "oi"}], None,
            [SCHEMA_BUSCA], funcoes,
        )
    finally:
        ip._post_json = original
    assert texto == "fim", texto
    tool_msgs = [m for m in post.corpos[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 2, tool_msgs
    assert "JSON" in tool_msgs[0]["content"], tool_msgs[0]
    # só a 2ª chamada (a corrigida) executou; a de JSON quebrado virou erro sem rodar
    assert chamadas == [{"termo": "123", "limite": 5}], chamadas
    print("loop openai: args quebrados viram erro p/ IA; args errados chegam corrigidos à função")


def teste_loop_openai_esgota_iteracoes():
    funcoes = {"busca": lambda **kw: {"ok": True}}
    post = _PostFalso([
        {"choices": [{"message": _openai_tool_call({"termo": "x"})}]},
        {"choices": [{"message": _openai_tool_call({"termo": "y"}, id_chamada="c2")}]},
        {"choices": [{"message": {"role": "assistant", "content": "resposta final com o que achei"}}]},
    ])
    original, ip._post_json = ip._post_json, post
    try:
        texto = ip._chamar_openai_compativel(
            "http://fake", "", "m", [{"role": "user", "content": "oi"}], None,
            [SCHEMA_BUSCA], funcoes, max_iteracoes=2,
        )
    finally:
        ip._post_json = original
    assert texto == "resposta final com o que achei", texto
    corpo_final = post.corpos[-1]
    assert "tools" not in corpo_final, "a última chamada tem de sair sem tools"
    assert corpo_final["messages"][-1]["role"] == "user"
    assert "NÃO chame mais ferramentas" in corpo_final["messages"][-1]["content"]
    print("loop openai: esgotadas as rodadas, UMA última chamada sem tools fecha o turno")


def teste_loop_openai_esgota_e_falha():
    funcoes = {"busca": lambda **kw: {"ok": True}}
    post = _PostFalso([
        {"choices": [{"message": _openai_tool_call({"termo": "x"})}]},
        ip.ErroProvedorIA("provedor caiu"),
    ])
    original, ip._post_json = ip._post_json, post
    try:
        ip._chamar_openai_compativel(
            "http://fake", "", "m", [{"role": "user", "content": "oi"}], None,
            [SCHEMA_BUSCA], funcoes, max_iteracoes=1,
        )
        raise AssertionError("devia ter levantado ErroProvedorIA")
    except ip.ErroProvedorIA as exc:
        assert "não terminou a resposta" in str(exc), exc
    finally:
        ip._post_json = original
    print("loop openai: se até a chamada final falhar, o erro de sempre volta")


def teste_loop_anthropic_esgota_iteracoes():
    funcoes = {"busca": lambda **kw: {"ok": True}}
    post = _PostFalso([
        {"stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "busca", "input": {"termo": "x"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "fechou"}]},
    ])
    original, ip._post_json = ip._post_json, post
    try:
        texto = ip._chamar_anthropic(
            "http://fake", "k", "m", [{"role": "user", "content": "oi"}], "sistema",
            [SCHEMA_BUSCA], funcoes, max_iteracoes=1,
        )
    finally:
        ip._post_json = original
    assert texto == "fechou", texto
    corpo_final = post.corpos[-1]
    assert "tools" not in corpo_final
    assert corpo_final["system"][0]["text"] == "sistema"
    ultima_msg = corpo_final["messages"][-1]
    assert ultima_msg["role"] == "user" and "NÃO chame mais ferramentas" in ultima_msg["content"][0]["text"]
    print("loop anthropic: mesma saída digna ao esgotar as rodadas, com system preservado")


def main():
    teste_coercion()
    teste_estrutura()
    teste_parsear_argumentos()
    teste_executar_ferramenta_validada()
    teste_loop_openai_args_invalidos()
    teste_loop_openai_esgota_iteracoes()
    teste_loop_openai_esgota_e_falha()
    teste_loop_anthropic_esgota_iteracoes()
    print("FUNCTION CALLING OK")


if __name__ == "__main__":
    main()
