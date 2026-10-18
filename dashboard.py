"""
dashboard.py
------------
Interface visual do Painel de Saúde da Carteira (Streamlit).

Reaproveita o pipeline de `generate_risk_report.py`: sobe o `mcp_server.py` via
stdio e consolida, para cada cliente, o Health Score (ERP SQLite + Helpdesk
FastAPI) e o principal gargalo de suporte.

Pré-requisitos:
    python setup_erp_db.py
    python mock_helpdesk_api.py      # em outro terminal

Execução:
    streamlit run dashboard.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Final

import httpx
import pandas as pd
import streamlit as st

from generate_risk_report import _brl, coletar
from mcp_server import ERP_DB_PATH, HELPDESK_API_URL

CLASSIFICACOES: Final[tuple[str, ...]] = ("Crítico", "Atenção", "Saudável")
ICONES: Final[dict[str, str]] = {"Crítico": "🔴", "Atenção": "🟡", "Saudável": "🟢"}
CACHE_TTL_SEGUNDOS: Final[int] = 300

st.set_page_config(page_title="Painel de Saúde da Carteira", page_icon="📉", layout="wide")


def _checar_fontes() -> list[str]:
    """Valida ERP e Helpdesk antes de subir o MCP, para devolver um erro legível."""
    problemas = []
    if not ERP_DB_PATH.exists():
        problemas.append(f"Banco do ERP não encontrado em `{ERP_DB_PATH}`. Execute `python setup_erp_db.py`.")
    try:
        httpx.get(f"{HELPDESK_API_URL}/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        problemas.append(f"Helpdesk indisponível em `{HELPDESK_API_URL}`. Suba a API com `python mock_helpdesk_api.py`.")
    return problemas


@st.cache_data(ttl=CACHE_TTL_SEGUNDOS, show_spinner="Consultando ERP e Helpdesk via MCP...")
def carregar_carteira() -> tuple[pd.DataFrame, datetime]:
    linhas = asyncio.run(coletar())
    df = pd.DataFrame(linhas)
    df["status_contratos"] = df["status_contratos"].str.join(", ")
    df.insert(0, "posicao", range(1, len(df) + 1))
    df.insert(df.columns.get_loc("score") + 1, "saude", df["classificacao"].map(lambda c: f"{ICONES[c]} {c}"))
    return df, datetime.now()


def _brl_compacto(valor: float) -> str:
    """Formato curto para os cards de KPI."""
    for limite, sufixo in ((1e9, "bi"), (1e6, "mi"), (1e3, "mil")):
        if abs(valor) >= limite:
            return f"R$ {valor / limite:.1f} {sufixo}".replace(".", ",")
    return _brl(valor)


def _pct(parte: float, total: float) -> str:
    return f"{100 * parte / total:.1f}%".replace(".", ",") if total else "0,0%"


# --------------------------------------------------------------------------- #
# Cabeçalho e carga
# --------------------------------------------------------------------------- #
st.title("📉 Painel de Saúde da Carteira")
st.caption("Health Score por cliente a partir do ERP (SQLite) e do Helpdesk (FastAPI), orquestrado via MCP.")

if problemas := _checar_fontes():
    for p in problemas:
        st.error(p)
    st.stop()

with st.sidebar:
    st.header("Dados")
    if st.button("🔄 Atualizar dados", use_container_width=True):
        carregar_carteira.clear()

try:
    carteira, atualizado_em = carregar_carteira()
except Exception as exc:  # noqa: BLE001 — falha do MCP vira mensagem na tela
    st.error(f"Falha ao gerar o relatório de risco: {exc}")
    st.stop()

# --------------------------------------------------------------------------- #
# Filtros
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.caption(f"Atualizado em {atualizado_em:%d/%m/%Y %H:%M:%S} · cache de {CACHE_TTL_SEGUNDOS // 60} min")
    st.header("Filtros")
    classes = st.multiselect("Classificação", CLASSIFICACOES, default=list(CLASSIFICACOES))
    segmentos = st.multiselect("Segmento", sorted(carteira["segmento"].unique()))
    setores = st.multiselect("Setor", sorted(carteira["setor"].unique()))
    faixa_score = st.slider("Score", 0.0, 100.0, (0.0, 100.0), step=1.0)
    busca = st.text_input("Buscar cliente", placeholder="Nome do cliente")

filtro = carteira["classificacao"].isin(classes) & carteira["score"].between(*faixa_score)
if segmentos:
    filtro &= carteira["segmento"].isin(segmentos)
if setores:
    filtro &= carteira["setor"].isin(setores)
if busca:
    filtro &= carteira["nome"].str.contains(busca, case=False, regex=False)
df = carteira[filtro]

# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #
receita_total = df["receita"].sum()
risco_total = df["receita_em_risco"].sum()
criticos = df[df["classificacao"] == "Crítico"]
top5 = df.nlargest(5, "receita_em_risco")["receita_em_risco"].sum()

kpi = {"delta_color": "off", "delta_arrow": "off", "border": True}
k1, k2, k3, k4 = st.columns(4)
k1.metric("Receita Total em Risco", _brl_compacto(risco_total), f"{_pct(risco_total, receita_total)} da receita recorrente",
          help="Soma do faturamento ameaçado, calculado pelo Health Score cruzado com a receita do cliente.", **kpi)
k2.metric("Clientes Críticos", f"{len(criticos)} de {len(df)}",
          f"{_brl_compacto(criticos['receita_em_risco'].sum())} em risco",
          help="Clientes com Health Score na faixa crítica (< 50), exigindo intervenção imediata da equipe de CS.", **kpi)
k3.metric("Receita Recorrente (ARR)", _brl_compacto(receita_total),
          f"Score médio {df['score'].mean():.1f}".replace(".", ",") if len(df) else None,
          help="Faturamento anual recorrente considerando apenas contratos Ativos ou Em Renegociação.", **kpi)
k4.metric("Concentração Top 5", _pct(top5, risco_total), "da receita em risco",
          help="Porcentagem da receita em risco concentrada exclusivamente nos 5 clientes em pior situação no ranking.",
          **kpi)

# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
st.subheader("Ranking por Receita em Risco")
st.caption("Clique no cabeçalho de uma coluna para ordenar. "
           "Receita em Risco = ARR recorrente × (100 − Score) / 100.")

st.dataframe(
    df,
    hide_index=True,
    use_container_width=True,
    height=min(38 * (len(df) + 1) + 2, 740),
    column_order=("posicao", "nome", "segmento", "setor", "receita", "score", "saude", "receita_em_risco",
                  "tickets_abertos", "status_contratos", "gargalo"),
    column_config={
        "posicao": st.column_config.NumberColumn("#", width="small"),
        "nome": st.column_config.TextColumn("Cliente"),
        "segmento": st.column_config.TextColumn("Segmento"),
        "setor": st.column_config.TextColumn("Setor"),
        "receita": st.column_config.NumberColumn("Faturamento (ARR)", format="R$ %,.2f"),
        "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
        "saude": st.column_config.TextColumn("Saúde"),
        "receita_em_risco": st.column_config.NumberColumn("Receita em Risco", format="R$ %,.2f"),
        "tickets_abertos": st.column_config.NumberColumn("Tickets abertos", width="small"),
        "status_contratos": st.column_config.TextColumn("Contratos"),
        "gargalo": st.column_config.TextColumn("Principal gargalo de suporte", width="large"),
    },
)

perdidos = df[df["receita"] == 0]
if not perdidos.empty:
    st.info(f"**Receita já perdida (contratos suspensos/cancelados):** {', '.join(perdidos['nome'])} — "
            "sem receita recorrente, por isso aparecem com risco zero.")

st.download_button(
    "⬇️ Exportar ranking (CSV)",
    df.drop(columns="saude").to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
    file_name=f"ranking_risco_{atualizado_em:%Y%m%d_%H%M}.csv",
    mime="text/csv",
)
