#!/usr/bin/env bash
# Abre o Buscador de Artigos na sua pasta de PDFs.
PASTA="/home/samp/Insync/sampaio.mag@gmail.com/Google Drive/a_fisica/Estudos/p_ler"
cd "$(dirname "$0")"
# baixa a versão mais nova, se houver internet (sem internet, segue com a atual)
timeout 20 git pull -q --ff-only >/dev/null 2>&1 || echo "Não consegui buscar atualização; usando a versão atual."
exec python3 buscador.py "$PASTA"
