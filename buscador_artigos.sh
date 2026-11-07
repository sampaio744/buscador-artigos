#!/usr/bin/env bash
# Ícone da área de trabalho: abre o Buscador de Artigos sem precisar do terminal.
# Para fechar, use o link "fechar o buscador" no topo da página.
REGISTRO="$HOME/.local/share/buscador-artigos/registro.txt"
mkdir -p "$(dirname "$REGISTRO")"
nohup "$HOME/buscador-artigos/abrir-buscador.sh" > "$REGISTRO" 2>&1 &
