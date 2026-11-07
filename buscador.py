#!/usr/bin/env python3
"""Buscador de artigos: indexa os PDFs de uma pasta e permite buscar
por palavra ou por autor numa página no navegador.

Uso:
    python3 buscador.py "/caminho/da/pasta/com/pdfs"

Depois abra http://127.0.0.1:8765 (o navegador abre sozinho).
"""

import html
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # versões antigas do PyMuPDF
    except ImportError:
        fitz = None
if fitz is None:
    sys.exit("Falta o PyMuPDF. Instale com:  pip install --user pymupdf")

PORTA = 8765
PASTA_DADOS = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "buscador-artigos",
)
ARQUIVO_INDICE = os.path.join(PASTA_DADOS, "indice.db")

RE_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
RE_ARXIV = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
RE_ANO = re.compile(r"D:(\d{4})")
TITULOS_RUINS = re.compile(
    r"^(untitled|microsoft word|document\d*|title)|\.(dvi|tex|pdf|docx?)$", re.I
)

estado = {"indexando": False, "mensagem": "", "pasta": ""}
trava_bd = threading.Lock()


# ---------------------------------------------------------------- índice

def conectar():
    os.makedirs(PASTA_DADOS, exist_ok=True)
    bd = sqlite3.connect(ARQUIVO_INDICE, check_same_thread=False)
    bd.executescript(
        """
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY,
            caminho TEXT UNIQUE,
            mtime REAL,
            titulo TEXT,
            autores TEXT,
            ano TEXT,
            doi TEXT,
            arxiv TEXT,
            paginas INTEGER,
            sem_texto INTEGER
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
            titulo, autores, cabecalho,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS paginas_fts USING fts5(
            texto, doc_id UNINDEXED, pagina UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2'
        );
        """
    )
    return bd


def limpar(texto):
    # NFKC desfaz ligaduras como "ﬁ" -> "fi", comuns em PDFs de LaTeX.
    return unicodedata.normalize("NFKC", texto or "").strip()


def titulo_pela_fonte(pagina):
    """O título costuma ser o texto com a maior fonte da primeira página."""
    trechos = []
    for bloco in pagina.get_text("dict")["blocks"]:
        for linha in bloco.get("lines", []):
            for span in linha["spans"]:
                txt = span["text"].strip()
                if len(txt) > 1 and span["bbox"][1] < pagina.rect.height * 0.6:
                    trechos.append((span["size"], txt))
    if not trechos:
        return ""
    maior = max(t[0] for t in trechos)
    titulo = " ".join(t for s, t in trechos if s >= maior - 0.5)
    return limpar(titulo)[:300]


def extrair(caminho):
    with fitz.open(caminho) as pdf:
        meta = pdf.metadata or {}
        flags = fitz.TEXT_DEHYPHENATE | fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
        paginas = [limpar(p.get_text("text", flags=flags)) for p in pdf]
        titulo = limpar(meta.get("title"))
        if len(titulo) < 6 or TITULOS_RUINS.search(titulo):
            titulo = titulo_pela_fonte(pdf[0]) if len(pdf) else ""
        if not titulo:
            titulo = os.path.splitext(os.path.basename(caminho))[0]

    primeira = paginas[0] if paginas else ""
    corte = re.search(r"\babstract\b|\bresumo\b", primeira, re.I)
    cabecalho = primeira[: corte.start()] if corte and corte.start() > 50 else primeira
    cabecalho = cabecalho[:2500]

    inicio = "\n".join(paginas[:2])
    doi = RE_DOI.search(inicio)
    arxiv = RE_ARXIV.search(inicio)
    ano = ""
    if arxiv:
        aa = re.sub(r"^.*/", "", arxiv.group(1))[:2]
        ano = ("19" if aa > "90" else "20") + aa
    else:
        m = RE_ANO.search(meta.get("creationDate") or "")
        ano = m.group(1) if m else ""

    return {
        "titulo": titulo,
        "autores": limpar(meta.get("author")),
        "ano": ano,
        "doi": doi.group(1).rstrip(".,;)]") if doi else "",
        "arxiv": arxiv.group(1) if arxiv else "",
        "paginas": paginas,
        "cabecalho": cabecalho,
        "sem_texto": int(sum(len(p) for p in paginas) < 20 * max(len(paginas), 1)),
    }


def remover(bd, doc_id):
    bd.execute("DELETE FROM docs WHERE id=?", (doc_id,))
    bd.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
    bd.execute("DELETE FROM paginas_fts WHERE doc_id=?", (doc_id,))


