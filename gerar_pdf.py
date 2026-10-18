"""
gerar_pdf.py
------------
Converte o `MANUAL_COMPLETO.md` em `MANUAL_COMPLETO.pdf` com layout de
documento oficial (capa, tipografia sans-serif, código destacado e diagramas).

Pipeline:
  1. Markdown -> HTML com `markdown` (tabelas, código cercado, âncoras) e
     `pygments` (realce de sintaxe).
  2. Blocos ```mermaid viram diagramas renderizados pelo Mermaid.js.
  3. O HTML é impresso em PDF pelo Playwright, usando o Edge/Chrome já
     instalado na máquina (sem baixar um navegador extra).
  4. Capa (sem rodapé) e miolo (numerado) são unidos com `pypdf`.

Dependências (só para a documentação):
    pip install markdown pygments playwright pypdf

Execução:
    python gerar_pdf.py
"""

from __future__ import annotations

import html
import io
import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Final

import markdown
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pygments.formatters import HtmlFormatter
from pypdf import PdfReader, PdfWriter

BASE_DIR: Final[Path] = Path(__file__).resolve().parent
ORIGEM: Final[Path] = BASE_DIR / "MANUAL_COMPLETO.md"
DESTINO: Final[Path] = BASE_DIR / "MANUAL_COMPLETO.pdf"
CANAIS_NAVEGADOR: Final[tuple[str, ...]] = ("msedge", "chrome")
MERMAID_JS: Final[str] = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"
MESES: Final[tuple[str, ...]] = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                                  "agosto", "setembro", "outubro", "novembro", "dezembro")

_MERMAID_BLOCO: Final[re.Pattern[str]] = re.compile(r"^```mermaid\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def slug_github(texto: str, separador: str = "-") -> str:
    """Mesma regra de âncoras do GitHub, para os links do Sumário funcionarem no PDF."""
    texto = re.sub(r"<[^>]+>", "", texto).strip().lower()
    texto = re.sub(r"[^\w\- ]", "", texto)
    return texto.replace(" ", separador)


_ITEM_LISTA: Final[re.Pattern[str]] = re.compile(r"^(\s*)([-*+]|\d+\.)\s")


def normalizar_listas(fonte: str) -> str:
    """
    Aproxima o Python-Markdown do dialeto do GitHub:
      • insere linha em branco antes de uma lista colada a um parágrafo;
      • dobra a indentação de sub-itens (GitHub aceita 2 espaços, Python-Markdown exige 4).
    """
    saida: list[str] = []
    em_codigo = False
    anterior = ""
    for linha in fonte.splitlines():
        if linha.lstrip().startswith("```"):
            em_codigo = not em_codigo
        elif not em_codigo and (item := _ITEM_LISTA.match(linha)):
            if anterior.strip() and not _ITEM_LISTA.match(anterior):
                saida.append("")
            if recuo := item.group(1):
                linha = recuo * 2 + linha.lstrip()
        saida.append(linha)
        anterior = linha
    return "\n".join(saida) + "\n"


def markdown_para_html(fonte: str) -> tuple[str, str]:
    """Retorna (título do documento, corpo HTML)."""
    titulo_match = re.match(r"^#\s+(.+)\n", fonte)
    titulo = titulo_match.group(1).strip() if titulo_match else "Manual"
    if titulo_match:
        fonte = fonte[titulo_match.end():]  # o título vai para a capa

    # Mermaid sai do fluxo do Markdown e volta depois como <pre class="mermaid">.
    diagramas: list[str] = []

    def _guardar(m: re.Match[str]) -> str:
        diagramas.append(m.group(1))
        return f"\n\nMERMAIDPLACEHOLDER{len(diagramas) - 1}\n\n"

    fonte = normalizar_listas(_MERMAID_BLOCO.sub(_guardar, fonte))

    corpo = markdown.markdown(
        fonte,
        extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists"],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "codehilite"},
            "toc": {"slugify": slug_github},
        },
    )
    for i, codigo in enumerate(diagramas):
        corpo = corpo.replace(
            f"<p>MERMAIDPLACEHOLDER{i}</p>",
            f'<figure class="diagrama"><pre class="mermaid">{html.escape(codigo)}</pre></figure>',
        )
    return titulo, corpo


