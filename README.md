# Buscador de Artigos

Programa para achar artigos em PDF pelo conteúdo ou pelo nome do autor, numa página no navegador. Tudo roda no seu computador. Nenhum arquivo vai para a internet.

## Instalação (uma vez só)

```bash
pip install --user pymupdf
```

## Como usar

```bash
./abrir-buscador.sh
```

O navegador abre em http://127.0.0.1:8765. Na primeira vez o programa lê todos os PDFs, o que leva alguns segundos. Nas vezes seguintes ele só lê os arquivos novos ou alterados. Para sair, aperte Ctrl+C no terminal.

### Ícone na área de trabalho

Para abrir com dois cliques, sem terminal, copie o ícone para a área de trabalho (uma vez só):

```bash
cp ~/buscador-artigos/buscador_artigos.sh "$(xdg-user-dir DESKTOP)/"
```

Dê dois cliques nele e a página abre no navegador. Para fechar, clique em "fechar o buscador" no topo da página. Se o buscador já estiver aberto, o ícone só abre a página de novo.

Para usar outra pasta:

```bash
python3 buscador.py "/caminho/da/pasta"
```

## Como buscar

| Você digita | O que acha |
|---|---|
| `decoherence` | artigos com essa palavra em qualquer página |
| `entanglement entropy` | artigos com as duas palavras |
| `"phase transition"` | a frase exata |
| `autor:Zurek` | artigos desse autor |
| `autor:"Stephen Hawking"` | nome completo |
| `"quantum Hall" autor:Laughlin` | as duas coisas juntas |
| `topolog*` | topology, topological, topologia... |

Acentos são ignorados (`schrodinger` acha "Schrödinger"). Cada resultado mostra trechos com a palavra destacada. Clicar no título ou em "pág. N" abre o PDF direto naquela página.

O botão **🗑 remover**, no canto de cada artigo, manda o PDF para a Lixeira do sistema (pede confirmação antes). Ele sai da pasta e da busca, e dá para recuperar pela Lixeira. Como a pasta é sincronizada pelo Insync, o arquivo também vai para a lixeira do Google Drive.

Com a busca vazia, a página lista todos os artigos em ordem de título.

Artigos novos ou apagados: com o buscador aberto, ele confere a pasta sozinho a cada minuto. Para conferir na hora, clique em "procurar PDFs novos" no topo da página ou no ícone da área de trabalho. Pastas ocultas, como a lixeira do Insync (`.insync-trash`), são ignoradas.

## Limitações desta versão

- PDFs escaneados (só imagem, sem texto) aparecem num aviso no topo da página, mas a busca não enxerga o conteúdo deles.
- O autor vem dos dados internos do PDF e do cabeçalho da primeira página (o texto antes do "Abstract"). Por isso `autor:` pode também achar um nome que aparece no cabeçalho sem ser autor, por exemplo num agradecimento.

O índice fica em `~/.local/share/buscador-artigos/indice.db`. Pode apagar esse arquivo à vontade: o programa o recria.