def indexar(bd, pasta):
    estado["indexando"] = True
    try:
        encontrados = {}
        for raiz, _, arquivos in os.walk(pasta):
            for nome in arquivos:
                if nome.lower().endswith(".pdf"):
                    caminho = os.path.join(raiz, nome)
                    try:
                        encontrados[caminho] = os.path.getmtime(caminho)
                    except OSError:
                        pass

        with trava_bd:
            conhecidos = {c: (i, m) for i, c, m in bd.execute("SELECT id, caminho, mtime FROM docs")}
            for caminho, (doc_id, _) in conhecidos.items():
                if caminho not in encontrados:
                    remover(bd, doc_id)
            bd.commit()

        novos = [c for c, m in encontrados.items() if c not in conhecidos or conhecidos[c][1] != m]
        falhas = 0
        for n, caminho in enumerate(sorted(novos), 1):
            estado["mensagem"] = f"Lendo {n} de {len(novos)}: {os.path.basename(caminho)}"
            print(estado["mensagem"], flush=True)
            try:
                info = extrair(caminho)
            except Exception as erro:  # PDF corrompido, protegido etc.
                print(f"  não consegui ler: {erro}", flush=True)
                falhas += 1
                continue
            with trava_bd:
                if caminho in conhecidos:
                    remover(bd, conhecidos[caminho][0])
                cur = bd.execute(
                    "INSERT INTO docs (caminho, mtime, titulo, autores, ano, doi, arxiv, paginas, sem_texto)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (caminho, encontrados[caminho], info["titulo"], info["autores"], info["ano"],
                     info["doi"], info["arxiv"], len(info["paginas"]), info["sem_texto"]),
                )
                doc_id = cur.lastrowid
                bd.execute(
                    "INSERT INTO docs_fts (rowid, titulo, autores, cabecalho) VALUES (?,?,?,?)",
                    (doc_id, info["titulo"], info["autores"], info["cabecalho"]),
                )
                bd.executemany(
                    "INSERT INTO paginas_fts (texto, doc_id, pagina) VALUES (?,?,?)",
                    [(t, doc_id, i + 1) for i, t in enumerate(info["paginas"]) if t],
                )
                bd.commit()
        total = bd.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        estado["mensagem"] = (
            f"{total} artigos no índice ({len(novos) - falhas} lidos agora"
            + (f", {falhas} com erro" if falhas else "") + ")."
        )
        print(estado["mensagem"], flush=True)
    finally:
        estado["indexando"] = False


# ---------------------------------------------------------------- busca

def interpretar(consulta):
    """Separa 'autor:Nome' (ou autor:"Nome Sobrenome") das palavras comuns."""
    autores, termos = [], []
    padrao = re.compile(r'(autor:|author:)?(?:"([^"]+)"|(\S+))', re.I)
    for m in padrao.finditer(consulta):
        valor = (m.group(2) or m.group(3) or "").strip()
        if not valor:
            continue
        (autores if m.group(1) else termos).append((valor, bool(m.group(2))))
    return autores, termos


def para_fts(itens):
    partes = []
    for valor, frase in itens:
        if frase:
            partes.append('"' + valor.replace('"', '""') + '"')
            continue
        prefixo = valor.endswith("*")
        for palavra in re.findall(r"\w+", valor):
            partes.append('"' + palavra + '"')
        if prefixo and partes:
            partes[-1] += "*"
    return " AND ".join(partes)


def marcar(trecho):
    t = html.escape(trecho).replace("\x02", "<mark>").replace("\x03", "</mark>")
    return re.sub(r"\s+", " ", t)


def buscar(bd, consulta):
    autores, termos = interpretar(consulta)
    q_autor, q_termos = para_fts(autores), para_fts(termos)
    if not q_autor and not q_termos:
        return []

    with trava_bd:
        filtro = None
        if q_autor:
            linhas = bd.execute(
                "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?",
                ("{autores cabecalho}: (" + q_autor + ")",),
            ).fetchall()
            filtro = {r[0] for r in linhas}
            if not filtro:
                return []

        resultados = {}
        if q_termos:
            linhas = bd.execute(
                "SELECT doc_id, pagina, bm25(paginas_fts),"
                " snippet(paginas_fts, 0, char(2), char(3), '…', 28)"
                " FROM paginas_fts WHERE paginas_fts MATCH ? ORDER BY bm25(paginas_fts) LIMIT 2000",
                (q_termos,),
            ).fetchall()
            for doc_id, pagina, nota, trecho in linhas:
                if filtro is not None and doc_id not in filtro:
                    continue
                r = resultados.setdefault(doc_id, {"nota": 0.0, "trechos": []})
                r["nota"] += nota  # bm25 é negativo: mais negativo = mais relevante
                if len(r["trechos"]) < 3:
                    r["trechos"].append((pagina, marcar(trecho)))
            # palavra no título vale mais
            for (doc_id,) in bd.execute(
                "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?", ("{titulo}: (" + q_termos + ")",)
            ):
                if doc_id in resultados:
                    resultados[doc_id]["nota"] -= 50
        else:
            resultados = {d: {"nota": 0.0, "trechos": []} for d in filtro}

        saida = []
        for doc_id, r in resultados.items():
            doc = bd.execute(
                "SELECT id, caminho, titulo, autores, ano, doi, arxiv, paginas FROM docs WHERE id=?",
                (doc_id,),
            ).fetchone()
            if doc:
                saida.append((r["nota"], doc, r["trechos"]))
    saida.sort(key=lambda x: (x[0], (x[1][2] or "").lower()))
    return saida