CSS: Final[str] = """
:root {
  --tinta: #1f2937; --suave: #4b5563; --linha: #e5e7eb;
  --marca: #1e3a8a; --marca-clara: #eef2ff; --destaque: #2563eb;
  --codigo-fundo: #f6f8fa; --codigo-borda: #d0d7de;
}
@page { size: A4; margin: 22mm 20mm 22mm 20mm; }
@page :first { margin: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Inter", "Segoe UI", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.65; color: var(--tinta); margin: 0;
  font-feature-settings: "cv11", "ss01";
}

/* ---------- Capa ---------- */
.capa {
  height: 297mm; width: 210mm; position: relative; overflow: hidden;
  background: linear-gradient(160deg, #0f1e4a 0%, #1e3a8a 55%, #2563eb 100%);
  color: #fff; padding: 38mm 24mm; break-after: page;
}
.capa::after {
  content: ""; position: absolute; right: -60mm; bottom: -60mm; width: 170mm; height: 170mm;
  border-radius: 50%; border: 22mm solid rgba(255,255,255,0.06);
}
.capa .rotulo { font-size: 9.5pt; letter-spacing: .22em; text-transform: uppercase; opacity: .75; }
.capa h1 { font-size: 38pt; line-height: 1.1; margin: 10mm 0 6mm; font-weight: 800; letter-spacing: -0.02em; border: 0; color: #fff; }
.capa .subtitulo { font-size: 14pt; font-weight: 400; opacity: .9; max-width: 140mm; line-height: 1.45; }
.capa .barra { width: 28mm; height: 2.2mm; background: #93c5fd; margin: 14mm 0; border-radius: 2mm; }
.capa .stack { font-size: 10pt; opacity: .85; letter-spacing: .04em; }
.capa .rodape { position: absolute; left: 24mm; bottom: 26mm; font-size: 9.5pt; opacity: .8; line-height: 1.7; }

/* ---------- Títulos ---------- */
h1, h2, h3, h4 { color: var(--marca); line-height: 1.25; break-after: avoid; font-weight: 700; }
h1 {
  font-size: 22pt; margin: 0 0 8mm; padding-bottom: 3mm; border-bottom: 1.2mm solid var(--destaque);
  break-before: page; letter-spacing: -0.01em;
}
h2 { font-size: 15pt; margin: 9mm 0 3mm; padding-left: 3mm; border-left: 1.2mm solid var(--destaque); }
h3 { font-size: 12pt; margin: 6mm 0 2mm; color: #1e40af; }
h4 { font-size: 10.5pt; margin: 5mm 0 2mm; }
.conteudo > h2:first-child { margin-top: 0; }
p { margin: 0 0 3mm; orphans: 3; widows: 3; }
a { color: var(--destaque); text-decoration: none; }
strong { color: #111827; }
hr { border: 0; border-top: 1px solid var(--linha); margin: 7mm 0; }
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { margin-bottom: 1mm; }

/* ---------- Citações (callouts) ---------- */
blockquote {
  margin: 4mm 0; padding: 3mm 5mm; background: var(--marca-clara);
  border-left: 1.2mm solid var(--destaque); border-radius: 0 2mm 2mm 0; color: #1e3a8a;
  break-inside: avoid;
}
blockquote p:last-child { margin-bottom: 0; }

/* ---------- Tabelas ---------- */
table { width: 100%; border-collapse: collapse; margin: 3mm 0 5mm; font-size: 9.3pt; }
thead th {
  background: var(--marca); color: #fff; text-align: left; font-weight: 600;
  padding: 2mm 3mm;
}
td { padding: 1.8mm 3mm; border-bottom: 1px solid var(--linha); vertical-align: top; }
tbody tr:nth-child(even) td { background: #f9fafb; }
tr { break-inside: avoid; }
thead { display: table-header-group; }

/* ---------- Código ---------- */
code {
  font-family: "JetBrains Mono", Consolas, monospace; font-size: 8.8pt;
  background: #eef1f5; color: #b4235a; padding: .3mm 1.2mm; border-radius: 1mm;
}
.codehilite, pre {
  background: var(--codigo-fundo); border: 1px solid var(--codigo-borda);
  border-left: 1.2mm solid var(--destaque); border-radius: 1.5mm;
  margin: 3mm 0 5mm; break-inside: avoid;
}
.codehilite pre, pre { margin: 0; padding: 3mm 4mm; border: 0; background: none; white-space: pre-wrap; word-break: break-word; }
.codehilite code, pre code { background: none; color: inherit; padding: 0; font-size: 8.6pt; line-height: 1.55; }
.codehilite .err { border: 0 !important; background: none !important; color: inherit !important; }

/* ---------- Diagramas ---------- */
figure.diagrama {
  margin: 4mm 0 6mm; padding: 4mm; border: 1px solid var(--linha); border-radius: 2mm;
  background: #fcfcfd; text-align: center; break-inside: avoid;
}
pre.mermaid { background: none; border: 0; padding: 0; margin: 0; white-space: pre; }
pre.mermaid svg { max-width: 100%; height: auto; max-height: 210mm; }
pre.mermaid svg[aria-roledescription^="flowchart"] { width: 100%; max-width: 100% !important; }
"""


