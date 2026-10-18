"""
setup_erp_db.py
---------------
Cria e popula o banco SQLite `erp_mock.db`, que simula o ERP (fonte de verdade
para dados cadastrais e financeiros dos clientes B2B).

Execução:
    python setup_erp_db.py

O script é idempotente: recria as tabelas a cada execução e usa uma seed fixa,
então os dados gerados são sempre os mesmos (útil para demos reproduzíveis).
"""

from __future__ import annotations

import logging
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final

DB_PATH: Final[Path] = Path(__file__).resolve().parent / "erp_mock.db"
SEED: Final[int] = 42

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("setup_erp_db")

SCHEMA_SQL: Final[str] = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS faturamento;
DROP TABLE IF EXISTS clientes;

CREATE TABLE clientes (
    id            INTEGER PRIMARY KEY,
    nome          TEXT    NOT NULL UNIQUE,
    setor         TEXT    NOT NULL,
    data_cadastro TEXT    NOT NULL            -- ISO-8601 (YYYY-MM-DD)
);

CREATE TABLE faturamento (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id      INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    valor_anual     REAL    NOT NULL CHECK (valor_anual >= 0),   -- ARR do contrato (R$)
    status_contrato TEXT    NOT NULL CHECK (
        status_contrato IN ('Ativo', 'Em Renegociação', 'Suspenso', 'Cancelado')
    )
);

CREATE INDEX idx_faturamento_cliente ON faturamento (cliente_id);
"""

# Carteira B2B fictícia: o ID precisa coincidir com o usado pela API de Helpdesk,
# pois é a chave de federação entre os dois sistemas.
CLIENTES_BASE: Final[list[tuple[str, str]]] = [
    ("Metalúrgica Aurora S.A.", "Indústria"),
    ("Agro Vale Verde Ltda.", "Agronegócio"),
    ("TransLog Brasil Logística", "Logística"),
    ("Rede Farma Bem-Estar", "Varejo"),
    ("Construtora Horizonte", "Construção Civil"),
    ("Distribuidora Atlântica", "Distribuição"),
    ("Têxtil Serra Azul", "Indústria"),
    ("Nova Era Tecnologia", "Tecnologia"),
    ("Laticínios Campo Bom", "Alimentos"),
    ("Hospital Santa Clara", "Saúde"),
    ("Plásticos Delta Ltda.", "Indústria"),
    ("Mercado Central Atacadista", "Varejo"),
    ("Química Paraná S.A.", "Indústria"),
    ("Rodovia Express Transportes", "Logística"),
    ("Grupo Educacional Saber", "Educação"),
    ("Cerâmica Terra Nobre", "Construção Civil"),
    ("Autopeças Movimento", "Distribuição"),
    ("Frigorífico Pampa", "Alimentos"),
    ("Energia Solar Sul", "Energia"),
    ("Moinho Dourado Alimentos", "Alimentos"),
]

# Distribuição realista de status: a maioria da carteira é ativa.
STATUS_PESOS: Final[dict[str, int]] = {
    "Ativo": 70,
    "Em Renegociação": 15,
    "Suspenso": 8,
    "Cancelado": 7,
}


@dataclass(frozen=True, slots=True)
class Cliente:
    id: int
    nome: str
    setor: str
    data_cadastro: str


@dataclass(frozen=True, slots=True)
class Contrato:
    cliente_id: int
    valor_anual: float
    status_contrato: str


def gerar_clientes(rng: random.Random) -> list[Cliente]:
    """Gera clientes com datas de cadastro distribuídas nos últimos ~6 anos."""
    hoje = date.today()
    clientes: list[Cliente] = []
    for idx, (nome, setor) in enumerate(CLIENTES_BASE, start=1):
        cadastro = hoje - timedelta(days=rng.randint(90, 6 * 365))
        clientes.append(Cliente(idx, nome, setor, cadastro.isoformat()))
    return clientes


def gerar_contratos(rng: random.Random, clientes: list[Cliente]) -> list[Contrato]:
    """
    Cada cliente tem um contrato principal; ~25% possuem um contrato adicional
    (ex.: módulo extra de BI ou Fiscal), o que permite testar agregações (SUM/GROUP BY).
    """
    status_opcoes = list(STATUS_PESOS)
    status_pesos = list(STATUS_PESOS.values())
    contratos: list[Contrato] = []

    for cliente in clientes:
        # Segmentação de porte: SMB, Mid-Market e Enterprise.
        porte = rng.choices(["SMB", "MID", "ENT"], weights=[40, 40, 20])[0]
        faixa = {"SMB": (48_000, 150_000), "MID": (150_000, 500_000), "ENT": (500_000, 1_800_000)}[porte]

        contratos.append(
            Contrato(
                cliente_id=cliente.id,
                valor_anual=round(rng.uniform(*faixa), 2),
                status_contrato=rng.choices(status_opcoes, weights=status_pesos)[0],
            )
        )
        if rng.random() < 0.25:
            contratos.append(
                Contrato(
                    cliente_id=cliente.id,
                    valor_anual=round(rng.uniform(18_000, 120_000), 2),
                    status_contrato=rng.choices(status_opcoes, weights=status_pesos)[0],
                )
            )
    return contratos


def criar_banco(db_path: Path = DB_PATH, seed: int = SEED) -> None:
    rng = random.Random(seed)
    clientes = gerar_clientes(rng)
    contratos = gerar_contratos(rng, clientes)

    try:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.executemany(
                "INSERT INTO clientes (id, nome, setor, data_cadastro) VALUES (?, ?, ?, ?)",
                [(c.id, c.nome, c.setor, c.data_cadastro) for c in clientes],
            )
            conn.executemany(
                "INSERT INTO faturamento (cliente_id, valor_anual, status_contrato) VALUES (?, ?, ?)",
                [(c.cliente_id, c.valor_anual, c.status_contrato) for c in contratos],
            )
            arr_total = conn.execute(
                "SELECT ROUND(SUM(valor_anual), 2) FROM faturamento WHERE status_contrato = 'Ativo'"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        logger.error("Falha ao criar o banco do ERP: %s", exc)
        raise SystemExit(1) from exc

    logger.info("Banco criado em: %s", db_path)
    logger.info("Clientes: %d | Contratos: %d | ARR ativo: R$ %s",
                len(clientes), len(contratos), f"{arr_total:,.2f}")


if __name__ == "__main__":
    criar_banco()
    sys.exit(0)
