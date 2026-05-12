import pandas as pd
import chardet
import io
from datetime import datetime, date
from categorias import resolver_categoria, carregar_base_produtos, CATEGORIAS_VALIDAS
from sheets_service import ler_aba, sobrescrever_aba, atualizar_status_arquivo

# Setores válidos da CMD Conde
SETORES_VALIDOS = {
    "101", "102", "103", "104", "105", "106",
    "301", "302", "303", "304", "305"
}

DIAS_SEMANA_MAP = {
    "SEG": 0, "TER": 1, "QUA": 2, "QUI": 3, "SEX": 4, "SAB": 5, "DOM": 6,
    "SEGUNDA": 0, "TERÇA": 1, "QUARTA": 2, "QUINTA": 3, "SEXTA": 4,
}


def detectar_encoding(conteudo_bytes):
    resultado = chardet.detect(conteudo_bytes[:10000])
    enc = resultado.get("encoding", "latin-1")
    return enc if enc else "latin-1"


def ler_csv_inf(conteudo_bytes):
    """Lê um CSV/INF separado por ; com encoding automático."""
    enc = detectar_encoding(conteudo_bytes)
    try:
        texto = conteudo_bytes.decode(enc, errors="replace")
    except Exception:
        texto = conteudo_bytes.decode("latin-1", errors="replace")
    return pd.read_csv(io.StringIO(texto), sep=";", dtype=str, low_memory=False)


def normalizar_setor(setor_raw):
    """Remove zeros à esquerda do setor. Ex: '00101' → '101'"""
    try:
        return str(int(str(setor_raw).strip()))
    except:
        return str(setor_raw).strip()


def normalizar_dia_visita(dia_raw):
    """Extrai o dia da semana do campo. Ex: 'SEG/   ' → 'SEG'"""
    if not dia_raw or str(dia_raw).strip() == "":
        return ""
    dia = str(dia_raw).strip().upper().split("/")[0].strip()
    return dia


def dia_semana_hoje():
    return date.today().weekday()  # 0=SEG, 6=DOM


