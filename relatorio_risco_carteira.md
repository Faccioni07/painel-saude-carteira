# Relatório de Risco da Carteira

_Gerado em 17/10/2026 18:50 via MCP (`analyze_customer_health_score` + `get_customer_tickets`) para 20 clientes._

## Resumo executivo

- **Receita recorrente analisada:** R$ 5.001.457,10
- **Receita em risco estimada:** R$ 837.203,10 (16,7% da carteira recorrente)
- **Distribuição de saúde:** 5 Crítico · 5 Atenção · 10 Saudável
- **Concentração:** os 5 primeiros clientes respondem por 58% da receita em risco
- **Receita já perdida (contratos suspensos/cancelados):** Química Paraná S.A., Laticínios Campo Bom, Cerâmica Terra Nobre — sem receita recorrente, por isso aparecem com risco zero

## Ranking por Receita em Risco

| # | Cliente | Segmento | Setor | Faturamento (ARR) | Score | Receita em Risco | Principal gargalo de suporte |
|---:|---|---|---|---:|---:|---:|---|
| 1 | Frigorífico Pampa | Mid-Market | Alimentos | R$ 427.227,78 | 🟡 70,0 | **R$ 128.168,33** | Sem gargalo de suporte — risco contratual (Cancelado) |
| 2 | Agro Vale Verde Ltda. | Mid-Market | Agronegócio | R$ 269.087,68 | 🟡 59,1 | **R$ 110.056,86** | Compras — 2 aberto(s), 2 c/ SLA estourado |
| 3 | Distribuidora Atlântica | Mid-Market | Distribuição | R$ 451.597,42 | 🟢 80,9 | **R$ 86.255,11** | RH / Folha — 1 aberto(s), 1 c/ SLA estourado |
| 4 | Energia Solar Sul | SMB | Energia | R$ 91.513,63 | 🔴 10,0 | **R$ 82.362,27** | Fiscal (NF-e) — 3 aberto(s), 1 crítico(s), 3 c/ SLA estourado |
| 5 | Metalúrgica Aurora S.A. | Mid-Market | Indústria | R$ 433.300,66 | 🟢 81,2 | **R$ 81.460,52** | Estoque — 2 aberto(s), 1 c/ SLA estourado |
| 6 | Nova Era Tecnologia | SMB | Tecnologia | R$ 112.839,81 | 🔴 30,0 | **R$ 78.987,87** | Financeiro — 5 aberto(s), 1 crítico(s), 4 c/ SLA estourado |
| 7 | Rodovia Express Transportes | SMB | Logística | R$ 114.854,74 | 🔴 42,5 | **R$ 66.041,48** | Integração API — 2 aberto(s), 1 c/ SLA estourado |
| 8 | Hospital Santa Clara | Mid-Market | Saúde | R$ 209.898,53 | 🟡 70,0 | **R$ 62.969,56** | Sem gargalo de suporte — risco contratual (Em Renegociação, Cancelado) |
| 9 | Autopeças Movimento | Mid-Market | Distribuição | R$ 172.554,71 | 🟡 70,5 | **R$ 50.903,64** | Estoque — 3 aberto(s), 2 c/ SLA estourado |
| 10 | Rede Farma Bem-Estar | Mid-Market | Varejo | R$ 432.494,90 | 🟢 90,0 | **R$ 43.249,49** | Sem gargalo de suporte — risco contratual (Em Renegociação) |
| 11 | Moinho Dourado Alimentos | Mid-Market | Alimentos | R$ 305.734,88 | 🟢 92,6 | **R$ 22.624,38** | Fiscal (NF-e) — 1 aberto(s), 1 c/ SLA estourado |
| 12 | Plásticos Delta Ltda. | Mid-Market | Indústria | R$ 344.932,41 | 🟢 95,4 | **R$ 15.866,89** | RH / Folha — 1 aberto(s) |
| 13 | Mercado Central Atacadista | Mid-Market | Varejo | R$ 230.166,83 | 🟢 97,1 | **R$ 6.674,84** | Contabilidade — 1 aberto(s), 1 c/ SLA estourado |
| 14 | Têxtil Serra Azul | SMB | Indústria | R$ 112.990,29 | 🟢 98,6 | **R$ 1.581,86** | Financeiro — 1 aberto(s) |
| 15 | Química Paraná S.A. | SMB | Indústria | R$ 0,00 | 🔴 0,0 | **R$ 0,00** | Estoque — 3 aberto(s), 1 crítico(s), 3 c/ SLA estourado |
| 16 | Laticínios Campo Bom | SMB | Alimentos | R$ 0,00 | 🔴 11,5 | **R$ 0,00** | Financeiro — 2 aberto(s), 1 crítico(s), 2 c/ SLA estourado |
| 17 | Cerâmica Terra Nobre | SMB | Construção Civil | R$ 0,00 | 🟡 62,3 | **R$ 0,00** | Financeiro — 3 aberto(s), 2 c/ SLA estourado |
| 18 | TransLog Brasil Logística | SMB | Logística | R$ 57.460,08 | 🟢 100,0 | **R$ 0,00** | Sem gargalo relevante |
| 19 | Construtora Horizonte | Enterprise | Construção Civil | R$ 992.094,69 | 🟢 100,0 | **R$ 0,00** | Sem gargalo relevante |
| 20 | Grupo Educacional Saber | Mid-Market | Educação | R$ 242.708,06 | 🟢 100,0 | **R$ 0,00** | Sem gargalo relevante |

**Como ler:** `Receita em Risco = ARR recorrente × (100 − Score) / 100`. ARR recorrente considera apenas contratos *Ativos* e *Em Renegociação*. O gargalo é o módulo com maior peso no backlog aberto (prioridade × tempo em aberto); sem backlog, o módulo com chamados recorrentes.