def montar_documento(titulo: str, corpo: str) -> str:
    hoje = date.today()
    data_extenso = f"{MESES[hoje.month - 1].capitalize()} de {hoje.year}"
    estilo_codigo = HtmlFormatter(style="friendly").get_style_defs(".codehilite")
    nome, _, subtitulo = titulo.partition("—")
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>{html.escape(titulo)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{CSS}
{estilo_codigo}</style>
</head>
<body>
<section class="capa">
  <div class="rotulo">Documentação técnica · Portfólio</div>
  <h1>{html.escape(nome.strip())}</h1>
  <div class="subtitulo">{html.escape(subtitulo.strip() or "Manual")}: arquitetura, regras de negócio
    e guia de operação do Health Score B2B com federação ERP + Helpdesk via MCP.</div>
  <div class="barra"></div>
  <div class="stack">Python · Model Context Protocol · FastAPI · SQLite · Streamlit</div>
  <div class="rodape">Versão 1.0<br>{data_extenso}</div>
</section>
<main class="conteudo">
{corpo}
</main>
<script src="{MERMAID_JS}"></script>
<script>
  // Configura já (síncrono), senão o Mermaid desenha sozinho no "load" com o tema padrão.
  window.mermaid?.initialize({{ startOnLoad: false, theme: "neutral", securityLevel: "strict",
    fontFamily: "Inter, Segoe UI, sans-serif", flowchart: {{ htmlLabels: true, curve: "basis" }} }});
  window.__pronto = (async () => {{
    // A fonte precisa estar carregada ANTES do Mermaid medir os rótulos, senão o texto é cortado.
    await Promise.all(["400", "600"].map(p => document.fonts.load(p + " 16px Inter")));
    await document.fonts.ready;
    if (window.mermaid) {{
      await mermaid.run({{ querySelector: "pre.mermaid" }});
    }}
    return true;
  }})();
</script>
</body>
</html>"""


RODAPE: Final[str] = """
<div style="width:100%; font-family:'Segoe UI',Arial,sans-serif; font-size:8pt; color:#6b7280;
            padding:0 20mm; display:flex; justify-content:space-between;">
  <span>Painel de Saúde da Carteira · Manual Completo</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>"""


def imprimir_pdf(documento_html: str, destino: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        pagina_html = Path(tmp) / "manual.html"
        pagina_html.write_text(documento_html, encoding="utf-8")

        with sync_playwright() as p:
            navegador = None
            for canal in CANAIS_NAVEGADOR:
                try:
                    navegador = p.chromium.launch(channel=canal)
                    break
                except PlaywrightError:
                    continue
            if navegador is None:
                raise SystemExit("Nenhum navegador compatível encontrado (instale o Edge ou o Chrome).")

            pagina = navegador.new_page()
            pagina.goto(pagina_html.as_uri(), wait_until="networkidle")
            pagina.evaluate("window.__pronto")

            # A capa sai sem rodapé; o miolo, com numeração. Depois os dois são unidos.
            capa = pagina.pdf(format="A4", print_background=True, prefer_css_page_size=True, page_ranges="1")
            pagina.add_style_tag(content=".capa { display: none; } @page :first { margin: 22mm 20mm; }")
            miolo = pagina.pdf(
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=RODAPE,
            )
            navegador.close()

    escritor = PdfWriter()
    for parte in (capa, miolo):
        escritor.append(PdfReader(io.BytesIO(parte)))
    escritor.add_metadata({"/Title": "Painel de Saúde da Carteira — Manual Completo"})
    with destino.open("wb") as arquivo:
        escritor.write(arquivo)


def main() -> None:
    if not ORIGEM.exists():
        raise SystemExit(f"Arquivo não encontrado: {ORIGEM}")
    titulo, corpo = markdown_para_html(ORIGEM.read_text(encoding="utf-8"))
    imprimir_pdf(montar_documento(titulo, corpo), DESTINO)
    print(f"PDF gerado em {DESTINO}", file=sys.stderr)


if __name__ == "__main__":
    main()