def processar_clientes(conteudo_bytes):
    """
    Processa o arquivo 0105070402 (base de clientes/PDVs).
    Gera as abas:
    - pdv_base: todos os PDVs ativos dos setores válidos
    - visitas_hoje: PDVs com visita no dia atual
    - inadimplentes: PDVs com títulos pendentes
    - sem_compra: PDVs ordenados por dias sem compra
    """
    print("📂 Processando clientes...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Filtrar ativos dos setores válidos
    df["_setor"] = df["Setor VDE"].apply(normalizar_setor)
    df["_status"] = df["Status do PDV"].str.strip().str.upper()
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    df = df[df["_status"] == "ATIVO"]

    print(f"  → {len(df)} PDVs ativos nos setores válidos")

    hoje_idx = dia_semana_hoje()

    resultados = []
    for _, row in df.iterrows():
        dia_raw = row.get("Dia de Visita do VDE", "")
        dia_norm = normalizar_dia_visita(dia_raw)
        dia_idx = DIAS_SEMANA_MAP.get(dia_norm, -1)

        # Última compra → dias sem compra
        ultima_compra_raw = str(row.get("Data da Última Compra", "")).strip()
        dias_sem_compra = calcular_dias_sem_compra(ultima_compra_raw)

        # Inadimplência
        titulos = str(row.get("Títulos Pendentes", "0")).strip().replace(",", ".")
        try:
            titulos_val = float(titulos)
        except:
            titulos_val = 0.0

        resultados.append({
            "cod_pdv": str(row.get("Cód PDV", "")).strip().lstrip("0"),
            "nome_fantasia": str(row.get("Nome Fantasia", "")).strip(),
            "razao_social": str(row.get("Razão Social", "")).strip(),
            "setor": row["_setor"],
            "cidade": str(row.get("Cidade", "")).strip(),
            "segmento": str(row.get("Segmento NGE", "")).strip(),
            "dia_visita": dia_norm,
            "visita_hoje": "1" if dia_idx == hoje_idx else "0",
            "ultima_compra": ultima_compra_raw,
            "dias_sem_compra": dias_sem_compra,
            "titulos_pendentes": round(titulos_val, 2),
            "inadimplente": "1" if titulos_val > 0 else "0",
            "limite_disponivel": str(row.get("Limite de Crédito Disponível", "")).strip(),
            "compra_media": str(row.get("Compra Média", "")).strip(),
            "acumulado_vendas": str(row.get("Acumulado de Vendas", "")).strip(),
            "data_prox_visita": str(row.get("Data da Prox Visita VDE", "")).strip(),
        })

    df_base = pd.DataFrame(resultados)

    # ── Aba pdv_base ─────────────────────────────────────────────────────────
    sobrescrever_aba("pdv_base", df_base)

    # ── Aba visitas_hoje ─────────────────────────────────────────────────────
    df_hoje = df_base[df_base["visita_hoje"] == "1"].copy()
    sobrescrever_aba("visitas_hoje", df_hoje)

    # ── Aba inadimplentes ────────────────────────────────────────────────────
    df_inad = df_base[df_base["inadimplente"] == "1"].copy()
    df_inad = df_inad.sort_values("titulos_pendentes", ascending=False)
    sobrescrever_aba("inadimplentes", df_inad[["setor","cod_pdv","nome_fantasia","cidade","segmento","titulos_pendentes","ultima_compra","dias_sem_compra"]])

    # ── Aba sem_compra ───────────────────────────────────────────────────────
    df_sc = df_base.copy()
    df_sc["dias_sem_compra_num"] = pd.to_numeric(df_sc["dias_sem_compra"], errors="coerce").fillna(0)
    df_sc = df_sc.sort_values("dias_sem_compra_num", ascending=False)
    sobrescrever_aba("sem_compra", df_sc[["setor","cod_pdv","nome_fantasia","cidade","segmento","dias_sem_compra","ultima_compra","inadimplente"]])

    atualizar_status_arquivo("0105070402 (Clientes)", "✅ OK", f"{len(df_base)} PDVs processados")
    print(f"  ✅ Clientes processados: {len(df_base)} PDVs, {len(df_hoje)} visitas hoje")
    return df_base


def calcular_dias_sem_compra(data_str):
    """Calcula dias desde a última compra."""
    formatos = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]
    for fmt in formatos:
        try:
            dt = datetime.strptime(data_str.strip(), fmt).date()
            return (date.today() - dt).days
        except:
            continue
    return ""


