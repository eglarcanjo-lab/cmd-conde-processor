# Camada de dados do processador — SEMPRE PostgreSQL (Supabase).
# A implementação antiga em Google Sheets (gspread) foi removida na auditoria de out/2026:
# a migração p/ SQL está concluída e a planilha virou só um backup congelado (não é mais
# lida nem gravada). O nome do arquivo fica para não mudar os imports
# (`from sheets_service import ler_aba, ...`). O código antigo está no git (antes da v3.86.0).
from sql_service import ler_aba, ler_aba_meses, sobrescrever_aba, sobrescrever_por_mes, atualizar_status_arquivo  # noqa: F401
