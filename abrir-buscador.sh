#!/usr/bin/env bash
# Abre o Buscador de Artigos na sua pasta de PDFs.
PASTA="/home/samp/Insync/sampaio.mag@gmail.com/Google Drive/a_fisica/Estudos/p_ler"
cd "$(dirname "$0")"
exec python3 buscador.py "$PASTA"