def processar_pedidos(conteudo_bytes, df_clientes_base=None):
    """
    Processa o arquivo 03014701 (pedidos faturados).
    Gera as abas:
    - cobertura: status OK/Pendente/NOK por PDV x Categoria
    - cobertura_resumo: % cobertura por setor x categoria
    - pdv_mix: top produtos por PDV (últimos 4 meses)
    - rank_clientes: ranking por volume
    - faltas: produtos com falta registrada
    - devolucoes: devoluções por PDV
    """
    print("📂 Processando pedidos...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Normalizar setor
    df["_setor"] = df["Setor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normalizar data
    df["_data"] = pd.to_datetime(df["Data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    df = df[df["_data"].notna()]

    # Carregar base de produtos do Sheets para categorias
    df_prods_base = ler_aba("produtos_base")
    mapa_produtos = carregar_base_produtos(df_prods_base) if not df_prods_base.empty else {}

    # Resolver categoria de cada linha
    def get_cat(row):
        cod = str(row.get("Cod. Prod.", "")).strip()
        cat_sheet = mapa_produtos.get(cod)
        if cat_sheet:
            return cat_sheet
        return resolver_categoria(
            cod,
            row.get("Cat_Funda", ""),
            row.get("Nome Prod.", ""),
            row.get("Código Marca", ""),
        )

    df["_categoria"] = df.apply(get_cat, axis=1)

    # Normalizar volume
    df["_volume"] = df["Volume Entrega"].str.replace(",", ".").apply(
        lambda x: float(x) if x.strip() != "" else 0.0
    )

    # Normalizar cod cliente
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0")

    hoje = date.today()
    mes_atual = hoje.replace(day=1)
    mes_anterior = (mes_atual - pd.DateOffset(months=1)).date()
    quatro_meses_atras = (mes_atual - pd.DateOffset(months=4)).date()

    df_mes_atual = df[df["_data"].dt.date >= mes_atual]
    df_mes_ant = df[(df["_data"].dt.date >= mes_anterior) & (df["_data"].dt.date < mes_atual)]
    df_4m = df[df["_data"].dt.date >= quatro_meses_atras]

    # ── Cobertura ─────────────────────────────────────────────────────────────
    _processar_cobertura(df_mes_atual, df_mes_ant, df_clientes_base)

    # ── PDV Mix (últimos 4 meses) ─────────────────────────────────────────────
    _processar_mix(df_4m)

    # ── Rank clientes ─────────────────────────────────────────────────────────
    _processar_rank(df_4m)

    # ── Faltas ────────────────────────────────────────────────────────────────
    _processar_faltas(df)

    # ── Devoluções ────────────────────────────────────────────────────────────
    _processar_devolucoes(df)

    # ── Produtos sem categoria ────────────────────────────────────────────────
    _registrar_sem_categoria(df, mapa_produtos)

    atualizar_status_arquivo("03014701 (Pedidos)", "✅ OK", f"{len(df)} linhas processadas")
    print(f"  ✅ Pedidos processados: {len(df)} linhas")


def _processar_cobertura(df_atual, df_ant, df_clientes):
    """Gera cobertura OK/Pendente/NOK por PDV x Categoria."""
    cats = [c for c in CATEGORIAS_VALIDAS if c not in ("TRIMARCA RGB HE (Original)", "TRIMARCA RGB HE (Stella)", "TRIMARCA RGB HE (Spaten)")]

    # PDVs que compraram no mês atual (por categoria)
    ok_set = set(
        df_atual[df_atual["_categoria"].notna() & (df_atual["_volume"] > 0)]
        .apply(lambda r: (r["_cod_pdv"], r["_categoria"]), axis=1)
    )

    # PDVs que compraram no mês anterior (por categoria)
    pend_set = set(
        df_ant[df_ant["_categoria"].notna() & (df_ant["_volume"] > 0)]
        .apply(lambda r: (r["_cod_pdv"], r["_categoria"]), axis=1)
    )

    # Base de PDVs
    if df_clientes is not None and not df_clientes.empty:
        pdvs = df_clientes[["cod_pdv", "nome_fantasia", "setor"]].drop_duplicates()
    else:
        todos_pdvs = pd.concat([df_atual, df_ant])[["_cod_pdv", "_setor"]].drop_duplicates()
        pdvs = todos_pdvs.rename(columns={"_cod_pdv": "cod_pdv", "_setor": "setor"})
        pdvs["nome_fantasia"] = ""

    linhas = []
    for _, pdv in pdvs.iterrows():
        cod = pdv["cod_pdv"]
        for cat in cats:
            if (cod, cat) in ok_set:
                status = "OK"
            elif (cod, cat) in pend_set:
                status = "PENDENTE"
            else:
                status = "NOK"

            linhas.append({
                "setor": pdv["setor"],
                "cod_pdv": cod,
                "nome_fantasia": pdv.get("nome_fantasia", ""),
                "categoria": cat,
                "status": status,
                "mes_referencia": date.today().strftime("%Y-%m"),
            })

    df_cob = pd.DataFrame(linhas)
    sobrescrever_aba("cobertura", df_cob)

    # Resumo por setor x categoria
    resumo = df_cob.groupby(["setor", "categoria", "status"]).size().reset_index(name="qtd")
    total = df_cob.groupby(["setor", "categoria"]).size().reset_index(name="total")
    resumo = resumo.merge(total, on=["setor", "categoria"])
    resumo["pct"] = (resumo["qtd"] / resumo["total"] * 100).round(1)
    sobrescrever_aba("cobertura_resumo", resumo)


def _processar_mix(df_4m):
    """Top 5 produtos mais pedidos por PDV nos últimos 4 meses."""
    df_v = df_4m[df_4m["_volume"] > 0].copy()
    mix = (
        df_v.groupby(["_setor", "_cod_pdv", "Cod. Prod.", "Nome Prod.", "_categoria"])
        ["_volume"].sum()
        .reset_index()
        .rename(columns={"_volume": "volume_total_hl", "Cod. Prod.": "cod_prod", "Nome Prod.": "nome_prod", "_categoria": "categoria", "_cod_pdv": "cod_pdv", "_setor": "setor"})
        .sort_values(["cod_pdv", "volume_total_hl"], ascending=[True, False])
    )
    # Top 10 por PDV
    mix = mix.groupby("cod_pdv").head(10).reset_index(drop=True)
    sobrescrever_aba("pdv_mix", mix)


def _processar_rank(df_4m):
    """Ranking de clientes por volume nos últimos 4 meses."""
    df_v = df_4m[df_4m["_volume"] > 0].copy()
    rank = (
        df_v.groupby(["_setor", "_cod_pdv"])
        ["_volume"].sum()
        .reset_index()
        .rename(columns={"_volume": "volume_4m_hl", "_cod_pdv": "cod_pdv", "_setor": "setor"})
        .sort_values("volume_4m_hl", ascending=False)
    )
    rank["posicao"] = rank.groupby("setor")["volume_4m_hl"].rank(ascending=False, method="first").astype(int)
    sobrescrever_aba("rank_clientes", rank)


def _processar_faltas(df):
    """PDVs com falta registrada (Motivo contém FALTA)."""
    df_falta = df[df["Motivo"].str.upper().str.contains("FALTA", na=False)].copy()
    faltas = (
        df_falta.groupby(["_setor", "Cod. Prod.", "Nome Prod.", "_categoria"])
        .size()
        .reset_index(name="qtd_faltas")
        .rename(columns={"Cod. Prod.": "cod_prod", "Nome Prod.": "nome_prod", "_categoria": "categoria", "_setor": "setor"})
        .sort_values("qtd_faltas", ascending=False)
    )
    sobrescrever_aba("faltas", faltas)


def _processar_devolucoes(df):
    """PDVs com devoluções (Desc Tipo Movimento contém DEVOL)."""
    df_dev = df[df["Desc Tipo Movimento"].str.upper().str.contains("DEVOL", na=False)].copy()
    devs = (
        df_dev.groupby(["_setor", "_cod_pdv"])
        ["_volume"].sum()
        .reset_index()
        .rename(columns={"_volume": "volume_devolvido_hl", "_cod_pdv": "cod_pdv", "_setor": "setor"})
        .sort_values("volume_devolvido_hl", ascending=False)
    )
    sobrescrever_aba("devolucoes", devs)


def _registrar_sem_categoria(df, mapa_produtos):
    """Registra produtos que apareceram nos pedidos mas não têm categoria."""
    df["_cod_str"] = df["Cod. Prod."].str.strip()

    # Atualiza produtos_base com todos os produtos que aparecem nos pedidos
    todos_prods = df[["_cod_str", "Nome Prod."]].drop_duplicates()
    todos_prods = todos_prods.rename(columns={"_cod_str": "cod", "Nome Prod.": "nome"})
    todos_prods["categoria"] = todos_prods["cod"].map(mapa_produtos).fillna("")
    todos_prods["atualizado_em"] = date.today().strftime("%d/%m/%Y")
    sobrescrever_aba("produtos_base", todos_prods)

    # Registra os sem categoria
    sem_cat = df[df["_categoria"].isna()][["_cod_str", "Nome Prod."]].drop_duplicates()
    sem_cat = sem_cat[~sem_cat["_cod_str"].isin(mapa_produtos.keys())]
    sem_cat = sem_cat.rename(columns={"_cod_str": "cod_prod", "Nome Prod.": "nome_prod"})
    sem_cat["categoria"] = ""
    sem_cat["alerta"] = "⚠️ Sem categoria"
    sobrescrever_aba("produtos_sem_categoria", sem_cat)
    print(f"  ⚠️ {len(sem_cat)} produtos sem categoria registrados")
    print(f"  📦 {len(todos_prods)} produtos na base")


def processar_inadimplencia(conteudo_bytes):
    """
    Processa o arquivo 121601 (inadimplência).
    Gera a aba inadimplencia_real com títulos vencidos por PDV.
    """
    print("📂 Processando inadimplência...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Normaliza setor pelo campo Vendedor
    df["_setor"] = df["Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normaliza cod PDV
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0")
    df["_nome"] = df["Nome"].str.strip()

    # Dias: valores negativos = vencido, positivos = a vencer
    df["_dias"] = pd.to_numeric(df["Dias"].str.strip(), errors="coerce").fillna(0)

    # Valor pendente: remove + e vírgula brasileira
    df["_valor"] = df["ValorPendente"].str.replace("+", "", regex=False).str.replace(",", ".").str.strip()
    df["_valor"] = pd.to_numeric(df["_valor"], errors="coerce").fillna(0)

    # Agrupa por PDV
    resumo = (
        df.groupby(["_setor", "_cod_pdv", "_nome"])
        .agg(
            qtd_titulos=("_valor", "count"),
            valor_total=("_valor", "sum"),
            maior_atraso=("_dias", "min"),  # mais negativo = mais atrasado
            aging=("AGING", lambda x: x.mode()[0] if len(x) > 0 else ""),
        )
        .reset_index()
        .rename(columns={
            "_setor": "setor",
            "_cod_pdv": "cod_pdv",
            "_nome": "nome_fantasia",
        })
    )

    resumo["maior_atraso"] = resumo["maior_atraso"].abs().astype(int)
    resumo["valor_total"] = resumo["valor_total"].round(2)
    resumo = resumo.sort_values("maior_atraso", ascending=False)

    sobrescrever_aba("inadimplencia_real", resumo)
    atualizar_status_arquivo("120601 (Inadimplência)", "✅ OK", f"{len(resumo)} PDVs inadimplentes")
    print(f"  ✅ Inadimplência processada: {len(resumo)} PDVs")
    return resumo


def processar_tasks(conteudo_bytes):
    """
    Processa o arquivo de tasks do BI (xlsx).
    Gera a aba tasks com status VALID/INVALID/OPEN por PDV.
    """
    import io
    print("📂 Processando tasks...")
    
    # Linha 0: filtros, Linha 1: vazia, Linha 2: cabeçalho real
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, skiprows=2)
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str, skiprows=2)
    
    df.columns = [c.strip() for c in df.columns]
    
    # Normaliza setor
    df["_setor"] = df["Setor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    
    # Normaliza cod PDV
    df["_cod_pdv"] = df["Pdv"].str.strip().str.lstrip("0")
    
    # Converte data de visita (serial Excel → data)
    def serial_to_date(val):
        try:
            n = int(float(str(val).strip()))
            from datetime import date
            return (date(1899, 12, 30) + pd.Timedelta(days=n)).strftime("%d/%m/%Y")
        except:
            return str(val).strip()
    
    df["_data_visita"] = df["Data Visita"].apply(serial_to_date)
    df["_data_criacao"] = df["Data Criação Tarefa"].apply(serial_to_date)
    df["_data_conclusao"] = df["Data Conclusão Tarefa"].apply(serial_to_date)
    
    # Status normalizado
    df["_status"] = df["Effectiveness Result"].str.strip().str.upper()
    
    resultado = []
    for _, row in df.iterrows():
        resultado.append({
            "setor": row["_setor"],
            "cod_pdv": row["_cod_pdv"],
            "data_visita": row["_data_visita"],
            "data_criacao": row["_data_criacao"],
            "data_conclusao": row["_data_conclusao"],
            "status": row["_status"],
            "tipo": str(row.get("Cluster Secundário", "")).strip(),
            "categoria": str(row.get("Categoria", "")).strip(),
            "descricao": str(row.get("Texto da Tarefa", "")).strip(),
            "pontuacao": str(row.get("Pontuação", "")).strip(),
            "id_task": str(row.get("Id task pool", "")).strip(),
            "mes_ano": str(row.get("Mês/ Ano", "")).strip(),
        })
    
    df_tasks = pd.DataFrame(resultado)
    sobrescrever_aba("tasks", df_tasks)
    atualizar_status_arquivo("Tasks (BI)", "✅ OK", f"{len(df_tasks)} tasks processadas")
    print(f"  ✅ Tasks processadas: {len(df_tasks)}")
    return df_tasks
