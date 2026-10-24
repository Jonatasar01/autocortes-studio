"""Processamento em lote a partir de um arquivo de marcação (CSV).

Fluxo por linha: ingestão → corte bruto (stream copy) → transcrição do trecho →
legenda ASS → reenquadramento 9:16 com legenda embutida → validação → /output.
A escolha dos trechos é sempre do usuário; nada aqui decide onde cortar.
"""
