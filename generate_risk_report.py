"""
generate_risk_report.py
-----------------------
Cliente MCP (stdio) que gera o Relatório de Risco da Carteira.

Em vez de importar a lógica diretamente, o script sobe o `mcp_server.py` como
subprocesso e conversa com ele via protocolo MCP — exatamente como um agente
(ex. Claude Desktop) faria. Para cada cliente do ERP ele chama:
  • analyze_customer_health_score  -> score, receita e receita em risco
  • get_customer_tickets           -> tickets brutos, para apontar o gargalo

Pré-requisitos:
    python setup_erp_db.py
    python mock_helpdesk_api.py      # em outro terminal

Execução:
    python generate_risk_report.py   # grava relatorio_risco_carteira.md
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server import OPEN_STATUSES, PRIORITY_WEIGHTS, RECURRENT_MODULE_THRESHOLD, SLA_DAYS

BASE_DIR: Final[Path] = Path(__file__).resolve().parent
OUTPUT_PATH: Final[Path] = BASE_DIR / "relatorio_risco_carteira.md"


async def _call(session: ClientSession, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await session.call_tool(tool, arguments)
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        texto = " ".join(getattr(c, "text", "") for c in result.content)
        raise RuntimeError(f"{tool}({arguments}) falhou: {texto}")
    estruturado = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if estruturado:
        # FastMCP embrulha retornos não-objeto em {"result": ...}; dicts vêm direto.
        return estruturado.get("result", estruturado) if set(estruturado) == {"result"} else estruturado
    return json.loads(result.content[0].text)


def principal_gargalo(tickets: list[dict[str, Any]], status_contratos: list[str]) -> str:
    """
    Módulo que mais contribui para o risco operacional, usando o mesmo peso do
    Health Score (prioridade × envelhecimento). Sem backlog aberto, cai para o
    módulo recorrente (>= 3 chamados) — sinal de problema sistêmico já "apagado".
    Sem nada no suporte, explicita quando o score caiu por risco contratual.
    """
    abertos = [t for t in tickets if t["status"] in OPEN_STATUSES]
    if abertos:
        peso: dict[str, float] = defaultdict(float)
        for t in abertos:
            peso[t["modulo"]] += PRIORITY_WEIGHTS.get(t["prioridade"], 1.0) * (1 + min(t["dias_em_aberto"] / 30, 2))
        modulo = max(peso, key=peso.get)  # type: ignore[arg-type]
        do_modulo = [t for t in abertos if t["modulo"] == modulo]
        criticos = sum(t["prioridade"] == "Crítica" for t in do_modulo)
        sla = sum(t["dias_em_aberto"] > SLA_DAYS.get(t["prioridade"], 15) for t in do_modulo)
        detalhes = [f"{len(do_modulo)} aberto(s)"]
        if criticos:
            detalhes.append(f"{criticos} crítico(s)")
        if sla:
            detalhes.append(f"{sla} c/ SLA estourado")
        return f"{modulo} — {', '.join(detalhes)}"

    por_modulo: dict[str, int] = defaultdict(int)
    for t in tickets:
        por_modulo[t["modulo"]] += 1
    recorrentes = {m: n for m, n in por_modulo.items() if n >= RECURRENT_MODULE_THRESHOLD}
    if recorrentes:
        modulo = max(recorrentes, key=recorrentes.get)  # type: ignore[arg-type]
        return f"{modulo} — recorrente ({recorrentes[modulo]} chamados, todos resolvidos)"
    sensiveis = [s for s in status_contratos if s != "Ativo"]
    if sensiveis:
        return f"Sem gargalo de suporte — risco contratual ({', '.join(sensiveis)})"
    return "Sem gargalo relevante"


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _decimal(valor: float, casas: int = 1) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


async def coletar() -> list[dict[str, Any]]:
    params = StdioServerParameters(command=sys.executable, args=[str(BASE_DIR / "mcp_server.py")], cwd=str(BASE_DIR))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        # O universo de clientes vem do ERP, via a própria tool de SQL somente leitura.
        ids = [r["id"] for r in (await _call(session, "execute_erp_query", query="SELECT id FROM clientes ORDER BY id"))["linhas"]]

        linhas: list[dict[str, Any]] = []
        for cliente_id in ids:
            health = await _call(session, "analyze_customer_health_score", cliente_id=cliente_id)
            tickets = (await _call(session, "get_customer_tickets", cliente_id=cliente_id))["tickets"]
            fin, hs, op = health["metricas_financeiras"], health["health_score"], health["metricas_operacionais"]
            linhas.append({
                "id": cliente_id,
                "nome": health["cliente"]["nome"],
                "setor": health["cliente"]["setor"],
                "segmento": fin["segmento_receita"],
                "receita": fin["receita_recorrente_anual"],
                "valor_total": fin["valor_total_contratado"],
                "status_contratos": sorted({c["status_contrato"] for c in fin["contratos"]}),
                "score": hs["score"],
                "classificacao": hs["classificacao"],
                "receita_em_risco": fin["receita_em_risco_estimada"],
                "tickets_abertos": op["tickets_abertos"],
                "gargalo": principal_gargalo(tickets, [c["status_contrato"] for c in fin["contratos"]]),
            })
    return sorted(linhas, key=lambda r: (-r["receita_em_risco"], r["score"]))


def renderizar(linhas: list[dict[str, Any]]) -> str:
    receita_total = sum(r["receita"] for r in linhas)
    risco_total = sum(r["receita_em_risco"] for r in linhas)
    por_classe = {c: [r for r in linhas if r["classificacao"] == c] for c in ("Crítico", "Atenção", "Saudável")}
    perdidos = [r for r in linhas if r["receita"] == 0]
    concentracao = sum(r["receita_em_risco"] for r in linhas[:5]) / risco_total if risco_total else 0

    out = [
        "# Relatório de Risco da Carteira",
        "",
        f"_Gerado em {datetime.now():%d/%m/%Y %H:%M} via MCP (`analyze_customer_health_score` + `get_customer_tickets`) "
        f"para {len(linhas)} clientes._",
        "",
        "## Resumo executivo",
        "",
        f"- **Receita recorrente analisada:** {_brl(receita_total)}",
        f"- **Receita em risco estimada:** {_brl(risco_total)} "
        f"({_decimal(100 * risco_total / receita_total)}% da carteira recorrente)" if receita_total else "- **Receita em risco:** R$ 0,00",
        f"- **Distribuição de saúde:** {len(por_classe['Crítico'])} Crítico · "
        f"{len(por_classe['Atenção'])} Atenção · {len(por_classe['Saudável'])} Saudável",
        f"- **Concentração:** os 5 primeiros clientes respondem por {concentracao:.0%} da receita em risco",
    ]
    if perdidos:
        out.append(
            f"- **Receita já perdida (contratos suspensos/cancelados):** "
            f"{', '.join(r['nome'] for r in perdidos)} — sem receita recorrente, por isso aparecem com risco zero"
        )

    out += [
        "",
        "## Ranking por Receita em Risco",
        "",
        "| # | Cliente | Segmento | Setor | Faturamento (ARR) | Score | Receita em Risco | Principal gargalo de suporte |",
        "|---:|---|---|---|---:|---:|---:|---|",
    ]
    icones = {"Crítico": "🔴", "Atenção": "🟡", "Saudável": "🟢"}
    for pos, r in enumerate(linhas, start=1):
        out.append(
            f"| {pos} | {r['nome']} | {r['segmento']} | {r['setor']} | {_brl(r['receita'])} | "
            f"{icones[r['classificacao']]} {_decimal(r['score'])} | **{_brl(r['receita_em_risco'])}** | {r['gargalo']} |"
        )

    out += [
        "",
        "**Como ler:** `Receita em Risco = ARR recorrente × (100 − Score) / 100`. "
        "ARR recorrente considera apenas contratos *Ativos* e *Em Renegociação*. "
        "O gargalo é o módulo com maior peso no backlog aberto (prioridade × tempo em aberto); "
        "sem backlog, o módulo com chamados recorrentes.",
        "",
    ]
    return "\n".join(out)


def main() -> None:
    linhas = asyncio.run(coletar())
    relatorio = renderizar(linhas)
    OUTPUT_PATH.write_text(relatorio, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(relatorio)
    print(f"\nRelatório salvo em {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
