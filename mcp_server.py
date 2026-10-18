"""
mcp_server.py
-------------
Servidor MCP (transporte stdio) que atua como CAMADA DE FEDERAÇÃO entre:
  • ERP      -> SQLite local (`erp_mock.db`): cadastro + faturamento
  • Helpdesk -> API REST externa (`mock_helpdesk_api.py`): chamados de suporte

Nenhum dado é copiado entre os sistemas: o servidor consulta cada fonte sob
demanda e faz o JOIN lógico em memória usando `cliente_id` como chave.

Execução (normalmente iniciado pelo cliente MCP, ex. Claude Desktop):
    python mcp_server.py

Variáveis de ambiente opcionais:
    ERP_DB_PATH       caminho do banco (padrão: ./erp_mock.db)
    HELPDESK_API_URL  URL base da API (padrão: http://127.0.0.1:8000)

IMPORTANTE: em stdio, o stdout é o canal do protocolo JSON-RPC.
Todo log vai para stderr — nunca use print() aqui.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx

# Compatibilidade com as duas linhas do SDK oficial:
#   mcp >= 2.0 -> FastMCP foi renomeado para MCPServer (mesma API de tools)
#   mcp 1.x    -> mcp.server.fastmcp.FastMCP
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - fallback para mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #
ERP_DB_PATH: Final[Path] = Path(
    os.getenv("ERP_DB_PATH", str(Path(__file__).resolve().parent / "erp_mock.db"))
)
HELPDESK_API_URL: Final[str] = os.getenv("HELPDESK_API_URL", "http://127.0.0.1:8000").rstrip("/")
HTTP_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(5.0, connect=2.0)
MAX_ROWS: Final[int] = 500

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("erp-helpdesk-mcp")

mcp = FastMCP("erp-helpdesk-federation")


# --------------------------------------------------------------------------- #
# Camada de acesso ao ERP (SQLite, somente leitura)
# --------------------------------------------------------------------------- #
# Ações SQLite permitidas para consultas ad-hoc: apenas leitura.
_READ_ONLY_ACTIONS: Final[frozenset[int]] = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),  # CTEs recursivas
})
_SELECT_PREFIX: Final[re.Pattern[str]] = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def _read_only_authorizer(action: int, *_: Any) -> int:
    """Defesa em profundidade: o próprio engine SQLite nega qualquer escrita/DDL/PRAGMA."""
    return sqlite3.SQLITE_OK if action in _READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def _connect_erp(*, strict_authorizer: bool = False) -> sqlite3.Connection:
    if not ERP_DB_PATH.exists():
        raise ToolError(f"Banco do ERP não encontrado em '{ERP_DB_PATH}'. Execute 'python setup_erp_db.py' primeiro.")
    # mode=ro: o arquivo é aberto em modo somente leitura no nível do SO/driver.
    conn = sqlite3.connect(f"{ERP_DB_PATH.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if strict_authorizer:
        conn.set_authorizer(_read_only_authorizer)
    return conn


def _validate_select(query: str) -> str:
    """Primeira barreira: aceita uma única instrução iniciada por SELECT ou WITH."""
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        raise ToolError("A consulta está vazia.")
    if not _SELECT_PREFIX.match(cleaned):
        raise ToolError("Apenas consultas de leitura (SELECT / WITH ... SELECT) são permitidas.")
    if ";" in cleaned:
        raise ToolError("Envie apenas uma instrução SQL por chamada (múltiplos ';' não são permitidos).")
    return cleaned


def _fetch_customer_financials(cliente_id: int) -> dict[str, Any]:
    """Lado ERP da federação: cadastro + contratos do cliente."""
    try:
        with _connect_erp() as conn:
            cliente = conn.execute(
                "SELECT id, nome, setor, data_cadastro FROM clientes WHERE id = ?", (cliente_id,)
            ).fetchone()
            if cliente is None:
                raise ToolError(f"Cliente {cliente_id} não existe no ERP.")
            contratos = conn.execute(
                "SELECT id, valor_anual, status_contrato FROM faturamento WHERE cliente_id = ? ORDER BY valor_anual DESC",
                (cliente_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.exception("Erro ao consultar o ERP")
        raise ToolError(f"Erro ao consultar o ERP: {exc}") from exc

    return {"cliente": dict(cliente), "contratos": [dict(c) for c in contratos]}


# --------------------------------------------------------------------------- #
# Camada de acesso ao Helpdesk (API REST externa)
# --------------------------------------------------------------------------- #
async def _fetch_tickets(cliente_id: int) -> dict[str, Any]:
    url = f"{HELPDESK_API_URL}/api/tickets/{cliente_id}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as exc:
        raise ToolError(f"Timeout ao consultar o Helpdesk ({url}).") from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ToolError(f"Cliente {cliente_id} não encontrado no Helpdesk.") from exc
        raise ToolError(f"Helpdesk retornou HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
    except httpx.TransportError as exc:
        raise ToolError(
            f"Helpdesk indisponível em {HELPDESK_API_URL}. Suba a API com 'python mock_helpdesk_api.py'. Detalhe: {exc}"
        ) from exc
    except ValueError as exc:  # JSON inválido
        raise ToolError("Resposta do Helpdesk não é um JSON válido.") from exc


# --------------------------------------------------------------------------- #
# Regras de negócio do Health Score
# --------------------------------------------------------------------------- #
OPEN_STATUSES: Final[frozenset[str]] = frozenset({"Aberto", "Em Andamento", "Aguardando Cliente"})
PRIORITY_WEIGHTS: Final[dict[str, float]] = {"Crítica": 12.0, "Alta": 7.0, "Média": 3.0, "Baixa": 1.0}
SLA_DAYS: Final[dict[str, int]] = {"Crítica": 1, "Alta": 3, "Média": 7, "Baixa": 15}
CONTRACT_PENALTY: Final[dict[str, float]] = {"Ativo": 0, "Em Renegociação": 10, "Suspenso": 20, "Cancelado": 30}
RECURRENT_MODULE_THRESHOLD: Final[int] = 3
MAX_OPERATIONAL_PENALTY: Final[float] = 70.0


@dataclass(frozen=True, slots=True)
class RevenueTier:
    nome: str
    receita_minima: float
    multiplicador: float  # quanto o risco operacional pesa para este porte


# Ordem decrescente: o primeiro tier cuja receita mínima é atingida vence.
REVENUE_TIERS: Final[tuple[RevenueTier, ...]] = (
    RevenueTier("Enterprise", 500_000, 1.5),
    RevenueTier("Mid-Market", 150_000, 1.25),
    RevenueTier("SMB", 0, 1.0),
)


@dataclass(slots=True)
class HealthScoreResult:
    score: float
    classificacao: str
    componentes: dict[str, float]
    recomendacoes: list[str] = field(default_factory=list)


def _classify_tier(receita: float) -> RevenueTier:
    return next(t for t in REVENUE_TIERS if receita >= t.receita_minima)


def _compute_health_score(
    receita_recorrente: float,
    status_contratos: list[str],
    tickets: list[dict[str, Any]],
) -> tuple[HealthScoreResult, dict[str, Any]]:
    """
    Lógica de federação: o risco operacional (Helpdesk) é PONDERADO pelo peso
    financeiro do cliente (ERP). O mesmo backlog de tickets é mais grave em um
    Enterprise do que em um SMB, pois a exposição de receita é maior.

    score = 100 - (penalidade_operacional × multiplicador_receita) - penalidade_contratual
    """
    abertos = [t for t in tickets if t.get("status") in OPEN_STATUSES]

    # 1) Backlog: cada ticket aberto pesa pela prioridade e envelhece com o tempo (até 3x).
    penalidade_backlog = sum(
        PRIORITY_WEIGHTS.get(t.get("prioridade", ""), 1.0) * (1 + min(t.get("dias_em_aberto", 0) / 30, 2))
        for t in abertos
    )

    # 2) Recorrência: muitos chamados no mesmo módulo indicam gargalo sistêmico, não incidente isolado.
    por_modulo = Counter(t.get("modulo", "Desconhecido") for t in tickets)
    modulos_recorrentes = {m: n for m, n in por_modulo.items() if n >= RECURRENT_MODULE_THRESHOLD}
    penalidade_recorrencia = 5.0 * len(modulos_recorrentes)

    penalidade_operacional = min(penalidade_backlog + penalidade_recorrencia, MAX_OPERATIONAL_PENALTY)

    # 3) Cruzamento com o ERP: porte financeiro amplifica o risco operacional.
    tier = _classify_tier(receita_recorrente)
    penalidade_ponderada = penalidade_operacional * tier.multiplicador

    # 4) Saúde contratual: considera o pior status entre os contratos do cliente.
    penalidade_contratual = max((CONTRACT_PENALTY.get(s, 0) for s in status_contratos), default=0)

    score = round(max(0.0, min(100.0, 100 - penalidade_ponderada - penalidade_contratual)), 1)
    classificacao = "Saudável" if score >= 75 else "Atenção" if score >= 50 else "Crítico"

    sla_violados = [t for t in abertos if t.get("dias_em_aberto", 0) > SLA_DAYS.get(t.get("prioridade", ""), 15)]
    criticos_abertos = [t for t in abertos if t.get("prioridade") == "Crítica"]

    recomendacoes: list[str] = []
    if criticos_abertos:
        recomendacoes.append(f"Escalar imediatamente {len(criticos_abertos)} ticket(s) crítico(s) em aberto.")
    if sla_violados:
        recomendacoes.append(f"Revisar {len(sla_violados)} ticket(s) com SLA estourado.")
    for modulo, qtd in modulos_recorrentes.items():
        recomendacoes.append(f"Investigar causa-raiz no módulo '{modulo}' ({qtd} chamados).")
    if tier.nome == "Enterprise" and classificacao != "Saudável":
        recomendacoes.append("Conta Enterprise em risco: acionar Customer Success e executivo de conta.")
    if penalidade_contratual > 0:
        recomendacoes.append("Contrato com status sensível: alinhar plano de retenção com o comercial.")
    if not recomendacoes:
        recomendacoes.append("Cliente saudável: oportunidade para upsell ou case de sucesso.")

    resultado = HealthScoreResult(
        score=score,
        classificacao=classificacao,
        componentes={
            "penalidade_backlog": round(penalidade_backlog, 2),
            "penalidade_recorrencia": penalidade_recorrencia,
            "penalidade_operacional": round(penalidade_operacional, 2),
            "multiplicador_receita": tier.multiplicador,
            "penalidade_operacional_ponderada": round(penalidade_ponderada, 2),
            "penalidade_contratual": penalidade_contratual,
        },
        recomendacoes=recomendacoes,
    )

    metricas_operacionais = {
        "total_tickets": len(tickets),
        "tickets_abertos": len(abertos),
        "tickets_criticos_abertos": len(criticos_abertos),
        "tickets_sla_violado": len(sla_violados),
        "abertos_por_prioridade": dict(Counter(t.get("prioridade") for t in abertos)),
        "media_dias_em_aberto": round(sum(t.get("dias_em_aberto", 0) for t in abertos) / len(abertos), 1) if abertos else 0.0,
        "max_dias_em_aberto": max((t.get("dias_em_aberto", 0) for t in abertos), default=0),
        "tickets_por_modulo": dict(por_modulo.most_common()),
        "modulos_recorrentes": modulos_recorrentes,
    }
    return resultado, metricas_operacionais


# --------------------------------------------------------------------------- #
# Ferramentas MCP
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_erp_schema() -> dict[str, Any]:
    """
    Retorna o schema do banco do ERP (tabelas, colunas, tipos, chaves e índices).
    Use antes de montar consultas com `execute_erp_query`.
    """
    try:
        with _connect_erp() as conn:
            tabelas = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            schema: dict[str, Any] = {}
            for tabela in tabelas:
                nome = tabela["name"]
                colunas = conn.execute(f'PRAGMA table_info("{nome}")').fetchall()
                fks = conn.execute(f'PRAGMA foreign_key_list("{nome}")').fetchall()
                total = conn.execute(f'SELECT COUNT(*) FROM "{nome}"').fetchone()[0]
                schema[nome] = {
                    "colunas": [
                        {"nome": c["name"], "tipo": c["type"], "not_null": bool(c["notnull"]), "pk": bool(c["pk"])}
                        for c in colunas
                    ],
                    "foreign_keys": [{"coluna": f["from"], "referencia": f"{f['table']}.{f['to']}"} for f in fks],
                    "total_registros": total,
                    "ddl": tabela["sql"],
                }
    except sqlite3.Error as exc:
        raise ToolError(f"Erro ao ler o schema do ERP: {exc}") from exc

    return {"database": ERP_DB_PATH.name, "tabelas": schema}


@mcp.tool()
def execute_erp_query(query: str) -> dict[str, Any]:
    """
    Executa uma consulta SQL SOMENTE LEITURA (SELECT ou WITH ... SELECT) no ERP (SQLite).
    Retorna no máximo 500 linhas. Instruções de escrita, DDL ou PRAGMA são bloqueadas.

    Args:
        query: Uma única instrução SQL de leitura, no dialeto SQLite.
    """
    sql = _validate_select(query)
    try:
        # Três camadas de proteção: validação textual, conexão mode=ro e authorizer do SQLite.
        with _connect_erp(strict_authorizer=True) as conn:
            cursor = conn.execute(sql)
            colunas = [d[0] for d in cursor.description or []]
            linhas = cursor.fetchmany(MAX_ROWS + 1)
    except sqlite3.DatabaseError as exc:
        raise ToolError(f"Erro na consulta SQL: {exc}") from exc

    truncado = len(linhas) > MAX_ROWS
    return {
        "colunas": colunas,
        "linhas": [dict(r) for r in linhas[:MAX_ROWS]],
        "total_retornado": min(len(linhas), MAX_ROWS),
        "truncado": truncado,
    }


@mcp.tool()
async def get_customer_tickets(cliente_id: int) -> dict[str, Any]:
    """
    Busca no sistema de Helpdesk (API REST externa) os chamados de suporte de um cliente.

    Args:
        cliente_id: ID do cliente (mesma chave usada no ERP).
    """
    if cliente_id < 1:
        raise ToolError("cliente_id deve ser um inteiro positivo.")
    return await _fetch_tickets(cliente_id)


@mcp.tool()
async def analyze_customer_health_score(cliente_id: int) -> dict[str, Any]:
    """
    Calcula o Health Score (0-100) de um cliente federando ERP + Helpdesk.
    Cruza a receita recorrente (ERP) com o backlog e a recorrência de chamados (Helpdesk):
    gargalos de suporte em clientes de alto faturamento geram maior risco.
    Faixas: >=75 Saudável | 50-74 Atenção | <50 Crítico.

    Args:
        cliente_id: ID do cliente a ser analisado.
    """
    if cliente_id < 1:
        raise ToolError("cliente_id deve ser um inteiro positivo.")

    # Consulta as duas fontes em paralelo: a latência total é a da fonte mais lenta.
    erp_task = asyncio.to_thread(_fetch_customer_financials, cliente_id)
    helpdesk_task = _fetch_tickets(cliente_id)
    erp_data, helpdesk_data = await asyncio.gather(erp_task, helpdesk_task)

    contratos: list[dict[str, Any]] = erp_data["contratos"]
    # Receita recorrente = contratos que ainda geram (ou podem gerar) receita.
    receita_recorrente = sum(c["valor_anual"] for c in contratos if c["status_contrato"] in {"Ativo", "Em Renegociação"})
    valor_total = sum(c["valor_anual"] for c in contratos)
    tier = _classify_tier(receita_recorrente)

    resultado, metricas_operacionais = _compute_health_score(
        receita_recorrente=receita_recorrente,
        status_contratos=[c["status_contrato"] for c in contratos],
        tickets=helpdesk_data.get("tickets", []),
    )

    logger.info("Health score cliente=%s score=%s (%s)", cliente_id, resultado.score, resultado.classificacao)

    return {
        "cliente": erp_data["cliente"],
        "metricas_financeiras": {
            "receita_recorrente_anual": round(receita_recorrente, 2),
            "valor_total_contratado": round(valor_total, 2),
            "segmento_receita": tier.nome,
            "qtd_contratos": len(contratos),
            "contratos": contratos,
            # Exposição estimada: quanto da receita está "em jogo" dado o score atual.
            "receita_em_risco_estimada": round(receita_recorrente * (100 - resultado.score) / 100, 2),
        },
        "metricas_operacionais": metricas_operacionais,
        "health_score": {
            "score": resultado.score,
            "classificacao": resultado.classificacao,
            "componentes": resultado.componentes,
        },
        "recomendacoes": resultado.recomendacoes,
        "metadados": {
            "fontes": {"erp": ERP_DB_PATH.name, "helpdesk": HELPDESK_API_URL},
            "calculado_em": datetime.now(timezone.utc).isoformat(),
        },
    }


if __name__ == "__main__":
    logger.info("Iniciando MCP server (stdio) | ERP=%s | Helpdesk=%s", ERP_DB_PATH, HELPDESK_API_URL)
    mcp.run(transport="stdio")
