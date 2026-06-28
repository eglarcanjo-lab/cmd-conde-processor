# Conexão com o PostgreSQL (Supabase) — usada pela migração (ETL) e, futuramente,
# pela camada de dados SQL. A URL vem da env DATABASE_URL (connection string do
# pooler do Supabase). psycopg2 só é importado aqui, então se a dependência/URL
# faltar, só as rotas de banco falham — o resto do processador segue funcionando.
import os
import psycopg2


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não configurada no ambiente do processador.")
    return psycopg2.connect(url)
