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
    """Retorna o dia da semana atual no horário de Brasília (0=SEG, 6=DOM)."""
    from datetime import datetime
    import pytz
    brasilia = pytz.timezone("America/Sao_Paulo")
    agora = datetime.now(brasilia)
    return agora.weekday()


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
    def get_cats(row):
        """Retorna lista de categorias — SOMENTE da produtos_base cadastrada pelo admin."""
        cod = str(row.get("Cod. Prod.", "")).strip()
        cats_sheet = mapa_produtos.get(cod)
        if cats_sheet and isinstance(cats_sheet, list):
            return cats_sheet
        return []  # Sem categoria cadastrada = não contabiliza

    df["_categorias"] = df.apply(get_cats, axis=1)
    # Para compatibilidade: _categoria = primeira categoria da lista
    df["_categoria"] = df["_categorias"].apply(lambda x: x[0] if x else None)

    # Normalizar volume
    df["_volume"] = df["Volume Entrega"].str.replace(",", ".").apply(
        lambda x: float(x) if x.strip() != "" else 0.0
    )

    # Normalizar cod cliente
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0").str.strip()

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

    # PDVs que compraram no mês atual (por categoria) — expande múltiplas categorias
    ok_set = set()
    for _, row in df_atual[df_atual["_volume"] > 0].iterrows():
        for cat in (row.get("_categorias") or []):
            ok_set.add((row["_cod_pdv"], cat))

    # PDVs que compraram no mês anterior (por categoria)
    pend_set = set()
    for _, row in df_ant[df_ant["_volume"] > 0].iterrows():
        for cat in (row.get("_categorias") or []):
            pend_set.add((row["_cod_pdv"], cat))

    # Base de PDVs vem da base de clientes
    if df_clientes is not None and not df_clientes.empty:
        pdvs = df_clientes[["cod_pdv", "nome_fantasia", "setor"]].drop_duplicates()
    else:
        todos_pdvs = pd.concat([df_atual, df_ant])[["_cod_pdv", "_setor", "Nome Cliente"]].drop_duplicates(subset=["_cod_pdv"])
        pdvs = todos_pdvs.rename(columns={"_cod_pdv": "cod_pdv", "_setor": "setor", "Nome Cliente": "nome_fantasia"})

    linhas = []
    for _, pdv in pdvs.iterrows():
        # Normaliza cod_pdv: remove zeros e espaços, converte para string limpa
        cod = str(pdv["cod_pdv"]).strip().lstrip("0") or "0"
        setor = str(pdv.get("setor", pdv.get("_setor", ""))).strip()
        nome = str(pdv.get("nome_fantasia", "")).strip()
        for cat in cats:
            if (cod, cat) in ok_set:
                status = "OK"
            elif (cod, cat) in pend_set:
                status = "PENDENTE"
            else:
                status = "NOK"

            linhas.append({
                "setor": setor,
                "cod_pdv": cod,
                "nome_fantasia": nome,
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

    # PRESERVA categorias da produtos_base — apenas adiciona produtos novos
    try:
        df_base_atual = ler_aba("produtos_base")
        mapa_base = {}  # {cod: {nome, categorias}}
        if not df_base_atual.empty and "cod" in df_base_atual.columns:
            for _, row in df_base_atual.iterrows():
                cod = str(row.get("cod","")).strip()
                if cod:
                    mapa_base[cod] = {
                        "nome": str(row.get("nome","")).strip(),
                        "categorias": str(row.get("categorias","")).strip(),
                    }
    except Exception:
        mapa_base = {}

    # Produtos que aparecem nos pedidos
    todos_prods = df[["_cod_str", "Nome Prod."]].drop_duplicates()
    todos_prods = todos_prods.rename(columns={"_cod_str": "cod", "Nome Prod.": "nome_pedido"})

    linhas_base = []
    for _, row in todos_prods.iterrows():
        cod = str(row["cod"]).strip()
        nome_pedido = str(row["nome_pedido"]).strip()
        existente = mapa_base.get(cod, {})

        # Preserva nome da base 0111 se já existe, senão usa nome do pedido
        nome = existente.get("nome") or nome_pedido

        # PRESERVA categorias já cadastradas — não sobrescreve
        cats_existentes = existente.get("categorias", "")
        if cats_existentes:
            categorias = cats_existentes
        else:
            # Só resolve categoria se ainda não tem nenhuma cadastrada
            cats_list = mapa_produtos.get(cod, [])
            if isinstance(cats_list, list):
                categorias = "|".join(cats_list)
            else:
                categorias = cats_list or ""

        linhas_base.append({
            "cod": cod,
            "nome": nome,
            "categorias": categorias,
            "atualizado_em": date.today().strftime("%d/%m/%Y"),
        })

    df_base_nova = pd.DataFrame(linhas_base)
    sobrescrever_aba("produtos_base", df_base_nova)
    print(f"  📦 produtos_base atualizada: {len(df_base_nova)} produtos (categorias preservadas)")

    # Registra os sem categoria — usa nome completo da base 0111
    try:
        df_base_nomes = ler_aba("produtos_base")
        mapa_nomes_completos = {}
        if not df_base_nomes.empty and "cod" in df_base_nomes.columns:
            for _, row in df_base_nomes.iterrows():
                cod = str(row.get("cod","")).strip()
                nome = str(row.get("nome","")).strip()
                if cod and nome:
                    mapa_nomes_completos[cod] = nome
    except Exception:
        mapa_nomes_completos = {}

    sem_cat = df[df["_categoria"].isna()][["_cod_str", "Nome Prod."]].drop_duplicates()
    sem_cat = sem_cat[~sem_cat["_cod_str"].isin(mapa_produtos.keys())]
    sem_cat = sem_cat.rename(columns={"_cod_str": "cod_prod", "Nome Prod.": "nome_pedido"})
    # Usa nome completo da base 0111, fallback para nome do pedido
    sem_cat["nome_prod"] = sem_cat["cod_prod"].map(mapa_nomes_completos).fillna(sem_cat["nome_pedido"])
    sem_cat = sem_cat[["cod_prod", "nome_prod"]].drop_duplicates(subset=["cod_prod"])
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
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0").str.strip()
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
            "mes_ano":          str(row.get("Mês/ Ano", "")).strip(),
            "setor":            row["_setor"],
            "cod_pdv":          row["_cod_pdv"],
            "data_visita":      row["_data_visita"],
            "data_criacao":     row["_data_criacao"],
            "data_conclusao":   row["_data_conclusao"],
            "status":           row["_status"],
            "id_task":          str(row.get("Id task pool", "")).strip(),
            "cluster_primario": str(row.get("Cluster Primário", "")).strip(),
            "tipo":             str(row.get("Cluster Secundário", "")).strip(),
            "categoria":        str(row.get("Categoria", "")).strip(),
            "descricao":        str(row.get("Texto da Tarefa", "")).strip(),
            "qtd_solicitada":   str(row.get("QTD Solicitada", "")).strip(),
            "qtd_comprada":     str(row.get("QTD Já Comprada", "")).strip(),
            "completa":         str(row.get("Completa", "")).strip(),
            "validada":         str(row.get("Validada", "")).strip(),
            "pre_validada":     str(row.get("Pré Validada", "")).strip(),
            "pontuacao":        str(row.get("Pontuação", "")).strip(),
            "justificativa":    str(row.get("Justificativa", "")).strip(),
            "mensal_diaria":    str(row.get("Mensal/ Diária", "")).strip(),
            "geo":              str(row.get("Geo", "")).strip(),
            "gv":               str(row.get("GV", "")).strip(),
        })
    
    df_tasks = pd.DataFrame(resultado)
    sobrescrever_aba("tasks", df_tasks)
    atualizar_status_arquivo("Tasks (BI)", "✅ OK", f"{len(df_tasks)} tasks processadas")
    print(f"  ✅ Tasks processadas: {len(df_tasks)}")
    return df_tasks