# ---------------------------------------------------------------- páginas

ESTILO = """
:root { --fundo:#f7f6f2; --cartao:#fff; --texto:#1d1d1f; --suave:#666; --borda:#e2e0d8;
        --destaque:#1f5fbf; --marca:#ffe58a; }
@media (prefers-color-scheme: dark) {
  :root { --fundo:#17181a; --cartao:#202226; --texto:#e8e8e8; --suave:#9a9a9a; --borda:#33363b;
          --destaque:#7fb0ff; --marca:#6b5a12; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--fundo); color:var(--texto);
       font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:900px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 16px; }
h1 a { color:inherit; text-decoration:none; }
form { display:flex; gap:8px; }
input[type=search] { flex:1; font-size:18px; padding:10px 14px; border:1px solid var(--borda);
       border-radius:10px; background:var(--cartao); color:var(--texto); }
button { font-size:16px; padding:10px 18px; border:0; border-radius:10px;
         background:var(--destaque); color:#fff; cursor:pointer; }
.ajuda { color:var(--suave); font-size:14px; margin:8px 2px 20px; }
.ajuda code { background:var(--cartao); border:1px solid var(--borda); padding:1px 5px; border-radius:5px; }
.status { color:var(--suave); font-size:14px; margin-bottom:16px; }
.cartao { background:var(--cartao); border:1px solid var(--borda); border-radius:12px;
          padding:14px 16px; margin-bottom:12px; }
.cartao h2 { font-size:17px; margin:0 0 4px; }
.cartao h2 a { color:var(--destaque); text-decoration:none; }
.cartao h2 a:hover { text-decoration:underline; }
.meta { color:var(--suave); font-size:13px; word-break:break-all; }
.trecho { font-size:14px; margin:8px 0 0; }
.trecho a { color:var(--suave); font-size:13px; margin-right:6px; white-space:nowrap; }
mark { background:var(--marca); color:inherit; padding:0 2px; border-radius:3px; }
.acoes a { font-size:13px; color:var(--destaque); margin-right:12px; }
"""


def pagina_html(consulta, corpo):
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Buscador de Artigos</title><style>{ESTILO}</style></head><body><main>
<h1><a href="/">📚 Buscador de Artigos</a></h1>
<form action="/" method="get">
  <input type="search" name="q" value="{html.escape(consulta)}" autofocus
         placeholder='Ex.: decoherence   ou   autor:Zurek   ou   "quantum Hall" autor:Laughlin'>
  <button>Buscar</button>
</form>
<div class="ajuda">Palavras: <code>entanglement entropy</code> · frase exata: <code>"phase transition"</code>
 · autor: <code>autor:Hawking</code> ou <code>autor:"Stephen Hawking"</code> · começo de palavra: <code>topolog*</code></div>
{corpo}</main></body></html>"""


def cartao(doc, trechos, consulta):
    doc_id, caminho, titulo, autores, ano, doi, arxiv, paginas = doc
    _, termos = interpretar(consulta)
    busca = urllib.parse.quote(" ".join(v for v, _ in termos))
    pagina1 = trechos[0][0] if trechos else 1
    link = lambda p: f"/pdf/{doc_id}#page={p}" + (f"&search={busca}" if busca else "")
    meta = " · ".join(x for x in [
        html.escape(autores) if autores else "",
        ano,
        f"{paginas} pág.",
        f'<a href="https://doi.org/{html.escape(doi)}" target="_blank">DOI</a>' if doi else "",
        f'<a href="https://arxiv.org/abs/{arxiv}" target="_blank">arXiv:{arxiv}</a>' if arxiv else "",
    ] if x)
    html_trechos = "".join(
        f'<p class="trecho"><a href="{link(p)}" target="_blank">pág. {p}</a>{t}</p>' for p, t in trechos
    )
    return f"""<div class="cartao">
