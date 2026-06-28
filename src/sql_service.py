# ─────────────────────────────────────────────────────────────────────────────
# sql_service — versão PostgreSQL das funções de dados do processador, com a MESMA
# assinatura de sheets_service (ler_aba / sobrescrever_aba / atualizar_status_arquivo).
# Ativado por DATA_BACKEND=sql (ver fim de sheets_service.py). Padrão = Sheets.
#
#   • ler_aba: SELECT * → DataFrame. Colunas 100% numéricas viram número (mimetiza o
#     UNFORMATTED do Sheets); colunas de código (setor, cod_*, cpf…) ficam como texto.
#   • sobrescrever_aba: cadastro → TRUNCATE+INSERT (tabelas tipadas); computada →
#     DROP+CREATE(TEXT)+INSERT (igual ao ETL). Valores "" / NaN → NULL (o Postgres
#     converte o resto pro tipo da coluna).
#   • atualizar_status_arquivo: upsert por 'arquivo' em status_arquivos.
# ─────────────────────────────────────────────────────────────────────────────
import pandas as pd
from psycopg2.extras import execute_values
from db import get_conn

CADASTRO = {
    "usuarios", "reset_solicitacoes", "incidentes", "avisos", "popups", "incentivos",
    "incentivos_resultados", "sku_foco", "metas", "spo_metas", "spo_desafios",
    "configuracoes", "uso_app", "uso_telas", "inadimplencia_real",
}

# Colunas que NÃO devem virar número (códigos/chaves — preservam zeros à esquerda)
NAO_NUMERICAS = {
    "setor", "cod_pdv", "cod_produto", "cod", "cod_prod", "cpf", "gv", "telefone",
    "nota", "grade", "mes_referencia", "mes", "mes_ano", "dia", "item", "chave",
    "cod_motivo", "cluster_primario", "id", "id_task", "id_incentivo",
}


def ler_aba(nome_aba):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM "%s"' % nome_aba)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        if c.lower() in NAO_NUMERICAS:
            df[c] = df[c].apply(lambda x: "" if x is None else str(x))
            continue
        conv = pd.to_numeric(df[c], errors="coerce")
        nao_vazio = df[c].notna()
        if nao_vazio.any() and conv[nao_vazio].notna().all():
            df[c] = conv  # coluna numérica de verdade
    return df


def _to_param(x):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    s = str(x).strip()
    return None if s == "" else s


def sobrescrever_aba(nome_aba, df):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cols = [str(c).strip() for c in df.columns if str(c).strip() != ""]
        cols_l = [c.lower() for c in cols]
        cols_sql = ", ".join('"%s"' % c for c in cols_l)
        rows = [tuple(_to_param(r[c]) for c in cols) for _, r in df.iterrows()] if cols else []

        if nome_aba in CADASTRO:
            cur.execute('TRUNCATE TABLE "%s"' % nome_aba)
            if rows:
                execute_values(cur, 'INSERT INTO "%s" (%s) VALUES %%s ON CONFLICT DO NOTHING'
                               % (nome_aba, cols_sql), rows, page_size=500)
        else:
            cur.execute('DROP TABLE IF EXISTS "%s"' % nome_aba)
            defs = ", ".join('"%s" TEXT' % c for c in cols_l) or '"_vazio" TEXT'
            cur.execute('CREATE TABLE "%s" (%s)' % (nome_aba, defs))
            if rows:
                execute_values(cur, 'INSERT INTO "%s" (%s) VALUES %%s'
                               % (nome_aba, cols_sql), rows, page_size=500)
        conn.commit()
        print("  ✅ [SQL] '%s': %d linhas" % (nome_aba, len(rows)))
    finally:
        conn.close()


def atualizar_status_arquivo(nome_arquivo, status, detalhes=""):
    from datetime import datetime
    try:
        import pytz
        agora = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
    except Exception:
        agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS status_arquivos '
                    '(arquivo TEXT, status TEXT, detalhes TEXT, atualizado_em TEXT, periodicidade TEXT)')
        cur.execute('DELETE FROM status_arquivos WHERE arquivo = %s', (nome_arquivo,))
        cur.execute('INSERT INTO status_arquivos (arquivo, status, detalhes, atualizado_em, periodicidade) '
                    'VALUES (%s, %s, %s, %s, %s)', (nome_arquivo, status, detalhes, agora, "Diária"))
        conn.commit()
    finally:
        conn.close()