def processar_produtos_base(conteudo_bytes):
    """
    Processa o arquivo 0111 (base de produtos do sistema).
    Atualiza APENAS os nomes dos produtos que já estão na base de vendas.
    Não importa produtos sem vendas — mantém a base leve.
    Importação semanal.
    """
    print("📂 Processando base de produtos (0111)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Monta dicionário cod → dados completos do 0111
    df["_cod"] = df["Código"].str.strip().str.lstrip("0")
    df["_nome"] = df["Descrição"].str.strip()
    mapa_0111 = {}
    for _, row in df.iterrows():
        cod = row["_cod"]
        if cod:
            mapa_0111[cod] = {
                "nome": row["_nome"],
                "tipo_marca": str(row.get("Tipo Marca", "")).strip(),
                "linha_marca": str(row.get("Linha Marca", "")).strip(),
                "embalagem": str(row.get("Embalagem", "")).strip(),
                "marca": str(row.get("Marca", "")).strip(),
                "pgv": str(row.get("PGV", "")).strip(),
            }

    # Carrega base existente (apenas produtos com vendas)
    try:
        df_base = ler_aba("produtos_base")
    except Exception:
        df_base = pd.DataFrame()

    if df_base.empty or "cod" not in df_base.columns:
        atualizar_status_arquivo("0111 (Produtos)", "⚠️ Aguardando", "Importe os pedidos primeiro para popular a base")
        print("  ⚠️ Base de vendas vazia — importe os pedidos primeiro")
        return pd.DataFrame()

    # Atualiza nomes e dados do 0111 apenas para produtos que já estão na base
    atualizados = 0
    rows = []
    for _, row in df_base.iterrows():
        cod = str(row.get("cod", "")).strip()
        dados_0111 = mapa_0111.get(cod, {})
        nova_row = dict(row)
        if dados_0111:
            nova_row["nome"] = dados_0111["nome"]
            nova_row["tipo_marca"] = dados_0111["tipo_marca"]
            nova_row["linha_marca"] = dados_0111["linha_marca"]
            nova_row["embalagem"] = dados_0111["embalagem"]
            nova_row["marca"] = dados_0111["marca"]
            nova_row["pgv"] = dados_0111["pgv"]
            atualizados += 1
        rows.append(nova_row)

    df_final = pd.DataFrame(rows)
    sobrescrever_aba("produtos_base", df_final)
    atualizar_status_arquivo("0111 (Produtos)", "✅ OK", f"{atualizados} produtos com nome atualizado de {len(df_final)} na base")
    print(f"  ✅ 0111 processado: {atualizados}/{len(df_final)} produtos atualizados")
    return df_final



def processar_faturamento_mktp(conteudo_bytes):
    """
    Processa o arquivo 030509 (faturamento consolidado por cliente/produto).
    Filtra apenas produtos da categoria MKTP e agrega por setor.
    """
    print("📂 Processando faturamento Mktp (030509)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Colunas usadas: Cliente, Cod.Produto, Total Venda, Vendedor
    df["_setor"] = df["Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    df["_cod_prod"] = df["Cod.Produto"].str.strip()
    df["_total_venda"] = df["Total Venda"].str.replace(",", ".").str.replace("+", "", regex=False).str.strip()
    df["_total_venda"] = pd.to_numeric(df["_total_venda"], errors="coerce").fillna(0)

    # Filtra apenas produtos MKTP usando mapa de produtos
    df_prods = ler_aba("produtos_base")
    mapa_cats = carregar_base_produtos(df_prods) if not df_prods.empty else {}

    def is_mktp(cod):
        cats = mapa_cats.get(str(cod).strip(), [])
        if isinstance(cats, list):
            return "MKTP" in cats
        return cats == "MKTP"

    df["_is_mktp"] = df["_cod_prod"].apply(is_mktp)
    df_mktp = df[df["_is_mktp"]].copy()

    # Agrega por setor
    hoje = date.today()
    mes_ref = hoje.strftime("%Y-%m")

    resultado = (
        df_mktp.groupby("_setor")["_total_venda"]
        .sum()
        .reset_index()
        .rename(columns={"_setor": "setor", "_total_venda": "faturamento"})
    )
    resultado["mes_ref"] = mes_ref
    resultado["faturamento"] = resultado["faturamento"].round(2)

    sobrescrever_aba("rv_mktp", resultado[["setor", "mes_ref", "faturamento"]])
    atualizar_status_arquivo("030509 (Faturamento Mktp)", "✅ OK", f"{len(resultado)} setores processados")
    print(f"  ✅ Faturamento Mktp: {len(resultado)} setores")
    return resultado


def processar_pontos_bees(conteudo_bytes):
    """
    Processa o arquivo de pontos Bees do BI.
    Formato: Código Setor | Segmento RN | Coins Total
    """
    import io as _io
    print("📂 Processando pontos Bees...")
    
    try:
        df = pd.read_excel(_io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str)
    except Exception:
        df = ler_csv_inf(conteudo_bytes)

    df.columns = [c.strip() for c in df.columns]

    hoje = date.today()
    mes_ref = hoje.strftime("%Y-%m")

    # Normaliza colunas
    cod_col = next((c for c in df.columns if "código" in c.lower() or "setor" in c.lower() or "cod" in c.lower()), df.columns[0])
    coins_col = next((c for c in df.columns if "coins" in c.lower() or "pontos" in c.lower() or "total" in c.lower()), df.columns[-1])

    df["_setor"] = df[cod_col].apply(normalizar_setor)
    df["_pontos"] = pd.to_numeric(df[coins_col].str.replace(",", "."), errors="coerce").fillna(0)

    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    resultado = df[["_setor", "_pontos"]].rename(columns={"_setor": "setor", "_pontos": "pontos_real"})
    resultado["mes_ref"] = mes_ref

    sobrescrever_aba("rv_pontos", resultado[["setor", "mes_ref", "pontos_real"]])
    atualizar_status_arquivo("Pontos Bees (BI)", "✅ OK", f"{len(resultado)} setores processados")
    print(f"  ✅ Pontos Bees: {len(resultado)} setores")
    return resultado


def processar_volume_rv(df_pedidos, mes_ref):
    """
    Extrai volume por setor e categoria para cálculo de RV.
    Gera aba rv_volume com: setor | categoria | volume | mes_ref
    """
    print("📊 Gerando volume RV...")
    
    cats_rv = ["CERVEJA", "NAB", "MATCH", "GIRO RGB", "HE"]
    
    linhas = []
    for _, row in df_pedidos[df_pedidos["_volume"] > 0].iterrows():
        cats = row.get("_categorias") or []
        for cat in cats:
            if cat in cats_rv:
                linhas.append({
                    "setor": row["_setor"],
                    "categoria": cat,
                    "volume": row["_volume"],
                    "mes_ref": mes_ref,
                })

    if not linhas:
        return

    df_rv = pd.DataFrame(linhas)
    resultado = (
        df_rv.groupby(["setor", "categoria", "mes_ref"])["volume"]
        .sum()
        .reset_index()
    )
    resultado["volume"] = resultado["volume"].round(2)
    sobrescrever_aba("rv_volume", resultado)
    print(f"  ✅ Volume RV: {len(resultado)} linhas")

# ─── RV — FATURAMENTO MKTP (030509) ──────────────────────────────────────────

def processar_faturamento_mktp(conteudo_bytes):
    """
    Processa o arquivo 030509 (faturamento consolidado por cliente/produto).
    Usado para calcular GMV Marketplace do mês.
    Colunas: Cliente/Tipo Marca/Cod.Produto/Desc.Produto/Unidade/Quantidade/
             Qt Unit/Total Venda/Menor Preco/Preco Medio/PM/Ad.Esc./Pm Prazo/
             NF Pr.Min.Venda/Vendedor/Preco Medio/Objetivo/Empresa/Filial
    """
    print("📂 Processando faturamento Mktp (030509)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Normaliza setor pelo campo Vendedor
    df["_setor"] = df["Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normaliza cod produto
    df["_cod_prod"] = df["Cod.Produto"].str.strip()

    # Total Venda: remove pontos de milhar e troca vírgula por ponto
    df["_total_venda"] = (
        df["Total Venda"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    df["_total_venda"] = pd.to_numeric(df["_total_venda"], errors="coerce").fillna(0)

    # Carrega mapa de categorias para filtrar só MKTP
    df_prods = ler_aba("produtos_base")
    mapa_cats = {}
    if not df_prods.empty and "cod" in df_prods.columns:
        for _, row in df_prods.iterrows():
            cod = str(row.get("cod","")).strip()
            cats = str(row.get("categorias","")).strip()
            if cod and "MKTP" in cats.upper():
                mapa_cats[cod] = True

    # Filtra só produtos MKTP
    df["_is_mktp"] = df["_cod_prod"].map(mapa_cats).fillna(False)
    df_mktp = df[df["_is_mktp"]]

    # Agrega por setor
    resumo = (
        df_mktp.groupby("_setor")["_total_venda"]
        .sum()
        .reset_index()
        .rename(columns={"_setor": "setor", "_total_venda": "gmv_mktp_real"})
    )
    resumo["gmv_mktp_real"] = resumo["gmv_mktp_real"].round(2)
    resumo["mes_referencia"] = date.today().strftime("%Y-%m")

    sobrescrever_aba("rv_mktp", resumo)
    atualizar_status_arquivo("030509 (Faturamento Mktp)", "✅ OK", f"{len(resumo)} setores processados")
    print(f"  ✅ Faturamento Mktp: {len(resumo)} setores")
    return resumo


# ─── RV — PONTOS BEES (Excel BI) ─────────────────────────────────────────────

def processar_pontos_bees(conteudo_bytes):
    """
    Processa o Excel do BI de pontos Bees.
    Cabeçalho: Código Setor | Segmento RN | Coins Total
    """
    import io
    print("📂 Processando pontos Bees...")
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str)
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # Tenta encontrar as colunas certas mesmo com variações de nome
    col_setor = next((c for c in df.columns if "setor" in c.lower() or "código" in c.lower() or "codigo" in c.lower()), df.columns[0])
    col_coins = next((c for c in df.columns if "coins" in c.lower() or "pontos" in c.lower() or "total" in c.lower()), df.columns[2])

    df["_setor"] = df[col_setor].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    df["_coins"] = pd.to_numeric(
        df[col_coins].str.replace(",", ".").str.replace(".", "", 1),
        errors="coerce"
    ).fillna(0)

    # Tenta converter corretamente número com separador BR
    def parse_br(val):
        try:
            s = str(val).strip().replace(" ", "")
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            return float(s)
        except:
            return 0.0

    df["_coins"] = df[col_coins].apply(parse_br)

    resultado = df[["_setor", "_coins"]].rename(columns={
        "_setor": "setor",
        "_coins": "pontos_real"
    })
    resultado["mes_referencia"] = date.today().strftime("%Y-%m")
    resultado["pontos_meta"] = 100000  # fixo, alterar no código se mudar

    sobrescrever_aba("rv_pontos", resultado)
    atualizar_status_arquivo("Pontos Bees (BI)", "✅ OK", f"{len(resultado)} setores processados")
    print(f"  ✅ Pontos Bees: {len(resultado)} setores")
    return resultado


# ─── RV — VOLUME POR CATEGORIA ───────────────────────────────────────────────

def calcular_rv_volume(mes_referencia=None):
    """
    Calcula volume real por setor/categoria a partir dos pedidos já processados.
    Gera a aba rv_volume com: setor | categoria | volume_real_hl | mes_referencia
    """
    print("📂 Calculando RV volume...")
    df_cob = ler_aba("cobertura")
    if df_cob.empty:
        print("  ⚠️ Aba cobertura vazia")
        return pd.DataFrame()

    # Usa pedidos faturados já processados — pega da aba pdv_mix que tem volume
    df_mix = ler_aba("pdv_mix")
    if df_mix.empty:
        print("  ⚠️ Aba pdv_mix vazia")
        return pd.DataFrame()

    cats_rv = ["CERVEJA", "CERVEJA ZERO", "GIRO RGB", "HE", "HE RGB",
               "NAB", "NAB ZERO", "MATCH", "MKTP", "BALANCED CHOICE",
               "TRIMARCA RGB HE (Original)", "TRIMARCA RGB HE (Stella)", "TRIMARCA RGB HE (Spaten)"]

    # Agrupa volume por setor e categoria macro
    MAPA_MACRO = {
        "CERVEJA": "CERVEJA", "CERVEJA ZERO": "CERVEJA", "CERVEJA MULTIPACK": "CERVEJA",
        "GIRO RGB": "CERVEJA", "HE": "CERVEJA", "HE RGB": "CERVEJA",
        "TRIMARCA RGB HE (Original)": "CERVEJA", "TRIMARCA RGB HE (Stella)": "CERVEJA",
        "TRIMARCA RGB HE (Spaten)": "CERVEJA",
        "NAB": "NAB", "NAB ZERO": "NAB",
        "MATCH": "MATCH",
        "MKTP": "MKTP",
        "BALANCED CHOICE": "CERVEJA",
        "LITRINHO": "CERVEJA",
    }

    df_mix["_cat_macro"] = df_mix["categoria"].map(MAPA_MACRO)
    df_mix = df_mix[df_mix["_cat_macro"].notna()]

    df_mix["_vol"] = pd.to_numeric(df_mix["volume_total_hl"], errors="coerce").fillna(0)

    resultado = (
        df_mix.groupby(["setor", "_cat_macro"])["_vol"]
        .sum()
        .reset_index()
        .rename(columns={"_cat_macro": "categoria", "_vol": "volume_real_hl"})
    )
    resultado["mes_referencia"] = mes_referencia or date.today().strftime("%Y-%m")
    resultado["volume_real_hl"] = resultado["volume_real_hl"].round(3)

    sobrescrever_aba("rv_volume", resultado)
    print(f"  ✅ RV Volume: {len(resultado)} linhas")
    return resultado

# ─── RV — REMUNERAÇÃO VARIÁVEL ───────────────────────────────────────────────

META_PONTOS_BEES = 100_000  # Fixo. Alterar aqui se mudar.

SEGMENTO_OFF = {"101", "102", "103"}  # Match ativo, Mktp inativo
SEGMENTO_ON  = {"104", "105", "106", "301", "302", "303", "304", "305"}  # Mktp ativo, Match inativo


def processar_faturamento_mktp(conteudo_bytes):
    """
    Processa o arquivo 030509 (faturamento consolidado).
    Usa apenas: Vendedor, Cod.Produto, Total Venda.
    Filtra por categoria MKTP/MKTPLACE.
    """
    print("📂 Processando faturamento Mktp (030509)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Normaliza setor pelo campo Vendedor
    df["_setor"] = df["Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normaliza Total Venda
    df["_total_venda"] = (
        df["Total Venda"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    df["_total_venda"] = pd.to_numeric(df["_total_venda"], errors="coerce").fillna(0)

    # Carrega mapa de categorias para filtrar só Mktp
    df_prods = ler_aba("produtos_base")
    mapa_cat = {}
    if not df_prods.empty and "cod" in df_prods.columns:
        for _, row in df_prods.iterrows():
            cod = str(row.get("cod", "")).strip()
            cats = str(row.get("categorias", "")).strip()
            if cod and "MKTP" in cats.upper():
                mapa_cat[cod] = "MKTP"

    df["_cod_prod"] = df["Cod.Produto"].str.strip().str.lstrip("0")
    df["_categoria"] = df["_cod_prod"].map(mapa_cat)
    df_mktp = df[df["_categoria"] == "MKTP"].copy()

    # Agrega por setor
    resultado = (
        df_mktp.groupby("_setor")["_total_venda"]
        .sum()
        .reset_index()
        .rename(columns={"_setor": "setor", "_total_venda": "faturamento_mktp_real"})
    )
    resultado["mes_referencia"] = date.today().strftime("%Y-%m")
    resultado["faturamento_mktp_real"] = resultado["faturamento_mktp_real"].round(2)

    sobrescrever_aba("rv_faturamento_mktp", resultado)
    atualizar_status_arquivo("030509 (Faturamento Mktp)", "✅ OK", f"{len(resultado)} setores processados")
    print(f"  ✅ Faturamento Mktp: {len(resultado)} setores")
    return resultado


def processar_pontos_bees(conteudo_bytes):
    """
    Processa o Excel do BI de pontos Bees.
    Cabeçalho: Código Setor | Segmento RN | Coins Total
    """
    import io
    print("📂 Processando Pontos Bees...")
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str)
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # Tenta identificar colunas por nome aproximado
    col_setor = next((c for c in df.columns if "setor" in c.lower() or "código" in c.lower() or "codigo" in c.lower()), df.columns[0])
    col_pontos = next((c for c in df.columns if "coins" in c.lower() or "ponto" in c.lower() or "total" in c.lower()), df.columns[-1])

    df["_setor"] = df[col_setor].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    df["_pontos"] = pd.to_numeric(
        df[col_pontos].str.replace(",", ".", regex=False).str.strip(),
        errors="coerce"
    ).fillna(0)

    resultado = df[["_setor", "_pontos"]].rename(
        columns={"_setor": "setor", "_pontos": "pontos_real"}
    )
    resultado["pontos_meta"] = META_PONTOS_BEES
    resultado["pct_atingimento"] = (resultado["pontos_real"] / META_PONTOS_BEES * 100).round(1)
    resultado["mes_referencia"] = date.today().strftime("%Y-%m")

    sobrescrever_aba("rv_pontos_bees", resultado)
    atualizar_status_arquivo("Pontos Bees (BI)", "✅ OK", f"{len(resultado)} setores processados")
    print(f"  ✅ Pontos Bees: {len(resultado)} setores")
    return resultado


def calcular_rv_completa():
    """
    Calcula a RV completa para todos os RNs com base nos dados já processados.
    Grava na aba rv_resultado.
    """
    print("📊 Calculando RV completa...")

    # Carrega todas as fontes
    df_metas     = ler_aba("metas")
    df_cobertura = ler_aba("cobertura")
    df_pedidos   = ler_aba("rank_clientes")  # volume por setor
    df_pontos    = ler_aba("rv_pontos_bees")
    df_mktp      = ler_aba("rv_faturamento_mktp")
    df_ap        = ler_aba("rv_ap")

    # Mapa de metas por setor/categoria
    metas_map = {}
    po_total_map = {}
    for _, row in df_metas.iterrows():
        setor = str(row.get("setor", "")).strip()
        cat   = str(row.get("categoria", "")).strip().upper()
        meta  = pd.to_numeric(str(row.get("meta_volume", "0")).replace(",","."), errors="coerce") or 0
        peso  = pd.to_numeric(str(row.get("peso", "0")).replace(",","."), errors="coerce") or 0
        if setor not in metas_map:
            metas_map[setor] = {}
        metas_map[setor][cat] = {"meta": meta, "peso": peso}
        if cat == "PO_TOTAL":
            po_total_map[setor] = meta

    # Mapa de pontos Bees
    pontos_map = {}
    for _, row in df_pontos.iterrows():
        pontos_map[str(row.get("setor","")).strip()] = float(row.get("pontos_real", 0) or 0)

    # Mapa de faturamento Mktp
    mktp_map = {}
    for _, row in df_mktp.iterrows():
        mktp_map[str(row.get("setor","")).strip()] = float(row.get("faturamento_mktp_real", 0) or 0)

    # Mapa AP (atendimento produtivo)
    ap_map = {}
    for _, row in df_ap.iterrows():
        ap_map[str(row.get("setor","")).strip()] = str(row.get("ap_ok","NOK")).strip().upper()

    # Volume por setor (dos pedidos - últimos dados disponíveis)
    vol_map = {}
    for _, row in df_cobertura.iterrows():
        setor = str(row.get("setor","")).strip()
        cat   = str(row.get("categoria","")).strip().upper()
        # Conta PDVs OK como proxy de volume — volume real vem dos pedidos
        pass

    # Calcula por setor
    resultados = []
    todos_setores = list(SEGMENTO_OFF | SEGMENTO_ON)

    for setor in todos_setores:
        segmento = "OFF" if setor in SEGMENTO_OFF else "ON"
        ap_ok = ap_map.get(setor, "NOK")
        po_total = po_total_map.get(setor, 1500.0)

        # Pontos Bees (50% do PO)
        pontos_real = pontos_map.get(setor, 0)
        pontos_meta = META_PONTOS_BEES
        pct_pontos  = min((pontos_real / pontos_meta * 100) if pontos_meta > 0 else 0, 150)
        peso_pontos = metas_map.get(setor, {}).get("PONTOS BEES", {}).get("peso", 50)
        rv_pontos   = (po_total * peso_pontos / 100) * (pct_pontos / 100) if ap_ok == "OK" else 0

        # Volume Cerveja
        meta_cerv  = metas_map.get(setor, {}).get("CERVEJA", {}).get("meta", 0)
        peso_cerv  = metas_map.get(setor, {}).get("CERVEJA", {}).get("peso", 0)

        # Volume NAB
        meta_nab   = metas_map.get(setor, {}).get("NAB", {}).get("meta", 0)
        peso_nab   = metas_map.get(setor, {}).get("NAB", {}).get("peso", 0)

        # Match (OFF) ou Mktp (ON)
        if segmento == "OFF":
            meta_var  = metas_map.get(setor, {}).get("MATCH", {}).get("meta", 0)
            peso_var  = metas_map.get(setor, {}).get("MATCH", {}).get("peso", 0)
            var_label = "MATCH"
        else:
            meta_var  = metas_map.get(setor, {}).get("MARKETPLACE", {}).get("meta", 0)
            peso_var  = metas_map.get(setor, {}).get("MARKETPLACE", {}).get("peso", 0)
            var_label = "MARKETPLACE"

        resultados.append({
            "setor":          setor,
            "segmento":       segmento,
            "ap_ok":          ap_ok,
            "po_total":       po_total,
            "pontos_real":    pontos_real,
            "pontos_meta":    pontos_meta,
            "pct_pontos":     round(pct_pontos, 1),
            "peso_pontos":    peso_pontos,
            "rv_pontos":      round(rv_pontos, 2),
            "meta_cerveja":   meta_cerv,
            "peso_cerveja":   peso_cerv,
            "meta_nab":       meta_nab,
            "peso_nab":       peso_nab,
            f"meta_{var_label.lower()}": meta_var,
            f"peso_{var_label.lower()}": peso_var,
            "indicador_variavel": var_label,
            "mes_referencia": date.today().strftime("%Y-%m"),
        })

    df_resultado = pd.DataFrame(resultados)
    sobrescrever_aba("rv_resultado", df_resultado)
    print(f"  ✅ RV calculada: {len(df_resultado)} setores")
    return df_resultado


# ─── SPO — VISITAÇÃO GV NA BASE FOCO ────────────────────────────────────────

META_VISITACAO_GV = 36  # PDVs distintos por GV por mês (ON Trade)

def processar_visitacao_gv(conteudo_bytes):
    """
    Processa o relatório de Visitação GV na Base Foco.
    Colunas usadas: GV | SETOR | PDV | Visita Válida | GPS GV
    Uma visita é válida quando Visita Válida = OK e GPS GV = OK.
    """
    import io
    print("📂 Processando Visitação GV (SPO Item 1)...")

    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # Seleciona colunas relevantes
    col_gv      = next((c for c in df.columns if c.strip().upper() == "GV"), None)
    col_setor   = next((c for c in df.columns if c.strip().upper() == "SETOR"), None)
    col_pdv     = next((c for c in df.columns if c.strip().upper() == "PDV"), None)
    col_visita  = next((c for c in df.columns if "VISIT" in c.upper()), None)
    col_gps     = next((c for c in df.columns if "GPS" in c.upper()), None)

    print(f"  Colunas encontradas: gv={col_gv}, setor={col_setor}, pdv={col_pdv}, visita={col_visita}, gps={col_gps}")

    if not all([col_gv, col_setor, col_pdv, col_visita, col_gps]):
        raise ValueError(f"Colunas não encontradas. Disponíveis: {df.columns.tolist()}")

    df["_gv"]     = df[col_gv].str.strip()
    df["_setor"]  = df[col_setor].str.strip()
    df["_pdv"]    = df[col_pdv].str.strip().str.lstrip("0")
    df["_visita"] = df[col_visita].str.strip().str.upper()
    df["_gps"]    = df[col_gps].str.strip().str.upper()
    df["_valida"] = (df["_visita"] == "OK") & (df["_gps"] == "OK")

    # Carrega nomes e dia de visita dos PDVs da pdv_base
    try:
        df_base = ler_aba("pdv_base")
        mapa_nomes = {}
        mapa_dia = {}
        if not df_base.empty:
            for _, row in df_base.iterrows():
                cod = str(row.get("cod_pdv", row.get("cod", ""))).strip().lstrip("0")
                nome = str(row.get("nome_fantasia", row.get("nome", ""))).strip()
                dia = str(row.get("dia_visita", "")).strip()
                if cod:
                    mapa_nomes[cod] = nome
                    mapa_dia[cod] = dia
    except Exception:
        mapa_nomes = {}
        mapa_dia = {}

    df["_nome_pdv"] = df["_pdv"].map(mapa_nomes).fillna("")
    df["_dia_visita"] = df["_pdv"].map(mapa_dia).fillna("")
    df["mes_referencia"] = date.today().strftime("%Y-%m")

    # Grava detalhe linha por linha
    df_out = df[["_gv", "_setor", "_pdv", "_nome_pdv", "_dia_visita", "_visita", "_gps", "_valida", "mes_referencia"]].copy()
    df_out.columns = ["gv", "setor", "cod_pdv", "nome_pdv", "dia_visita", "visita_ok", "gps_ok", "valida", "mes_referencia"]
    df_out["valida"] = df_out["valida"].map({True: "SIM", False: "NÃO"})

    sobrescrever_aba("spo_visitacao_gv", df_out)

    # Resumo por GV
    resumo = []
    for gv in df_out["gv"].unique():
        df_gv = df_out[df_out["gv"] == gv]
        visitados = df_gv[df_gv["valida"] == "SIM"]["cod_pdv"].nunique()
        pct = round((visitados / META_VISITACAO_GV) * 100, 1)
        resumo.append({
            "gv": gv,
            "meta": META_VISITACAO_GV,
            "visitados": visitados,
            "pct": pct,
            "mes_referencia": date.today().strftime("%Y-%m"),
        })
        print(f"  GV {gv}: {visitados}/{META_VISITACAO_GV} PDVs ({pct}%)")

    df_resumo = pd.DataFrame(resumo)
    sobrescrever_aba("spo_visitacao_gv_resumo", df_resumo)
    atualizar_status_arquivo("SPO - Visitação GV", "✅ OK", f"{len(df_out)} linhas processadas")
    print(f"  ✅ Visitação GV processada: {len(df_out)} linhas")
    return df_out


# v2.5 - dias rota TT
# ─── SPO — ROTA COACHING ────────────────────────────────────────────────────

META_COACHING_TRI = 18  # 6 por mês × 3 meses

def processar_rota_coaching(conteudo_bytes):
    """
    Processa o relatório de Rota Coaching (Detalhamento Visitas).
    Filtra Tipo Visita = COACHING e Coaching Dia = OK.
    Calcula coachings válidos por GV e RNs cobertos no tri.
    """
    import io
    print("📂 Processando Rota Coaching (SPO Item 2)...")

    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # Filtra só COACHING
    df = df[df["Tipo Visita"].str.strip().str.upper().isin(["COACHING"])].copy()

    df["_gv"]     = df["GV"].str.strip()
    df["_setor"]  = df["Setor"].str.strip()
    df["_data"]   = pd.to_datetime(df["Data Visita"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["_ok"]     = df["Coaching Dia"].str.strip().str.upper() == "OK"
    df["_mes"]    = pd.to_datetime(df["Data Visita"], errors="coerce").dt.strftime("%Y-%m")

    # Detalhe: um registro por GV × RN × data
    df_det = df[["_gv", "_setor", "_data", "_mes", "_ok", "TMV Rota Dia", "Coachings Validos Dia", "Validação Segmento Coaching"]].copy()
    df_det.columns = ["gv", "setor", "data_visita", "mes_referencia", "coaching_ok", "tmv_rota", "coachings_validos", "validacao_segmento"]
    df_det["coaching_ok"] = df_det["coaching_ok"].map({True: "SIM", False: "NÃO"})

    sobrescrever_aba("spo_coaching_detalhe", df_det)

    # Resumo por GV — visão mensal + acumulado trimestral
    resumo = []
    meses_unicos = sorted(df["_mes"].dropna().unique())
    total_rns_por_gv = {"1": 6, "3": 5}  # GV1=sala1(6RNs), GV3=sala2(5RNs)

    for gv in sorted(df["_gv"].dropna().unique()):
        df_gv = df[df["_gv"] == gv]
        total_rns_sala = total_rns_por_gv.get(str(gv), 5)

        # === MENSAL ===
        for mes in meses_unicos:
            df_mes = df_gv[df_gv["_mes"] == mes]
            validos_mes = df_mes[df_mes["_ok"]].groupby(["_setor", "_data"]).size().reset_index()
            coachings_mes = len(validos_mes)
            rns_mes = df_mes[df_mes["_ok"]]["_setor"].nunique()
            pct_mes = round((coachings_mes / 6) * 100, 1)  # meta mensal = 6

            resumo.append({
                "gv": gv,
                "periodo": "mensal",
                "mes_referencia": mes,
                "coachings_validos": coachings_mes,
                "meta": 6,
                "pct": pct_mes,
                "rns_cobertos": rns_mes,
                "total_rns_sala": total_rns_sala,
                "gv_ok": "OK" if coachings_mes >= 6 else "NOK",
            })

        # === TRIMESTRAL (acumulado) ===
        validos_tri = df_gv[df_gv["_ok"]].groupby(["_setor", "_data"]).size().reset_index()
        total_tri = len(validos_tri)
        rns_tri = df_gv[df_gv["_ok"]]["_setor"].nunique()
        pct_tri = round((total_tri / META_COACHING_TRI) * 100, 1)
        gv_ok_tri = total_tri >= META_COACHING_TRI and rns_tri >= total_rns_sala
        mes_ref_tri = meses_unicos[-1] if meses_unicos else date.today().strftime("%Y-%m")

        resumo.append({
            "gv": gv,
            "periodo": "trimestral",
            "mes_referencia": mes_ref_tri,
            "coachings_validos": total_tri,
            "meta": META_COACHING_TRI,
            "pct": pct_tri,
            "rns_cobertos": rns_tri,
            "total_rns_sala": total_rns_sala,
            "gv_ok": "OK" if gv_ok_tri else "NOK",
        })
        print(f"  GV {gv}: TRI={total_tri}/{META_COACHING_TRI} ({pct_tri}%) | RNs: {rns_tri}/{total_rns_sala} | {'OK' if gv_ok_tri else 'NOK'}")

    df_resumo = pd.DataFrame(resumo)
    sobrescrever_aba("spo_coaching_resumo", df_resumo)
    # ── DIAS EM ROTA TT (Item 3) ────────────────────────────────────────────
    # Usa o mesmo DataFrame — conta dias únicos com Dia em Rota Válido = OK
    df_full = df.copy()  # df já está filtrado por COACHING, precisamos do arquivo completo
    # Nota: df_full aqui é só coaching. O arquivo completo é processado abaixo via df_rota
    # Calculamos dias em rota a partir do arquivo completo que foi passado
    try:
        df_rota = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str,
                                sheet_name="Export" if "Export" in pd.ExcelFile(io.BytesIO(conteudo_bytes)).sheet_names else 0)
    except Exception:
        df_rota = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df_rota.columns = [c.strip() for c in df_rota.columns]
    df_rota["_gv"]   = df_rota["GV"].str.strip()
    df_rota["_data"] = pd.to_datetime(df_rota["Data Visita"], errors="coerce").dt.strftime("%Y-%m-%d")
    df_rota["_mes"]  = pd.to_datetime(df_rota["Data Visita"], errors="coerce").dt.strftime("%Y-%m")
    df_rota["_ok"]   = df_rota["Dia em Rota Válido"].str.strip().str.upper() == "OK"

    resumo_rota = []
    meses_rota = sorted(df_rota["_mes"].dropna().unique())

    for gv in sorted(df_rota["_gv"].dropna().unique()):
        df_gv_r = df_rota[df_rota["_gv"] == gv]

        # Mensal
        for mes in meses_rota:
            df_m = df_gv_r[df_gv_r["_mes"] == mes]
            dias_ok = df_m[df_m["_ok"]]["_data"].nunique()
            pct = round((dias_ok / 15) * 100, 1)
            resumo_rota.append({
                "gv": gv, "periodo": "mensal", "mes_referencia": mes,
                "dias_validos": dias_ok, "meta": 15, "pct": pct,
                "gv_ok": "OK" if dias_ok >= 15 else "NOK",
            })

        # Trimestral
        dias_ok_tri = df_gv_r[df_gv_r["_ok"]]["_data"].nunique()
        pct_tri = round((dias_ok_tri / 45) * 100, 1)
        resumo_rota.append({
            "gv": gv, "periodo": "trimestral",
            "mes_referencia": meses_rota[-1] if meses_rota else date.today().strftime("%Y-%m"),
            "dias_validos": dias_ok_tri, "meta": 45, "pct": pct_tri,
            "gv_ok": "OK" if dias_ok_tri >= 45 else "NOK",
        })
        print(f"  GV {gv} Dias Rota TT: {dias_ok_tri}/45 ({pct_tri}%)")

    df_resumo_rota = pd.DataFrame(resumo_rota)
    sobrescrever_aba("spo_dias_rota_resumo", df_resumo_rota)
    atualizar_status_arquivo("SPO - Dias em Rota TT", "✅ OK", f"Calculado do mesmo arquivo")
    # ────────────────────────────────────────────────────────────────────────

    # RNs sem coaching no trimestre
    todos_setores = {
        "1": ["101","102","103","104","105","106"],
        "3": ["301","302","303","304","305"],
    }
    rns_com_coaching = df[df["_ok"]].groupby("_gv")["_setor"].apply(set).to_dict()
    mes_ref_tri = sorted(df["_mes"].dropna().unique())[-1] if len(df["_mes"].dropna().unique()) > 0 else date.today().strftime("%Y-%m")

    sem_coaching = []
    for gv, setores in todos_setores.items():
        cobertos = rns_com_coaching.get(gv, set())
        for setor in setores:
            if setor not in cobertos:
                sem_coaching.append({"gv": gv, "setor": setor, "mes_referencia": mes_ref_tri})

    df_sem = pd.DataFrame(sem_coaching) if sem_coaching else pd.DataFrame(columns=["gv","setor","mes_referencia"])
    sobrescrever_aba("spo_coaching_sem_coaching", df_sem)
    print(f"  ⚠️ RNs sem coaching: {len(df_sem)}")

    atualizar_status_arquivo("SPO - Rota Coaching", "✅ OK", f"{len(df_det)} registros processados")
    print(f"  ✅ Rota Coaching processada: {len(df_det)} registros")
    return df_det


# ─── SPO — DTO GC x GV ──────────────────────────────────────────────────────

META_DTO = {"matinal": 4, "vespertina": 4, "coaching": 2}

def processar_dto_gc(conteudo_bytes):
    """
    Processa o relatório de DTO GC x GV.
    Colunas: Status Final TRK | Matinal/Real/Status | Vespertina/Real/Status | Coaching/Real/Status
    """
    import io
    print("📂 Processando DTO GC x GV (SPO Item 6)...")

    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # Pega só a linha da CMD Conde
    df = df[df["Operação Agrupada"].str.strip().str.upper() == "CMD CONDE"].copy()
    if df.empty:
        raise ValueError("CMD Conde não encontrada no relatório.")

    row = df.iloc[0]

    # Extrai período dos filtros no arquivo
    mes_ref = date.today().strftime("%Y-%m")

    def get_val(col):
        try:
            v = str(row.get(col, "0") or "0").strip()
            return float(v.replace(",", ".")) if v not in ("", "nan", "NaN") else 0
        except:
            return 0

    matinal_real   = get_val("Real vs TRK")
    vespertina_real = get_val("Real vs TRK.1")
    coaching_real  = get_val("Real vs TRK.2")

    resultado = [{
        "mes_referencia":     mes_ref,
        "status_final":       str(row.get("Status Final TRK", "NOK")).strip(),
        "matinal_meta":       META_DTO["matinal"],
        "matinal_real":       round(matinal_real),
        "matinal_pct":        round((matinal_real / META_DTO["matinal"]) * 100, 1),
        "matinal_status":     str(row.get("Status Matinal", "NOK")).strip(),
        "vespertina_meta":    META_DTO["vespertina"],
        "vespertina_real":    round(vespertina_real),
        "vespertina_pct":     round((vespertina_real / META_DTO["vespertina"]) * 100, 1),
        "vespertina_status":  str(row.get("Status Vespertina", "NOK")).strip(),
        "coaching_meta":      META_DTO["coaching"],
        "coaching_real":      round(coaching_real),
        "coaching_pct":       round((coaching_real / META_DTO["coaching"]) * 100, 1),
        "coaching_status":    str(row.get("Status Rota Coaching", "NOK")).strip(),
    }]

    df_out = pd.DataFrame(resultado)
    sobrescrever_aba("spo_dto_resumo", df_out)
    atualizar_status_arquivo("SPO - DTO GC", "✅ OK", f"Matinal:{round(matinal_real)}/4 | Vesp:{round(vespertina_real)}/4 | Coach:{round(coaching_real)}/2")
    print(f"  ✅ DTO GC: Matinal {round(matinal_real)}/4 | Vespertina {round(vespertina_real)}/4 | Coaching {round(coaching_real)}/2 | Status: {resultado[0]['status_final']}")
    return df_out


# ─── SPO — % VISITAS ABRINDO ABA DE PROMOÇÃO (Item 7) ───────────────────────

META_PROMO_PCT = 10  # 10% das visitas

def processar_aba_promocao(conteudo_bytes):
    """
    Processa relatório de acesso à aba de Promoção no BEES.
    Colunas: unb_pdv | Visitas | Acesso Promoção | %Acesso Promoção | Meta | vs Meta
    Cruza cod_pdv com aba tasks para obter setor.
    """
    import io
    print("📂 Processando Aba Promoção BEES (SPO Item 7)...")

    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]
    df = df[df['unb_pdv'].notna() & df['Visitas'].notna()].copy()

    # Extrai cod_pdv
    df['_cod'] = df['unb_pdv'].str.split('_').str[-1].str.strip()
    df['_visitas'] = pd.to_numeric(df['Visitas'], errors='coerce').fillna(0)
    df['_acesso'] = pd.to_numeric(df['Acesso Promoção'], errors='coerce').fillna(0)

    # Carrega mapa cod_pdv → setor e dia_visita da pdv_base (base de clientes)
    mapa_setor = {}
    mapa_dia = {}
    try:
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            for _, row in df_base.iterrows():
                cod = str(row.get("cod_pdv", "")).strip()
                setor = str(row.get("setor", "")).strip().lstrip("0")
                dia = str(row.get("dia_visita", "")).strip()
                if cod and setor:
                    mapa_setor[cod] = setor
                if cod and dia:
                    mapa_dia[cod] = dia.split("/")[0].strip()
    except Exception as e:
        print(f"  ⚠️ Erro ao carregar pdv_base: {e}")

    df['_setor'] = df['_cod'].map(mapa_setor).fillna("")
    df['_dia_visita'] = df['_cod'].map(mapa_dia).fillna("")

    # Detalhe por PDV
    df_det = df[['_setor', '_cod', '_dia_visita', '_visitas', '_acesso']].copy()
    df_det.columns = ['setor', 'cod_pdv', 'dia_visita', 'visitas', 'acesso_promo']
    df_det['pct'] = (df_det['acesso_promo'] / df_det['visitas'].replace(0, 1) * 100).round(1)
    df_det['mes_referencia'] = date.today().strftime("%Y-%m")
    sobrescrever_aba("spo_promo_detalhe", df_det)

    # Resumo por setor
    setores_validos = SETORES_VALIDOS
    df_setor = df[df['_setor'].isin(setores_validos)].copy()

    resumo = []
    for setor in sorted(df_setor['_setor'].unique()):
        df_s = df_setor[df_setor['_setor'] == setor]
        total_vis = df_s['_visitas'].sum()
        total_ac = df_s['_acesso'].sum()
        pct = round((total_ac / total_vis * 100) if total_vis > 0 else 0, 1)
        resumo.append({
            'setor': setor,
            'visitas': int(total_vis),
            'acesso_promo': int(total_ac),
            'pct': pct,
            'meta': META_PROMO_PCT,
            'ok': "OK" if pct >= META_PROMO_PCT else "NOK",
            'mes_referencia': date.today().strftime("%Y-%m"),
        })
        print(f"  Setor {setor}: {int(total_ac)}/{int(total_vis)} ({pct}%)")

    # Total operação
    total_op_vis = df['_visitas'].sum()
    total_op_ac = df['_acesso'].sum()
    pct_op = round((total_op_ac / total_op_vis * 100) if total_op_vis > 0 else 0, 1)
    resumo.append({
        'setor': 'OPERACAO',
        'visitas': int(total_op_vis),
        'acesso_promo': int(total_op_ac),
        'pct': pct_op,
        'meta': META_PROMO_PCT,
        'ok': "OK" if pct_op >= META_PROMO_PCT else "NOK",
        'mes_referencia': date.today().strftime("%Y-%m"),
    })

    df_resumo = pd.DataFrame(resumo)
    sobrescrever_aba("spo_promo_resumo", df_resumo)
    atualizar_status_arquivo("SPO - Aba Promoção", "✅ OK", f"Operação: {pct_op}% ({int(total_op_ac)}/{int(total_op_vis)} visitas)")
    print(f"  ✅ Aba Promoção: {pct_op}% operação | {len([r for r in resumo if r['setor'] != 'OPERACAO'])} setores mapeados")
    return df_det


# ─── SPO — ADERÊNCIA DE POLÍTICA COMERCIAL (Item 8) ─────────────────────────

TASKS_TTC = {"03_05_09", "03_03_01", "03_04_01", "03_05_10"}
META_POLITICA = 60  # 60%

def calcular_politica_comercial():
    """Calcula aderência de política comercial a partir das tasks importadas."""
    print("📊 Calculando Política Comercial (SPO Item 8)...")

    TASKS_TTC_LOCAL = {"03_05_09", "03_03_01", "03_04_01", "03_05_10"}
    META_POLITICA_LOCAL = 60
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        print(f"  📋 Tasks: {len(df_tasks)} linhas")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        # Filtra tasks TTC
        ids = df_tasks["id_task"].str.strip()
        mask = ids.isin(TASKS_TTC_LOCAL)
        df_ttc = df_tasks[mask].copy()
        print(f"  Tasks TTC encontradas: {len(df_ttc)}")

        if df_ttc.empty:
            print(f"  ⚠️ Nenhuma task TTC. IDs amostra: {ids.unique()[:5].tolist()}")
            return pd.DataFrame()

        df_ttc["_setor"] = df_ttc["setor"].astype(str).str.strip()
        df_ttc["_pdv"]   = df_ttc["cod_pdv"].astype(str).str.strip()
        df_ttc["_ok"]    = df_ttc["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s_all = df_ttc[df_ttc["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s_all["_setor"].unique()):
            grp = df_s_all[df_s_all["_setor"] == setor]
            aderidos = grp["_pdv"].nunique()
            execucao = grp[grp["_ok"]]["_pdv"].nunique()
            pct = round(execucao / aderidos * 100, 1) if aderidos > 0 else 0
            resumo.append({"setor": setor, "pdvs_aderidos": aderidos, "pdvs_execucao": execucao,
                           "pct": pct, "meta": META_POLITICA_LOCAL,
                           "ok": "OK" if pct >= META_POLITICA_LOCAL else "NOK", "mes_referencia": mes_ref})
            print(f"  Setor {setor}: {execucao}/{aderidos} ({pct}%)")

        # Total operação
        total_ad  = df_s_all["_pdv"].nunique()
        total_val = df_s_all[df_s_all["_ok"]]["_pdv"].nunique()
        pct_op    = round(total_val / total_ad * 100, 1) if total_ad > 0 else 0
        resumo.append({"setor": "OPERACAO", "pdvs_aderidos": total_ad, "pdvs_execucao": total_val,
                       "pct": pct_op, "meta": META_POLITICA_LOCAL,
                       "ok": "OK" if pct_op >= META_POLITICA_LOCAL else "NOK", "mes_referencia": mes_ref})

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_politica_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Política Comercial", "✅ OK", f"Operação: {pct_op}% ({total_val}/{total_ad} PDVs)")
        print(f"  ✅ Política Comercial: {pct_op}% operação")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()

# ─── SPO — EXECUÇÃO MENU DE CERVEJA (Item 9) ────────────────────────────────

def calcular_execucao_menu():
    """
    Calcula % tasks de execução de TTC validadas por setor.
    Usa mesmas tasks do Item 8: 03_03_01, 03_05_09, 03_05_10.
    Denominador = total de tasks abertas (não PDVs).
    """
    print("📊 Calculando Execução Menu Cerveja (SPO Item 9)...")

    TASKS_MENU = {"03_03_01", "03_05_09", "03_05_10"}
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        ids = df_tasks["id_task"].astype(str).str.strip()
        df_menu = df_tasks[ids.isin(TASKS_MENU)].copy()
        print(f"  Tasks menu encontradas: {len(df_menu)}")

        if df_menu.empty:
            return pd.DataFrame()

        df_menu["_setor"] = df_menu["setor"].astype(str).str.strip()
        df_menu["_ok"] = df_menu["status"].astype(str).str.strip().str.upper() == "VALID"
        df_menu["_mes"] = df_menu.get("mes_ano", pd.Series(dtype=str)).astype(str).str.strip()
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df_menu[df_menu["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total = len(grp)
            validas = grp["_ok"].sum()
            pct = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor": setor, "tasks_total": total, "tasks_validas": int(validas),
                "pct": pct, "ok": "OK" if pct >= 46 else "NOK", "mes_referencia": mes_ref
            })
            print(f"  Setor {setor}: {int(validas)}/{total} ({pct}%)")

        # Total operação
        total_op = len(df_s)
        val_op = int(df_s["_ok"].sum())
        pct_op = round(val_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": val_op,
            "pct": pct_op, "ok": "OK" if pct_op >= 46 else "NOK", "mes_referencia": mes_ref
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_menu_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Execução Menu", "✅ OK", f"Operação: {pct_op}% ({val_op}/{total_op} tasks)")
        print(f"  ✅ Execução Menu: {pct_op}% operação")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()


# ─── SPO — TAREFAS DE PORTFÓLIO CERVEJA (Item 11) ────────────────────────────

META_TASKS_CERVEJA = 60  # Placeholder — ajustar por setor quando metas chegarem

def calcular_tarefas_cerveja():
    """
    Calcula tasks validadas do cluster Desenvolvimento de Portfólio / Cesta Cerveja.
    Cluster Primário = "Desenvolvimento de Portfólio"
    Categoria       = "beer" (coluna P do relatório de tasks)
    Tipo de cálculo tri: acumulado (soma dos meses).
    Gera aba: spo_tasks_cerveja_resumo
    """
    print("📊 Calculando Tarefas de Portfólio Cerveja (SPO Item 11)...")

    CLUSTER = "desenvolvimento de portfólio"
    CESTA   = "beer"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        # Filtro cluster + cesta
        mask_cluster = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER
        mask_cesta   = df_tasks["categoria"].astype(str).str.strip().str.lower() == CESTA
        df = df_tasks[mask_cluster & mask_cesta].copy()
        print(f"  Tasks Cerveja encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Cluster únicos: {df_tasks['cluster_primario'].astype(str).str.strip().unique()[:5].tolist()}")
            print(f"  ⚠️ Categoria únicos: {df_tasks['categoria'].astype(str).str.strip().unique()[:5].tolist()}")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total   = len(grp)
            validas = int(grp["_valida"].sum())
            pct     = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor":          setor,
                "tasks_total":    total,
                "tasks_validas":  validas,
                "pct":            pct,
                "ok":             "OK" if pct >= META_TASKS_CERVEJA else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")

        # Total operação
        total_op   = len(df_s)
        validas_op = int(df_s["_valida"].sum())
        pct_op     = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor":          "OPERACAO",
            "tasks_total":    total_op,
            "tasks_validas":  validas_op,
            "pct":            pct_op,
            "ok":             "OK" if pct_op >= META_TASKS_CERVEJA else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_cerveja_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks Cerveja", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks Cerveja: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()



# ─── SPO — TAREFAS DE PORTFÓLIO NAB (Item 13) ────────────────────────────────

META_TASKS_NAB = 60  # Placeholder

def calcular_tarefas_nab():
    """
    Calcula tasks validadas do cluster Desenvolvimento de Portfólio / Cesta NAB.
    Cluster Primário = "Desenvolvimento de Portfólio"
    Categoria       = "nab"
    Tri: acumulado.
    Gera aba: spo_tasks_nab_resumo
    """
    print("📊 Calculando Tarefas de Portfólio NAB (SPO Item 13)...")

    CLUSTER = "desenvolvimento de portfólio"
    CESTA   = "nab"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        mask_cluster = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER
        mask_cesta   = df_tasks["categoria"].astype(str).str.strip().str.lower() == CESTA
        df = df_tasks[mask_cluster & mask_cesta].copy()
        print(f"  Tasks NAB encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Cluster únicos: {df_tasks['cluster_primario'].astype(str).str.strip().unique()[:5].tolist()}")
            print(f"  ⚠️ Categoria únicos: {df_tasks['categoria'].astype(str).str.strip().unique()[:5].tolist()}")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total   = len(grp)
            validas = int(grp["_valida"].sum())
            pct     = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor": setor, "tasks_total": total, "tasks_validas": validas,
                "pct": pct, "ok": "OK" if pct >= META_TASKS_NAB else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")

        total_op   = len(df_s)
        validas_op = int(df_s["_valida"].sum())
        pct_op     = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
            "pct": pct_op, "ok": "OK" if pct_op >= META_TASKS_NAB else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_nab_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks NAB", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks NAB: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()

# ─── SPO — TASKS FATURAMENTO SCORE 5 (Item 12) ──────────────────────────────

META_SCORE5 = 46  # % — ajustar quando metas oficiais chegarem

def processar_score5(conteudo_bytes):
    """
    Processa o relatório ON_TRADE (Score 5 / Faturamento).
    Todos os PDVs do relatório são Score 5.
    BATEU META == 1 → task validada.
    Gera aba: spo_score5_resumo
    Colunas resultado: setor | pdvs_total | pdvs_ok | pct | meta | ok | mes_referencia
    """
    import io as _io
    print("📂 Processando Score 5 / Tasks Faturamento (SPO Item 12)...")

    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Normaliza setor (coluna RN)
        df["_setor"] = df["RN"].apply(normalizar_setor)
        df = df[df["_setor"].isin(SETORES_LOCAL)]

        # Remove linhas sem CHAVE PDV
        df = df[df["CHAVE PDV"].notna() & (df["CHAVE PDV"].str.strip() != "")]

        # BATEU META: 1 = OK
        df["_ok"] = df["BATEU META"].astype(str).str.strip() == "1"

        mes_ref = date.today().strftime("%Y-%m")
        resumo = []

        for setor in sorted(df["_setor"].unique()):
            grp = df[df["_setor"] == setor]
            total = len(grp)
            ok    = int(grp["_ok"].sum())
            pct   = round(ok / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor":          setor,
                "pdvs_total":     total,
                "pdvs_ok":        ok,
                "pct":            pct,
                "meta":           META_SCORE5,
                "ok":             "OK" if pct >= META_SCORE5 else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {ok}/{total} ({pct}%)")

        # Total operação
        total_op = len(df)
        ok_op    = int(df["_ok"].sum())
        pct_op   = round(ok_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor":          "OPERACAO",
            "pdvs_total":     total_op,
            "pdvs_ok":        ok_op,
            "pct":            pct_op,
            "meta":           META_SCORE5,
            "ok":             "OK" if pct_op >= META_SCORE5 else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_score5_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Score 5 (ON_TRADE)", "✅ OK",
                                 f"Operação: {pct_op}% ({ok_op}/{total_op} PDVs)")
        print(f"  ✅ Score 5: {pct_op}% operação ({ok_op}/{total_op} PDVs)")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()


# ─── SPO — TAREFAS DE VOLUME (Item 14) ───────────────────────────────────────

META_TASKS_VOLUME = 60  # Placeholder

def calcular_tarefas_volume():
    """
    Calcula tasks validadas do cluster Volume (sem filtro de cesta).
    Cluster Primário = "Volume"
    Tipo de cálculo tri: acumulado.
    Gera aba: spo_tasks_volume_resumo
    """
    print("📊 Calculando Tarefas de Volume (SPO Item 14)...")

    CLUSTER = "volume"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        mask_cluster = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER
        df = df_tasks[mask_cluster].copy()
        print(f"  Tasks Volume encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Cluster únicos: {df_tasks['cluster_primario'].astype(str).str.strip().unique()[:8].tolist()}")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total   = len(grp)
            validas = int(grp["_valida"].sum())
            pct     = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor":          setor,
                "tasks_total":    total,
                "tasks_validas":  validas,
                "pct":            pct,
                "ok":             "OK" if pct >= META_TASKS_VOLUME else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")

        total_op   = len(df_s)
        validas_op = int(df_s["_valida"].sum())
        pct_op     = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor":          "OPERACAO",
            "tasks_total":    total_op,
            "tasks_validas":  validas_op,
            "pct":            pct_op,
            "ok":             "OK" if pct_op >= META_TASKS_VOLUME else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_volume_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks Volume", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks Volume: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()


# ─── SPO — TAREFAS DE MARKETPLACE (Item 15) ──────────────────────────────────

META_TASKS_MKTP = 60  # Placeholder

def calcular_tarefas_marketplace():
    """
    Calcula tasks validadas do cluster Marketplace (sem filtro de cesta).
    Cluster Primário = "Marketplace"
    Tri: acumulado.
    Gera aba: spo_tasks_marketplace_resumo
    """
    print("📊 Calculando Tarefas de Marketplace (SPO Item 15)...")

    CLUSTER = "marketplace"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        mask_cluster = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER
        df = df_tasks[mask_cluster].copy()
        print(f"  Tasks Marketplace encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Cluster únicos: {df_tasks['cluster_primario'].astype(str).str.strip().unique()[:8].tolist()}")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total   = len(grp)
            validas = int(grp["_valida"].sum())
            pct     = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor": setor, "tasks_total": total, "tasks_validas": validas,
                "pct": pct, "ok": "OK" if pct >= META_TASKS_MKTP else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")

        total_op   = len(df_s)
        validas_op = int(df_s["_valida"].sum())
        pct_op     = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
            "pct": pct_op, "ok": "OK" if pct_op >= META_TASKS_MKTP else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_marketplace_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks Marketplace", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks Marketplace: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()


# ─── SPO — TAREFAS DE PORTFÓLIO MATCH (Item 16) ──────────────────────────────

META_TASKS_MATCH = 60  # Placeholder

def calcular_tarefas_match():
    """
    Cluster = Desenvolvimento de Portfólio / Cesta = match
    Tri: acumulado. Gera aba: spo_tasks_match_resumo
    """
    print("📊 Calculando Tarefas de Portfólio MATCH (SPO Item 16)...")
    CLUSTER = "desenvolvimento de portfólio"
    CESTA   = "match"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}
    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty: return pd.DataFrame()
        df = df_tasks[
            (df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER) &
            (df_tasks["categoria"].astype(str).str.strip().str.lower() == CESTA)
        ].copy()
        print(f"  Tasks MATCH encontradas: {len(df)}")
        if df.empty:
            print(f"  ⚠️ Categoria únicos: {df_tasks['categoria'].astype(str).str.strip().unique()[:8].tolist()}")
            return pd.DataFrame()
        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")
        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]
        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total = len(grp); validas = int(grp["_valida"].sum())
            pct = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({"setor": setor, "tasks_total": total, "tasks_validas": validas,
                           "pct": pct, "ok": "OK" if pct >= META_TASKS_MATCH else "NOK", "mes_referencia": mes_ref})
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")
        total_op = len(df_s); validas_op = int(df_s["_valida"].sum())
        pct_op = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({"setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
                       "pct": pct_op, "ok": "OK" if pct_op >= META_TASKS_MATCH else "NOK", "mes_referencia": mes_ref})
        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_match_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks MATCH", "✅ OK", f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks MATCH: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — TAREFAS DE PORTFÓLIO CERVEJA ZERO (Item 17) ───────────────────────

META_TASKS_CERV_ZERO = 60  # Placeholder

def calcular_tarefas_cerveja_zero():
    """
    Cluster = Desenvolvimento de Portfólio / Categoria = beer
    Filtro adicional: "Texto da Tarefa" contém \bzero\b ou \bcero\b (case insensitive)
    Exclui subZERO e similares pelo uso de word boundary.
    Tri: acumulado. Gera aba: spo_tasks_cerv_zero_resumo
    """
    print("📊 Calculando Tarefas de Portfólio Cerveja Zero (SPO Item 17)...")
    CLUSTER = "desenvolvimento de portfólio"
    CESTA   = "beer"
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}
    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia")
            return pd.DataFrame()

        # Filtro cluster + cesta
        mask_cluster = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER
        mask_cesta   = df_tasks["categoria"].astype(str).str.strip().str.lower() == CESTA
        df = df_tasks[mask_cluster & mask_cesta].copy()

        # Filtro texto da tarefa: \bzero\b ou \bcero\b (palavra isolada, case insensitive)
        mask_zero = df["descricao"].astype(str).str.contains(r"\bzero\b|\bcero\b", case=False, regex=True, na=False)
        df = df[mask_zero].copy()
        print(f"  Tasks Cerveja Zero encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Nenhuma task com 'zero'/'cero' no texto")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = date.today().strftime("%Y-%m")

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["_setor"].unique()):
            grp = df_s[df_s["_setor"] == setor]
            total = len(grp); validas = int(grp["_valida"].sum())
            pct = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({"setor": setor, "tasks_total": total, "tasks_validas": validas,
                           "pct": pct, "ok": "OK" if pct >= META_TASKS_CERV_ZERO else "NOK",
                           "mes_referencia": mes_ref})
            print(f"  Setor {setor}: {validas}/{total} ({pct}%)")

        total_op = len(df_s); validas_op = int(df_s["_valida"].sum())
        pct_op = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({"setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
                       "pct": pct_op, "ok": "OK" if pct_op >= META_TASKS_CERV_ZERO else "NOK",
                       "mes_referencia": mes_ref})

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_tasks_cerv_zero_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Tasks Cerveja Zero", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks Cerveja Zero: {pct_op}% operação ({validas_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — ORQUESTRADOR DE TASKS (evita quota exceeded) ─────────────────────

def calcular_todos_spo_tasks():
    """
    Lê a aba tasks UMA vez e repassa para todas as funções SPO baseadas em tasks.
    Evita exceder a quota de leitura do Google Sheets (60 req/min).
    """
    print("📊 Iniciando cálculo SPO tasks (leitura única)...")
    import time

    try:
        df_tasks = ler_aba("tasks")
        if df_tasks.empty:
            print("  ⚠️ Aba tasks vazia — abortando cálculos SPO")
            return
        print(f"  📋 Tasks carregadas: {len(df_tasks)} linhas")
    except Exception as e:
        print(f"  ❌ Erro ao carregar tasks: {e}")
        return

    funcoes = [
        ("Política Comercial",  lambda: _calcular_politica_com_df(df_tasks)),
        ("Execução Menu",       lambda: _calcular_menu_com_df(df_tasks)),
        ("Tasks Cerveja",       lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "beer",        None,           "spo_tasks_cerveja_resumo",    "SPO - Tasks Cerveja",    60)),
        ("Tasks NAB",           lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "nab",         None,           "spo_tasks_nab_resumo",        "SPO - Tasks NAB",        60)),
        ("Tasks Volume",        lambda: _calcular_tasks_com_df(df_tasks, "volume",                       None,          None,           "spo_tasks_volume_resumo",     "SPO - Tasks Volume",     60)),
        ("Tasks Marketplace",   lambda: _calcular_tasks_com_df(df_tasks, "marketplace",                  None,          None,           "spo_tasks_marketplace_resumo","SPO - Tasks Marketplace",60)),
        ("Tasks MATCH",         lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "match",       None,           "spo_tasks_match_resumo",      "SPO - Tasks MATCH",      60)),
        ("Tasks Cerveja Zero",  lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "beer",        r"\bzero\b|\bcero\b", "spo_tasks_cerv_zero_resumo", "SPO - Tasks Cerveja Zero", 60)),
        ("Tasks Digitalização", lambda: _calcular_tasks_com_df(df_tasks, "digitalização bees",            None,          None,           "spo_tasks_digit_resumo",      "SPO - Tasks Digitalização",60)),
    ]

    for nome, fn in funcoes:
        try:
            fn()
            time.sleep(1)  # respeita quota entre escritas
        except Exception as e:
            print(f"  ❌ Erro em {nome}: {e}")

    print("  ✅ Todos os cálculos SPO tasks concluídos")


def _calcular_tasks_com_df(df_tasks, cluster, cesta, filtro_texto, aba, status_nome, meta):
    """Calcula tasks SPO com DataFrame já carregado — evita chamada extra ao Sheets."""
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    mask = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == cluster
    if cesta:
        mask &= df_tasks["categoria"].astype(str).str.strip().str.lower() == cesta
    df = df_tasks[mask].copy()

    if filtro_texto:
        mask_txt = df["descricao"].astype(str).str.contains(filtro_texto, case=False, regex=True, na=False)
        df = df[mask_txt].copy()

    print(f"  [{status_nome}] {len(df)} tasks encontradas")
    if df.empty:
        return

    df["_setor"]  = df["setor"].astype(str).str.strip()
    df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
    mes_ref = date.today().strftime("%Y-%m")

    resumo = []
    df_s = df[df["_setor"].isin(SETORES_LOCAL)]

    for setor in sorted(df_s["_setor"].unique()):
        grp = df_s[df_s["_setor"] == setor]
        total = len(grp); validas = int(grp["_valida"].sum())
        pct = round(validas / total * 100, 1) if total > 0 else 0
        resumo.append({"setor": setor, "tasks_total": total, "tasks_validas": validas,
                       "pct": pct, "ok": "OK" if pct >= meta else "NOK", "mes_referencia": mes_ref})
        print(f"    Setor {setor}: {validas}/{total} ({pct}%)")

    total_op = len(df_s); validas_op = int(df_s["_valida"].sum())
    pct_op = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
    resumo.append({"setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
                   "pct": pct_op, "ok": "OK" if pct_op >= meta else "NOK", "mes_referencia": mes_ref})

    sobrescrever_aba(aba, pd.DataFrame(resumo))
    atualizar_status_arquivo(status_nome, "✅ OK", f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
    print(f"    ✅ {pct_op}% operação")


def _calcular_politica_com_df(df_tasks):
    """Wrapper de calcular_politica_comercial usando df já carregado."""
    TASKS_TTC = {"03_05_09", "03_03_01", "03_04_01", "03_05_10"}
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}
    META = 60

    ids = df_tasks["id_task"].astype(str).str.strip()
    df_ttc = df_tasks[ids.isin(TASKS_TTC)].copy()
    if df_ttc.empty: return

    df_ttc["_setor"] = df_ttc["setor"].astype(str).str.strip()
    df_ttc["_pdv"]   = df_ttc["cod_pdv"].astype(str).str.strip()
    df_ttc["_ok"]    = df_ttc["status"].astype(str).str.strip().str.upper() == "VALID"
    mes_ref = date.today().strftime("%Y-%m")

    resumo = []
    df_s = df_ttc[df_ttc["_setor"].isin(SETORES_LOCAL)]
    for setor in sorted(df_s["_setor"].unique()):
        grp = df_s[df_s["_setor"] == setor]
        ad = grp["_pdv"].nunique(); val = grp[grp["_ok"]]["_pdv"].nunique()
        pct = round(val / ad * 100, 1) if ad > 0 else 0
        resumo.append({"setor": setor, "pdvs_aderidos": ad, "pdvs_execucao": val,
                       "pct": pct, "meta": META, "ok": "OK" if pct >= META else "NOK", "mes_referencia": mes_ref})
    total_ad = df_s["_pdv"].nunique(); total_val = df_s[df_s["_ok"]]["_pdv"].nunique()
    pct_op = round(total_val / total_ad * 100, 1) if total_ad > 0 else 0
    resumo.append({"setor": "OPERACAO", "pdvs_aderidos": total_ad, "pdvs_execucao": total_val,
                   "pct": pct_op, "meta": META, "ok": "OK" if pct_op >= META else "NOK", "mes_referencia": mes_ref})
    sobrescrever_aba("spo_politica_resumo", pd.DataFrame(resumo))
    atualizar_status_arquivo("SPO - Política Comercial", "✅ OK", f"Operação: {pct_op}%")


def _calcular_menu_com_df(df_tasks):
    """Wrapper de calcular_execucao_menu usando df já carregado."""
    TASKS_MENU = {"03_03_01", "03_05_09", "03_05_10"}
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}
    META = 46

    ids = df_tasks["id_task"].astype(str).str.strip()
    df_menu = df_tasks[ids.isin(TASKS_MENU)].copy()
    if df_menu.empty: return

    df_menu["_setor"] = df_menu["setor"].astype(str).str.strip()
    df_menu["_ok"]    = df_menu["status"].astype(str).str.strip().str.upper() == "VALID"
    mes_ref = date.today().strftime("%Y-%m")

    resumo = []
    df_s = df_menu[df_menu["_setor"].isin(SETORES_LOCAL)]
    for setor in sorted(df_s["_setor"].unique()):
        grp = df_s[df_s["_setor"] == setor]
        total = len(grp); validas = int(grp["_ok"].sum())
        pct = round(validas / total * 100, 1) if total > 0 else 0
        resumo.append({"setor": setor, "tasks_total": total, "tasks_validas": validas,
                       "pct": pct, "ok": "OK" if pct >= META else "NOK", "mes_referencia": mes_ref})
    total_op = len(df_s); val_op = int(df_s["_ok"].sum())
    pct_op = round(val_op / total_op * 100, 1) if total_op > 0 else 0
    resumo.append({"setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": val_op,
                   "pct": pct_op, "ok": "OK" if pct_op >= META else "NOK", "mes_referencia": mes_ref})
    sobrescrever_aba("spo_menu_resumo", pd.DataFrame(resumo))
    atualizar_status_arquivo("SPO - Execução Menu", "✅ OK", f"Operação: {pct_op}%")


# ─── SPO — PDV COM COMPRA INDEPENDENTE / PEDIDO ALONE (Item 19) ──────────────

META_ALONE = {
    "2026-04": 837,
    "2026-05": 878,
    "2026-06": 910,
}

def processar_pedido_alone(conteudo_bytes):
    """
    Processa o relatório de Pedido Alone (export do BI Digitalização).
    Colunas: unb_pdv | Pedidos | Pedidos Independentes | %Pedidos Independentes
    Cruza com pdv_base para trazer setor, nome_fantasia, dia_visita.
    PDV alone = Pedidos Independentes > 0.
    Gera: spo_pedido_alone_resumo e spo_pedido_alone_detalhe
    """
    import io as _io
    print("📂 Processando PDV com Compra Independente (SPO Item 19)...")

    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Filtrar linhas válidas (unb_pdv no formato XXXXXXX_XXXXX)
        mask_valid = df["unb_pdv"].astype(str).str.match(r"^\d+_\d+$")
        df = df[mask_valid].copy()
        print(f"  Linhas válidas: {len(df)}")

        # Extrair código do PDV (parte após o _) e normalizar (remover zeros à esquerda)
        df["cod_pdv"] = df["unb_pdv"].astype(str).str.split("_").str[1]
        # Normalizar removendo .0 de floats se houver
        def norm_alone(v):
            s = str(v).strip()
            if s.endswith(".0"):
                s = s[:-2]
            return s
        df["cod_pdv_norm"] = df["cod_pdv"].apply(norm_alone)

        # Carregar pdv_base para cruzamento
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            df_base["_cod"] = df_base["cod_pdv"].astype(str).str.strip()
            mapa_setor  = df_base.set_index("_cod")["setor"].to_dict()
            # nome_fantasia é o campo correto na pdv_base
            if "nome_fantasia" in df_base.columns:
                mapa_nome = df_base.set_index("_cod")["nome_fantasia"].to_dict()
            elif "nome_pdv" in df_base.columns:
                mapa_nome = df_base.set_index("_cod")["nome_pdv"].to_dict()
            else:
                mapa_nome = {}
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_setor = mapa_nome = mapa_visita = {}

        df["setor"]      = df["cod_pdv_norm"].map(mapa_setor).fillna("").astype(str)
        df["nome_pdv"]   = df["cod_pdv_norm"].map(mapa_nome).fillna("").astype(str)
        df["dia_visita"] = df["cod_pdv_norm"].map(mapa_visita).fillna("").astype(str)

        # PDV alone = Pedidos Independentes > 0
        df["_alone"]         = pd.to_numeric(df["Pedidos Independentes"], errors="coerce").fillna(0) > 0
        df["_pedidos_alone"] = pd.to_numeric(df["Pedidos Independentes"], errors="coerce").fillna(0).astype(int)
        df["_pedidos_total"] = pd.to_numeric(df["Pedidos"], errors="coerce").fillna(0).astype(int)

        mes_ref = date.today().strftime("%Y-%m")
        meta_op = META_ALONE.get(mes_ref, 0)

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[df["setor"].isin(SETORES_LOCAL)][
            ["cod_pdv_norm","nome_pdv","setor","dia_visita","_pedidos_total","_pedidos_alone","_alone"]
        ].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","dia_visita","pedidos_total","pedidos_alone","is_alone"]
        df_det["is_alone"] = df_det["is_alone"].map({True: "SIM", False: "NÃO"})
        df_det["mes_referencia"] = mes_ref
        sobrescrever_aba("spo_pedido_alone_detalhe", df_det)

        # ── Resumo por setor ─────────────────────────────────────────────────
        resumo = []
        df_s = df[df["setor"].isin(SETORES_LOCAL)]

        for setor in sorted(df_s["setor"].unique()):
            grp = df_s[df_s["setor"] == setor]
            alone  = int(grp["_alone"].sum())
            total  = len(grp)
            resumo.append({
                "setor":          setor,
                "pdvs_alone":     alone,
                "pdvs_total":     total,
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {alone}/{total} PDVs alone")

        # Total operação
        alone_op = int(df_s["_alone"].sum())
        total_op = len(df_s)
        resumo.append({
            "setor":          "OPERACAO",
            "pdvs_alone":     alone_op,
            "pdvs_total":     total_op,
            "meta":           meta_op,
            "ok":             "OK" if alone_op >= meta_op else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_pedido_alone_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Pedido Alone", "✅ OK",
                                 f"Operação: {alone_op} PDVs alone (meta: {meta_op})")
        print(f"  ✅ Pedido Alone: {alone_op} PDVs alone / meta {meta_op}")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()
