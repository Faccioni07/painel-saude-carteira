"""
mock_helpdesk_api.py
--------------------
Simula um sistema de Helpdesk de terceiros (SaaS externo) exposto via REST.
Ele NÃO tem acesso ao banco do ERP: a única chave compartilhada entre os
sistemas é o `cliente_id`, exatamente como ocorre em integrações reais.

Execução:
    python mock_helpdesk_api.py
    # ou: uvicorn mock_helpdesk_api:app --port 8000 --reload

Docs interativas: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Final

import uvicorn
from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field

# O Helpdesk conhece apenas os clientes que já abriram conta na plataforma.
MAX_CLIENTE_ID: Final[int] = 20


class Prioridade(str, Enum):
    BAIXA = "Baixa"
    MEDIA = "Média"
    ALTA = "Alta"
    CRITICA = "Crítica"


class StatusTicket(str, Enum):
    ABERTO = "Aberto"
    EM_ANDAMENTO = "Em Andamento"
    AGUARDANDO_CLIENTE = "Aguardando Cliente"
    RESOLVIDO = "Resolvido"


class Ticket(BaseModel):
    ticket_id: str = Field(..., examples=["HD-0003-0001"])
    cliente_id: int
    modulo: str = Field(..., description="Módulo do ERP onde o erro ocorreu")
    titulo: str
    prioridade: Prioridade
    status: StatusTicket
    dias_em_aberto: int = Field(..., ge=0, description="Dias desde a abertura (ou até a resolução)")
    aberto_em: date


class TicketsResponse(BaseModel):
    cliente_id: int
    total_tickets: int
    gerado_em: datetime
    tickets: list[Ticket]


# Catálogo de problemas típicos por módulo de ERP.
PROBLEMAS_POR_MODULO: Final[dict[str, list[str]]] = {
    "Fiscal (NF-e)": ["Rejeição 539 na emissão de NF-e", "Erro de cálculo de ICMS-ST", "Timeout na SEFAZ"],
    "Financeiro": ["Conciliação bancária divergente", "Falha na geração de boletos CNAB", "Baixa duplicada de títulos"],
    "Estoque": ["Saldo negativo após inventário", "Custo médio incorreto", "Lote não localizado"],
    "Faturamento": ["Pedido travado na liberação", "Regra de comissão não aplicada", "Duplicidade de faturas"],
    "Contabilidade": ["Lançamentos sem contrapartida", "Erro no fechamento mensal", "SPED Contábil inconsistente"],
    "RH / Folha": ["eSocial evento S-1200 rejeitado", "Cálculo de férias incorreto", "Ponto não integrado"],
    "Compras": ["Aprovação de pedido não dispara", "Cotação sem fornecedores", "Divergência de NF de entrada"],
    "Integração API": ["Webhook de pedidos falhando", "Token OAuth expirando", "Latência alta no endpoint de produtos"],
}

# Perfis de suporte: geram variedade na carteira (clientes saudáveis vs. problemáticos).
PERFIS: Final[dict[str, dict[str, object]]] = {
    "estavel": {"qtd": (0, 3), "pesos_prioridade": [55, 30, 12, 3], "chance_aberto": 0.25},
    "moderado": {"qtd": (3, 7), "pesos_prioridade": [30, 35, 25, 10], "chance_aberto": 0.50},
    "critico": {"qtd": (6, 12), "pesos_prioridade": [10, 25, 35, 30], "chance_aberto": 0.75},
}

app = FastAPI(
    title="Mock Helpdesk API",
    version="1.0.0",
    description="Sistema de Helpdesk simulado para o portfólio de federação ERP + Suporte via MCP.",
)


def gerar_tickets(cliente_id: int) -> list[Ticket]:
    """
    Gera tickets determinísticos por cliente (mesma seed = mesma resposta),
    simulando um histórico estável entre chamadas.
    """
    rng = random.Random(f"helpdesk-{cliente_id}")
    perfil_nome = rng.choices(list(PERFIS), weights=[50, 30, 20])[0]
    perfil = PERFIS[perfil_nome]
    qtd_min, qtd_max = perfil["qtd"]  # type: ignore[misc]
    hoje = date.today()

    # Clientes problemáticos tendem a concentrar erros em poucos módulos (gargalo sistêmico).
    modulos = list(PROBLEMAS_POR_MODULO)
    modulos_foco = rng.sample(modulos, k=2)

    tickets: list[Ticket] = []
    for seq in range(1, rng.randint(qtd_min, qtd_max) + 1):
        modulo = rng.choice(modulos_foco) if rng.random() < 0.6 else rng.choice(modulos)
        prioridade = rng.choices(list(Prioridade), weights=perfil["pesos_prioridade"])[0]  # type: ignore[arg-type]

        if rng.random() < perfil["chance_aberto"]:  # type: ignore[operator]
            status = rng.choice([StatusTicket.ABERTO, StatusTicket.EM_ANDAMENTO, StatusTicket.AGUARDANDO_CLIENTE])
            dias = rng.randint(0, 45)
            aberto_em = hoje - timedelta(days=dias)
        else:
            status = StatusTicket.RESOLVIDO
            dias = rng.randint(0, 20)  # tempo que levou para ser resolvido
            aberto_em = hoje - timedelta(days=dias + rng.randint(1, 90))

        tickets.append(
            Ticket(
                ticket_id=f"HD-{cliente_id:04d}-{seq:04d}",
                cliente_id=cliente_id,
                modulo=modulo,
                titulo=rng.choice(PROBLEMAS_POR_MODULO[modulo]),
                prioridade=prioridade,
                status=status,
                dias_em_aberto=dias,
                aberto_em=aberto_em,
            )
        )
    return tickets


@app.get("/health", tags=["infra"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tickets/{cliente_id}", response_model=TicketsResponse, tags=["tickets"])
def listar_tickets(cliente_id: int = Path(..., ge=1, description="ID do cliente (chave compartilhada com o ERP)")) -> TicketsResponse:
    if cliente_id > MAX_CLIENTE_ID:
        raise HTTPException(status_code=404, detail=f"Cliente {cliente_id} não encontrado no Helpdesk.")

    tickets = gerar_tickets(cliente_id)
    return TicketsResponse(
        cliente_id=cliente_id,
        total_tickets=len(tickets),
        gerado_em=datetime.now(timezone.utc),
        tickets=tickets,
    )


if __name__ == "__main__":
    uvicorn.run("mock_helpdesk_api:app", host="127.0.0.1", port=8000, reload=False)