<h2><a href="{link(pagina1)}" target="_blank">{html.escape(titulo)}</a></h2>
<div class="meta">{meta}</div>
<div class="meta">{html.escape(os.path.relpath(caminho, estado['pasta']))}</div>
{html_trechos}
<div class="acoes" style="margin-top:8px"><a href="/abrir/{doc_id}?p={pagina1}">abrir no leitor de PDF do sistema</a></div>
</div>"""


class Servidor(BaseHTTPRequestHandler):
    bd = None

    def log_message(self, *args):
        pass

    def responder(self, codigo, tipo, conteudo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(conteudo)))
        self.end_headers()
        self.wfile.write(conteudo)

    def redirecionar(self, destino):
        self.send_response(303)
        self.send_header("Location", destino)
        self.end_headers()

    def caminho_doc(self, doc_id):
        with trava_bd:
            linha = self.bd.execute("SELECT caminho FROM docs WHERE id=?", (doc_id,)).fetchone()
        return linha[0] if linha else None

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)

        m = re.fullmatch(r"/pdf/(\d+)", url.path)
        if m:
            caminho = self.caminho_doc(int(m.group(1)))
            if not caminho or not os.path.exists(caminho):
                return self.responder(404, "text/plain; charset=utf-8", "PDF não encontrado".encode())
            with open(caminho, "rb") as f:
                return self.responder(200, "application/pdf", f.read())

        m = re.fullmatch(r"/abrir/(\d+)", url.path)
        if m:
            caminho = self.caminho_doc(int(m.group(1)))
            if caminho:
                subprocess.Popen(["xdg-open", caminho], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.responder(204, "text/plain", b"")

        if url.path == "/sair":
            self.responder(200, "text/html; charset=utf-8", pagina_html("",
                '<div class="cartao">Buscador fechado. Pode fechar esta aba.</div>').encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if url.path == "/reindexar":
            if not estado["indexando"]:
                threading.Thread(target=indexar, args=(self.bd, estado["pasta"]), daemon=True).start()
                time.sleep(0.3)
            return self.redirecionar("/")

        if url.path != "/":
            return self.responder(404, "text/plain; charset=utf-8", "Página não encontrada".encode())

        consulta = (params.get("q") or [""])[0].strip()
        status = html.escape(estado["mensagem"])
        if estado["indexando"]:
            status += ' <a href="/">atualizar</a>'
        else:
            status += ' · <a href="/reindexar">procurar PDFs novos</a> · <a href="/sair">fechar o buscador</a>'
        corpo = f'<div class="status">{status}</div>'

        if consulta:
            resultados = buscar(self.bd, consulta)
            corpo += f'<div class="status">{len(resultados)} artigo(s) encontrado(s) para <b>{html.escape(consulta)}</b></div>'
            corpo += "".join(cartao(doc, trechos, consulta) for _, doc, trechos in resultados)
            if not resultados:
                corpo += '<div class="cartao">Nada encontrado. Tente menos palavras ou o começo da palavra com <code>*</code>.</div>'
        else:
            with trava_bd:
                docs = self.bd.execute(
                    "SELECT id, caminho, titulo, autores, ano, doi, arxiv, paginas FROM docs"
                    " ORDER BY titulo COLLATE NOCASE"
                ).fetchall()
                sem_texto = self.bd.execute("SELECT caminho FROM docs WHERE sem_texto=1").fetchall()
            if sem_texto:
                nomes = ", ".join(html.escape(os.path.basename(c)) for (c,) in sem_texto)
                corpo += (f'<div class="cartao meta">⚠️ {len(sem_texto)} PDF(s) sem texto (provavelmente '
                          f'escaneados, a busca não enxerga o conteúdo): {nomes}</div>')
            corpo += f'<div class="status">Todos os artigos ({len(docs)}), em ordem de título:</div>'
            corpo += "".join(cartao(d, [], "") for d in docs)

        self.responder(200, "text/html; charset=utf-8", pagina_html(consulta, corpo).encode())


def main():
    if len(sys.argv) < 2:
        sys.exit('Uso: python3 buscador.py "/caminho/da/pasta/com/pdfs"')
    pasta = os.path.abspath(os.path.expanduser(sys.argv[1]))
    if not os.path.isdir(pasta):
        sys.exit(f"Pasta não encontrada: {pasta}")
    estado["pasta"] = pasta
    endereco = f"http://127.0.0.1:{PORTA}"
    abrir_navegador = "--sem-navegador" not in sys.argv

    with socket.socket() as teste:
        ja_aberto = teste.connect_ex(("127.0.0.1", PORTA)) == 0
    if ja_aberto:  # o buscador já está rodando: só mostra a página
        print(f"O buscador já está aberto em {endereco}", flush=True)
        if abrir_navegador:
            webbrowser.open(endereco)
        return

    bd = conectar()
    Servidor.bd = bd
    print(f"Pasta: {pasta}\nÍndice: {ARQUIVO_INDICE}", flush=True)
    indexar(bd, pasta)

    servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Servidor)
    print(f"\nPronto! Abra {endereco} no navegador. (Ctrl+C para sair)", flush=True)
    if abrir_navegador:
        threading.Timer(0.5, webbrowser.open, args=(endereco,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\nAté logo!")


if __name__ == "__main__":
    main()
