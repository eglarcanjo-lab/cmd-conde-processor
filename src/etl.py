# ETL — carga inicial Google Sheets → PostgreSQL (Supabase).
# Roda pelo endpoint POST /api/migrar/sheets-para-sql (uma vez).
#
# Estratégia (ver Plano_Migracao_Sheets_para_SQL.pdf):
#   • Lê cada aba com UNFORMATTED_VALUE → números vêm como número (sem locale BR),
#     datas gravadas como texto (RAW) vêm como texto.
#   • CADASTRO: tabelas já existem (schema.sql, tipadas) → TRUNCATE + INSERT.
#   • COMPUTADAS/IMPORTADAS: DROP + CREATE (todas as colunas TEXT, lossless) + INSERT.
#   • Abas órfãs são ignoradas.
# Re-executável (idempotente): cada run recria/recarrega.
import json
from psycopg2.extras import execute_values, Json
from sheets_service import get_sheet, _com_retry
from db import get_conn

# Tabelas de cadastro (DDL explícito no schema.sql — tipadas, com PK).
CADASTRO = {
    "usuarios", "reset_solicitacoes", "incidentes", "avisos", "popups", "incentivos",
    "incentivos_resultados", "sku_foco", "metas", "spo_metas", "spo_desafios",
    "configuracoes", "uso_app", "uso_telas", "inadimplencia_real",
}

# Abas que não devem ir para o SQL (já removidas do fluxo — ver Auditoria 2).
ORFAS = {
    "visitas_hoje", "faltas", "devolucoes", "entregas_resumo_motivo", "rv_pontos",
    "otp_sessions", "pdv_compras",
}


def _val(col, v):
    """Converte um valor da aba para o que o psycopg2 insere.
    Vazio → NULL; telas_json (JSONB) → Json; demais → como veio (número/texto)."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        if col == "telas_json":
            try:
                return Json(json.loads(s))
            except Exception:
                return None
        return s
    return v  # int/float/bool já no tipo certo (UNFORMATTED_VALUE)


def _migrar_aba(cur, titulo, registros):
    if not registros:
        return "vazia (pulada)"
    header = [str(h).strip() for h in registros[0].keys() if str(h).strip() != ""]
    if not header:
        return "sem cabeçalho (pulada)"
    cols = [c.lower() for c in header]
    cols_sql = ", ".join('"%s"' % c for c in cols)
    rows = [tuple(_val(c, r.get(h)) for h, c in zip(header, cols)) for r in registros]

    if titulo in CADASTRO:
        cur.execute('TRUNCATE TABLE "%s"' % titulo)
        sql = 'INSERT INTO "%s" (%s) VALUES %%s ON CONFLICT DO NOTHING' % (titulo, cols_sql)
    else:
        cur.execute('DROP TABLE IF EXISTS "%s"' % titulo)
        defs = ", ".join('"%s" TEXT' % c for c in cols)
        cur.execute('CREATE TABLE "%s" (%s)' % (titulo, defs))
        sql = 'INSERT INTO "%s" (%s) VALUES %%s' % (titulo, cols_sql)

    execute_values(cur, sql, rows, page_size=500)
    return "✅ %d linhas" % len(rows)


def migrar_tudo():
    """Lê todas as abas do Sheets e popula o Postgres. Retorna um relatório por aba."""
    sh = get_sheet()
    worksheets = _com_retry(lambda: sh.worksheets())
    rel = {}
    conn = get_conn()
    try:
        for ws in worksheets:
            titulo = ws.title.strip()
            if titulo in ORFAS:
                rel[titulo] = "órfã (ignorada)"
                continue
            try:
                registros = _com_retry(
                    lambda ws=ws: ws.get_all_records(value_render_option="UNFORMATTED_VALUE")
                )
                cur = conn.cursor()
                rel[titulo] = _migrar_aba(cur, titulo, registros)
                conn.commit()
                cur.close()
            except Exception as e:
                conn.rollback()
                rel[titulo] = "❌ %s" % str(e)[:140]
        return rel
    finally:
        conn.close()
