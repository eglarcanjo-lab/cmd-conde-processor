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
    # index_col=False: impede que pandas use a 1ª coluna de dados como índice
    # quando o CSV tem ponto-e-vírgula no final de cada linha (campo extra vazio).
    return pd.read_csv(io.StringIO(texto), sep=";", dtype=str, low_memory=False, index_col=False)


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


def _mes_ref_do_dado(df, *cols, fallback=None):
    """
    Deriva o mês de referência (YYYY-MM) a partir das colunas informadas do DataFrame.
    Prioriza a coluna 'mes_ano' (tasks BI), depois colunas de data DD/MM/YYYY.
    Retorna fallback (ou date.today()) se não encontrar nada.

    Evita gravar mes_referencia = mês atual quando o dado é de um mês anterior —
    problema que acontece ao rodar o processador em 01/06 com dados de maio.
    """
    import re as _re
    for col in cols:
        if col not in df.columns:
            continue
        vals = df[col].astype(str).str.strip()
        vals = vals[vals.ne("") & vals.ne("nan") & vals.ne("None") & vals.ne("NaN")]
        if vals.empty:
            continue
        # Formato YYYY-MM direto (campo mes_ano do BI)
        m = vals[vals.str.match(r"^\d{4}-\d{2}")]
        if not m.empty:
            return m.mode()[0][:7]
        # Formato MM/YYYY
        m = vals[vals.str.match(r"^\d{2}/\d{4}$")]
        if not m.empty:
            v = m.mode()[0]
            return f"{v[3:7]}-{v[:2]}"
        # Formato DD/MM/YYYY → extrai mês do maior valor (mês mais recente dos dados)
        m = vals[vals.str.match(r"^\d{2}/\d{2}/\d{4}$")]
        if not m.empty:
            try:
                datas = pd.to_datetime(m, format="%d/%m/%Y", errors="coerce").dropna()
                if not datas.empty:
                    return datas.max().strftime("%Y-%m")
            except Exception:
                pass
        # Formato YYYY-MM-DD
        m = vals[vals.str.match(r"^\d{4}-\d{2}-\d{2}$")]
        if not m.empty:
            try:
                datas = pd.to_datetime(m, format="%Y-%m-%d", errors="coerce").dropna()
                if not datas.empty:
                    return datas.max().strftime("%Y-%m")
            except Exception:
                pass
    return fallback or date.today().strftime("%Y-%m")


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
    import gc
    print("📂 Processando pedidos...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # ── Memória: mantém só as colunas usadas (CSV do Promax tem dezenas) ───────
    COLS_USADAS = ["Setor", "Data", "Cliente", "Nome Cliente", "Cod. Prod.",
                   "Nome Prod.", "Volume Entrega", "Motivo", "Desc Tipo Movimento"]
    df = df[[c for c in COLS_USADAS if c in df.columns]].copy()
    gc.collect()

    # Normalizar setor
    df["_setor"] = df["Setor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normalizar data
    df["_data"] = pd.to_datetime(df["Data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    df = df[df["_data"].notna()]

    # Carregar base de produtos do Sheets para categorias
    df_prods_base = ler_aba("produtos_base")
    mapa_produtos = carregar_base_produtos(df_prods_base) if not df_prods_base.empty else {}

    # ── Resolver categoria — VETORIZADO (map em vez de apply axis=1) ───────────
    # mapa_produtos: cod_normalizado -> lista de categorias
    cod_norm = df["Cod. Prod."].astype(str).str.strip().str.lstrip("0").replace("", "0")
    df["_categorias"] = cod_norm.map(mapa_produtos)
    df["_categorias"] = df["_categorias"].apply(lambda x: x if isinstance(x, list) else [])
    df["_categoria"]  = df["_categorias"].apply(lambda x: x[0] if x else None)
    del cod_norm
    gc.collect()

    # Normalizar volume
    df["_volume"] = pd.to_numeric(
        df["Volume Entrega"].astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce"
    ).fillna(0.0)

    # Normalizar cod cliente
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0").str.strip()

    hoje = date.today()
    mes_atual = hoje.replace(day=1)
    mes_anterior = (mes_atual - pd.DateOffset(months=1)).date()
    quatro_meses_atras = (mes_atual - pd.DateOffset(months=4)).date()

    _datas = df["_data"].dt.date
    df_mes_atual = df[_datas >= mes_atual]
    df_mes_ant = df[(_datas >= mes_anterior) & (_datas < mes_atual)]
    df_4m = df[_datas >= quatro_meses_atras]
    del _datas

    # ── Cobertura ─────────────────────────────────────────────────────────────
    _processar_cobertura(df_mes_atual, df_mes_ant, df_clientes_base)
    del df_mes_ant
    gc.collect()

    # ── PDV Mix (últimos 4 meses) ─────────────────────────────────────────────
    _processar_mix(df_4m)
    gc.collect()

    # ── Rank clientes ─────────────────────────────────────────────────────────
    _processar_rank(df_4m)
    del df_4m
    gc.collect()

    # ── Volume RV (mês atual — para cálculo de remuneração) ───────────────────
    processar_volume_rv(df_mes_atual, date.today().strftime("%Y-%m"))
    del df_mes_atual
    gc.collect()

    # ── Faltas ────────────────────────────────────────────────────────────────
    _processar_faltas(df)
    gc.collect()

    # ── Devoluções ────────────────────────────────────────────────────────────
    _processar_devolucoes(df)
    gc.collect()

    # ── Volume Diário ─────────────────────────────────────────────────────────
    _processar_volume_diario(df)
    gc.collect()

    # ── Produtos sem categoria ────────────────────────────────────────────────
    _registrar_sem_categoria(df, mapa_produtos)

    n_linhas = len(df)
    del df
    gc.collect()
    atualizar_status_arquivo("03014701 (Pedidos)", "✅ OK", f"{n_linhas} linhas processadas")
    print(f"  ✅ Pedidos processados: {n_linhas} linhas")


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


def _processar_volume_diario(df):
    """Agrega volume faturado por data x setor x produto (para Volume Diário)."""
    df_v = df[df["_volume"] > 0].copy()
    df_v["_data_str"] = df_v["_data"].dt.strftime("%d/%m/%Y")

    vol = (
        df_v.groupby(["_data_str", "_setor", "Cod. Prod.", "Nome Prod."])
        ["_volume"].sum()
        .reset_index()
        .rename(columns={
            "_data_str":  "data",
            "_setor":     "setor",
            "Cod. Prod.": "cod_produto",
            "Nome Prod.": "nome_produto",
            "_volume":    "volume_hl",
        })
    )
    vol["volume_hl"] = vol["volume_hl"].round(3)
    sobrescrever_aba("volume_diario", vol)
    print(f"  📊 volume_diario: {len(vol)} linhas geradas")


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
    # Normaliza código: remove zeros à esquerda para bater com produtos_base
    df["_cod_str"] = df["Cod. Prod."].str.strip().str.lstrip("0").replace("", "0")

    # PRESERVA categorias da produtos_base — apenas adiciona produtos novos
    try:
        df_base_atual = ler_aba("produtos_base")
        mapa_base = {}  # {cod: {nome, categorias, atualizado_em}}
        if not df_base_atual.empty and "cod" in df_base_atual.columns:
            for _, row in df_base_atual.iterrows():
                cod = str(row.get("cod","")).strip().lstrip("0") or "0"
                if cod and cod != "0":
                    mapa_base[cod] = {
                        "nome": str(row.get("nome","")).strip(),
                        "categorias": str(row.get("categorias","")).strip(),
                        "atualizado_em": str(row.get("atualizado_em","")).strip(),
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

    # Preserva produtos da base existente que NÃO apareceram nos pedidos atuais.
    # Crítico para produtos MKTP/especiais cadastrados manualmente que não transitam
    # pelo arquivo de pedidos (030506) — sem isso eles são apagados a cada reimportação.
    cods_processados = {l["cod"] for l in linhas_base}
    for cod, dados in mapa_base.items():
        if cod not in cods_processados:
            linhas_base.append({
                "cod": cod,
                "nome": dados.get("nome", ""),
                "categorias": dados.get("categorias", ""),
                "atualizado_em": dados.get("atualizado_em", ""),
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
    Processa o arquivo 120601 (inadimplência).
    Gera a aba inadimplencia_real com títulos vencidos por PDV.
    """
    print("📂 Processando inadimplência...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]
    print(f"  Colunas encontradas: {list(df.columns)}")
    print(f"  Total de linhas brutas: {len(df)}")

    # Auto-detecta coluna de setor: procura qual coluna tem valores em SETORES_VALIDOS
    # No arquivo 120601 a coluna correta é "Vendedor" (código do RN: 0101→101)
    col_setor = None
    candidatos_setor = ["Vendedor", "Superv", "GDD", "UNB", "Setor", "setor", "RN"]
    # 1) tenta nomes conhecidos primeiro
    for c in candidatos_setor:
        if c in df.columns:
            hits = df[c].apply(normalizar_setor).isin(SETORES_VALIDOS).sum()
            if hits > 0:
                col_setor = c
                print(f"  Coluna de setor detectada: '{c}' ({hits} linhas válidas)")
                break
    # 2) se não achou, varre todas as colunas
    if col_setor is None:
        for c in df.columns:
            if not c or c.startswith("Unnamed"):
                continue
            hits = df[c].apply(normalizar_setor).isin(SETORES_VALIDOS).sum()
            if hits > 0:
                col_setor = c
                print(f"  Coluna de setor detectada (varredura): '{c}' ({hits} linhas válidas)")
                break
    if col_setor is None:
        raise ValueError(f"Nenhuma coluna com setores válidos encontrada. Colunas: {list(df.columns)}")

    # ── Mapeamento POSICIONAL FIXO do relatório 120601 (Promax) ─────────────────
    # Estrutura imutável — ignoramos nomes de coluna (podem ter artefatos de encoding):
    #   pos 1  → Cliente        (código numérico do PDV)
    #   pos 2  → Nome           (nome fantasia)
    #   pos 14 → Dias           (dias de atraso, negativo = vencido)
    #   pos 19 → ValorPendente  (valor ainda em aberto — sem correção por juros)
    cols = list(df.columns)
    n_cols = len(cols)

    if n_cols <= 19:
        raise ValueError(f"Arquivo 120601 com estrutura inesperada: apenas {n_cols} colunas. Colunas: {cols}")

    col_cliente = cols[1]
    col_nome    = cols[2]
    col_dias    = cols[14]
    col_valor   = cols[19]   # ValorPendente (pos 19) = valor real em aberto

    print(f"  Mapeamento posicional: setor='{col_setor}', cliente='{col_cliente}'(pos 1), nome='{col_nome}'(pos 2), dias='{col_dias}'(pos 14), valor='{col_valor}'(pos 19)")
    # Amostra dos 3 primeiros registros para diagnóstico
    for _, row in df.head(3).iterrows():
        print(f"    ex: cliente={str(row[col_cliente]).strip()}, nome={str(row[col_nome]).strip()}, dias={str(row[col_dias]).strip()}, valor={str(row[col_valor]).strip()}")

    # Normaliza setor
    df["_setor"] = df[col_setor].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    print(f"  Linhas após filtro de setor: {len(df)}")

    if df.empty:
        print("  ⚠️ Nenhuma linha válida após filtro de setor. Verifique os setores no arquivo.")
        sobrescrever_aba("inadimplencia_real", pd.DataFrame(columns=["setor","cod_pdv","nome_fantasia","qtd_titulos","valor_total","maior_atraso","aging"]))
        atualizar_status_arquivo("120601 (Inadimplência)", "⚠️ Sem dados", "Nenhum setor válido encontrado")
        return pd.DataFrame()

    # Normaliza cod PDV (remove zeros à esquerda) e nome
    df["_cod_pdv"] = df[col_cliente].astype(str).str.strip().str.lstrip("0").str.strip()
    df["_nome"]    = df[col_nome].astype(str).str.strip()

    # Dias: valores negativos = vencido há N dias
    df["_dias"] = pd.to_numeric(
        df[col_dias].astype(str).str.strip().str.replace(r"[^\d\-]", "", regex=True),
        errors="coerce"
    ).fillna(0)

    # Valor pendente: remove prefixo +, converte vírgula BR para ponto
    df["_valor"] = (
        df[col_valor].astype(str)
        .str.replace("+", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    df["_valor"] = pd.to_numeric(df["_valor"], errors="coerce").fillna(0)

    # AGING — busca pelo nome da coluna (tolerante a encoding)
    col_aging = next((c for c in df.columns if "AGING" in c.upper()), None)

    agg_dict = {
        "qtd_titulos": ("_valor", "count"),
        "valor_total": ("_valor", "sum"),
        "maior_atraso": ("_dias", "min"),
    }
    if col_aging:
        agg_dict["aging"] = (col_aging, lambda x: x.mode()[0] if len(x) > 0 else "")

    # Agrupa por PDV
    resumo = (
        df.groupby(["_setor", "_cod_pdv", "_nome"])
        .agg(**agg_dict)
        .reset_index()
        .rename(columns={
            "_setor": "setor",
            "_cod_pdv": "cod_pdv",
            "_nome": "nome_fantasia",
        })
    )

    if "aging" not in resumo.columns:
        resumo["aging"] = ""

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
    
    cats_rv = {
        # Cerveja (todas as variantes)
        "CERVEJA", "CERVEJA ZERO", "CERVEJA MULTIPACK",
        "GIRO RGB", "HE", "HE RGB",
        "TRIMARCA RGB HE (Original)", "TRIMARCA RGB HE (Stella)", "TRIMARCA RGB HE (Spaten)",
        "BALANCED CHOICE", "LITRINHO",
        # NAB (todas as variantes)
        "NAB", "NAB ZERO",
        # Match (OFF)
        "MATCH",
    }

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

SEGMENTO_OFF = {"101", "102", "103"}  # AS/Rota/Sub: Cerveja+NAB+Mktp+Match
SEGMENTO_ON  = {"104", "105", "106", "301", "302", "303", "304", "305"}  # On Trade: Cerveja+NAB+Mktp

# Pesos oficiais do regulamento de RV (% do PO). Pontos Force = 50% (Meios);
# os 50% restantes (Resultados) se dividem conforme o segmento.
# FIXOS no código — substituem o campo "peso" da planilha de metas (que vem corrompido).
PESOS_RV = {
    "OFF": {"pontos": 50, "cerveja": 25, "nab": 12.5, "marketplace": 7.5, "match": 5},
    "ON":  {"pontos": 50, "cerveja": 25, "nab": 15.0, "marketplace": 10.0, "match": 0},
}


def processar_faturamento_mktp(conteudo_bytes):
    """
    Processa o arquivo 030509 (GMV Marketplace por setor).
    Filtra apenas produtos com categoria MKTP em produtos_base.
    Se produtos_base não tiver produtos MKTP cadastrados, soma TUDO (fallback).
    Colunas usadas: Vendedor (setor, col N), Cod.Produto (col C), Total Venda (col G).
    Ignora linhas onde Vendedor não é um setor válido (ex.: "0").
    """
    print("📂 Processando faturamento Mktp (030509)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [c.strip() for c in df.columns]

    # Normaliza setor pelo campo Vendedor; ignora setores inválidos (ex: "0")
    df["_setor"] = df["Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]

    # Normaliza Total Venda (formato Promax: "00000000163,16" → 163.16)
    df["_total_venda"] = (
        df["Total Venda"]
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    df["_total_venda"] = pd.to_numeric(df["_total_venda"], errors="coerce").fillna(0)

    # Carrega mapa de categorias para filtrar apenas produtos MKTP
    # Normaliza zeros à esquerda em ambos os lados para garantir match consistente
    df_prods = ler_aba("produtos_base")
    mapa_mktp = set()
    if not df_prods.empty and "cod" in df_prods.columns:
        for _, row in df_prods.iterrows():
            cod  = (str(row.get("cod", "")).strip().lstrip("0") or "0")
            cats = str(row.get("categorias", "")).strip().upper()
            if cod and cod != "0" and "MKTP" in cats:
                mapa_mktp.add(cod)

    # Normaliza código do pedido também (remove zeros à esquerda)
    df["_cod_prod"] = df["Cod.Produto"].str.strip().str.lstrip("0").replace("", "0")

    if mapa_mktp:
        # Filtra apenas produtos MKTP cadastrados
        df_filtrado = df[df["_cod_prod"].isin(mapa_mktp)].copy()
        print(f"  🔍 Filtro MKTP: {len(df_filtrado)} linhas de {len(df)} (produtos: {len(mapa_mktp)} MKTP cadastrados)")
    elif "Tipo Marca" in df.columns:
        # Fallback confiável: Tipo Marca = 86 identifica produtos Marketplace no sistema
        df_filtrado = df[df["Tipo Marca"].str.strip() == "86"].copy()
        print(f"  ⚠️ Sem MKTP em produtos_base — fallback Tipo Marca=86: {len(df_filtrado)} linhas de {len(df)}")
    else:
        # Último recurso: nenhum filtro disponível
        df_filtrado = df.copy()
        print("  ⚠️ Nenhum filtro MKTP disponível — somando todos os produtos do 030509")

    # Agrega Total Venda por setor
    resultado = (
        df_filtrado.groupby("_setor")["_total_venda"]
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
    Processa o Excel do Force (Pontos Bees).
    Nova estrutura: Período | Geo | Operação | ID | Código UNB | Código Setor |
                    Segmento RN | Coins Total | Coins Mensais | Coins Diárias | ...

    Lógica: pega o último Período registrado (coluna A, formato YYYYMMDD)
    e usa o Coins Total (coluna H) desse dia por setor — que representa o
    acumulado do mês até aquele ponto.
    """
    import io, re
    print("📂 Processando Pontos Force (Bees)...")
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str)
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)

    df.columns = [c.strip() for c in df.columns]

    # ── Identifica colunas ────────────────────────────────────────────────────
    # Período: coluna A (YYYYMMDD)
    col_periodo = next((c for c in df.columns if "período" in c.lower() or "periodo" in c.lower()), df.columns[0])
    # Código Setor: coluna F
    col_setor   = next((c for c in df.columns if "setor" in c.lower()), None) or df.columns[5]
    # Coins Total: coluna H — acumulado do mês
    col_coins   = next((c for c in df.columns if "coins total" in c.lower() or (c.lower().startswith("coins") and "total" in c.lower())), None) or df.columns[7]

    print(f"  Colunas → período={col_periodo}, setor={col_setor}, coins_total={col_coins}")

    # ── Filtra linhas válidas (Período = 8 dígitos numéricos) ─────────────────
    df = df[df[col_periodo].astype(str).str.match(r"^\d{8}$", na=False)].copy()
    if df.empty:
        print("  ⚠️ Nenhuma linha válida encontrada.")
        return pd.DataFrame()

    # ── Último período disponível ─────────────────────────────────────────────
    ultimo_periodo = df[col_periodo].max()
    df_ultimo = df[df[col_periodo] == ultimo_periodo].copy()
    print(f"  Último período: {ultimo_periodo} → {len(df_ultimo)} linhas")

    # ── Normaliza setor e coins ───────────────────────────────────────────────
    df_ultimo["_setor"]  = df_ultimo[col_setor].apply(normalizar_setor)
    df_ultimo["_pontos"] = pd.to_numeric(
        df_ultimo[col_coins].astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce"
    ).fillna(0)

    df_ultimo = df_ultimo[df_ultimo["_setor"].isin(SETORES_VALIDOS)]

    # ── Mês de referência derivado do período (YYYYMMDD → YYYY-MM) ───────────
    mes_ref = f"{ultimo_periodo[:4]}-{ultimo_periodo[4:6]}"

    # ── Monta resultado ───────────────────────────────────────────────────────
    resultado = df_ultimo[["_setor", "_pontos"]].rename(
        columns={"_setor": "setor", "_pontos": "pontos_real"}
    ).copy()
    resultado["pontos_meta"]       = META_PONTOS_BEES
    resultado["pct_atingimento"]   = (resultado["pontos_real"] / META_PONTOS_BEES * 100).round(1)
    resultado["mes_referencia"]    = mes_ref

    sobrescrever_aba("rv_pontos_bees", resultado)
    atualizar_status_arquivo("Pontos Bees (BI)", "✅ OK",
        f"{len(resultado)} setores | período {ultimo_periodo} | mês {mes_ref}")
    print(f"  ✅ Pontos Force: {len(resultado)} setores, período {ultimo_periodo}, mês {mes_ref}")
    return resultado


def _normalizar_cat_rv(cat_raw):
    """
    Normaliza o nome da categoria salvo em 'metas' para a chave interna de RV.
    Remove o sufixo entre parênteses e aplica mapeamentos especiais.

    Exemplos:
      "CERVEJA (VOLUME)"        → "CERVEJA"
      "NAB (VOLUME)"            → "NAB"
      "MATCH (VOLUME)"          → "MATCH"
      "MARKETPLACE (FATURAMENTO)" → "MARKETPLACE"
      "PONTOS FORCE"            → "PONTOS BEES"
      "BALANCED CHOICE (VOLUME)"→ "BALANCED CHOICE"
      "PO_TOTAL"                → "PO_TOTAL"
    """
    cat = str(cat_raw).strip().upper()
    if "(" in cat:
        cat = cat[:cat.index("(")].strip()
    _MAP = {"PONTOS FORCE": "PONTOS BEES"}
    return _MAP.get(cat, cat)


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
        # Normaliza categoria: "CERVEJA (VOLUME)" → "CERVEJA", "PONTOS FORCE" → "PONTOS BEES"
        cat   = _normalizar_cat_rv(row.get("categoria", ""))
        meta  = pd.to_numeric(str(row.get("meta_volume", "0")).replace(",","."), errors="coerce") or 0
        # Normaliza peso: aceita decimal (0.25) ou inteiro (25) — converte tudo para % inteira
        peso_raw = pd.to_numeric(str(row.get("peso", "0")).replace(",","."), errors="coerce") or 0
        peso = round(peso_raw * 100, 4) if 0 < peso_raw < 1 else peso_raw
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

    # Volume real por setor/categoria (gerado por processar_pedidos → rv_volume)
    df_vol = ler_aba("rv_volume")
    vol_map = {}
    CERVEJA_CATS = {
        "CERVEJA", "CERVEJA ZERO", "CERVEJA MULTIPACK",
        "GIRO RGB", "HE", "HE RGB",
        "TRIMARCA RGB HE (ORIGINAL)", "TRIMARCA RGB HE (STELLA)", "TRIMARCA RGB HE (SPATEN)",
        "BALANCED CHOICE", "LITRINHO",
    }
    NAB_CATS = {"NAB", "NAB ZERO"}
    for _, row in df_vol.iterrows():
        setor = str(row.get("setor", "")).strip()
        cat   = str(row.get("categoria", "")).strip().upper()
        vol   = float(row.get("volume", 0) or 0)
        if setor not in vol_map:
            vol_map[setor] = {"CERVEJA": 0.0, "NAB": 0.0, "MATCH": 0.0}
        if cat in CERVEJA_CATS:
            vol_map[setor]["CERVEJA"] = round(vol_map[setor]["CERVEJA"] + vol, 3)
        elif cat in NAB_CATS:
            vol_map[setor]["NAB"] = round(vol_map[setor]["NAB"] + vol, 3)
        elif cat == "MATCH":
            vol_map[setor]["MATCH"] = round(vol_map[setor]["MATCH"] + vol, 3)

    # Calcula por setor
    resultados = []
    todos_setores = list(SEGMENTO_OFF | SEGMENTO_ON)

    for setor in todos_setores:
        segmento = "OFF" if setor in SEGMENTO_OFF else "ON"
        w = PESOS_RV[segmento]  # pesos fixos do regulamento
        ap_ok = ap_map.get(setor, "NOK")
        po_total = po_total_map.get(setor, 1000.0)

        # Pontos Force (Meios) — 50% do PO, sem piso
        pontos_real = pontos_map.get(setor, 0)
        pontos_meta = META_PONTOS_BEES
        pct_pontos  = min((pontos_real / pontos_meta * 100) if pontos_meta > 0 else 0, 150)
        peso_pontos = w["pontos"]
        rv_pontos   = (po_total * peso_pontos / 100) * (pct_pontos / 100) if ap_ok == "OK" else 0

        # Metas vêm da planilha; PESOS são fixos por segmento
        meta_cerv = metas_map.get(setor, {}).get("CERVEJA", {}).get("meta", 0)
        meta_nab  = metas_map.get(setor, {}).get("NAB", {}).get("meta", 0)
        meta_mktp = metas_map.get(setor, {}).get("MARKETPLACE", {}).get("meta", 0)
        meta_match = metas_map.get(setor, {}).get("MATCH", {}).get("meta", 0)

        # Realizados
        real_cerv  = round(vol_map.get(setor, {}).get("CERVEJA", 0.0), 3)
        real_nab   = round(vol_map.get(setor, {}).get("NAB", 0.0), 3)
        real_mktp  = round(mktp_map.get(setor, 0.0), 2)
        real_match = round(vol_map.get(setor, {}).get("MATCH", 0.0), 3)

        # Indicador variável principal (compat): OFF→Match, ON→Marketplace
        var_label = "MATCH" if segmento == "OFF" else "MARKETPLACE"

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
            "real_cerveja":   real_cerv,
            "meta_cerveja":   meta_cerv,
            "peso_cerveja":   w["cerveja"],
            "real_nab":       real_nab,
            "meta_nab":       meta_nab,
            "peso_nab":       w["nab"],
            "real_match":         real_match,
            "meta_match":         meta_match,
            "peso_match":         w["match"],
            "real_marketplace":   real_mktp,
            "meta_marketplace":   meta_mktp,
            "peso_marketplace":   w["marketplace"],
            "indicador_variavel": var_label,
            "mes_referencia": date.today().strftime("%Y-%m"),
        })

    df_resultado = pd.DataFrame(resultados)
    sobrescrever_aba("rv_resultado", df_resultado)
    print(f"  ✅ RV calculada: {len(df_resultado)} setores")
    return df_resultado


# ─── SPO — VISITAÇÃO GV NA BASE FOCO ────────────────────────────────────────

META_VISITACAO_GV = 36  # PDVs distintos por GV por mês (ON Trade)

def processar_visitacao_gv(conteudo_bytes, mes_ref=None):
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
    # Mês: usa parâmetro explícito (da UI) com prioridade; fallback tenta colunas do arquivo
    _mes_visita = mes_ref or _mes_ref_do_dado(df, "Mês", "Período", "Data Visita", "Data", "mes", "data")
    df["mes_referencia"] = _mes_visita

    # Grava detalhe linha por linha
    df_out = df[["_gv", "_setor", "_pdv", "_nome_pdv", "_dia_visita", "_visita", "_gps", "_valida", "mes_referencia"]].copy()
    df_out.columns = ["gv", "setor", "cod_pdv", "nome_pdv", "dia_visita", "visita_ok", "gps_ok", "valida", "mes_referencia"]
    df_out["valida"] = df_out["valida"].map({True: "SIM", False: "NÃO"})

    # Filtrar linhas com GV vazio/invalido antes de gravar e resumir
    gvs_invalidos_mask = df_out["gv"].astype(str).str.strip().isin(["", "nan", "None", "NaN"])
    n_invalidos = gvs_invalidos_mask.sum()
    if n_invalidos > 0:
        print(f"  ⚠️ {n_invalidos} linhas com GV vazio/invalido ignoradas: {df_out[gvs_invalidos_mask]['cod_pdv'].tolist()[:10]}")
    df_out = df_out[~gvs_invalidos_mask].copy()

    sobrescrever_aba("spo_visitacao_gv", df_out)

    # Resumo por GV — apenas GVs validos (ex: "1", "3")
    resumo = []
    gvs_validos = sorted([g for g in df_out["gv"].unique() if str(g).strip() not in ("", "nan", "None", "NaN")])
    print(f"  GVs encontrados no arquivo: {gvs_validos}")
    for gv in gvs_validos:
        df_gv = df_out[df_out["gv"] == gv]
        visitados = df_gv[df_gv["valida"] == "SIM"]["cod_pdv"].nunique()
        total_linhas = len(df_gv)
        n_validas = (df_gv["valida"] == "SIM").sum()
        print(f"  GV {gv}: {total_linhas} linhas, {n_validas} validas, {visitados} PDVs unicos validos")
        pct = round((visitados / META_VISITACAO_GV) * 100, 1)
        resumo.append({
            "gv": gv,
            "meta": META_VISITACAO_GV,
            "visitados": visitados,
            "pct": pct,
            "mes_referencia": _mes_visita,
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

def processar_dto_gc(conteudo_bytes, mes_ref=None):
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

    # Mês: usa parâmetro explícito (da UI) com prioridade; fallback tenta colunas do arquivo
    mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Período", "Data", "mes", "data", "Referência")

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

    df_novo = pd.DataFrame(resultado)

    # Acumula por mês: mantém registros de outros meses, sobrescreve o mês atual
    try:
        df_existente = ler_aba("spo_dto_resumo")
        if not df_existente.empty and "mes_referencia" in df_existente.columns:
            df_outros = df_existente[df_existente["mes_referencia"] != mes_ref]
            df_out = pd.concat([df_outros, df_novo], ignore_index=True)
        else:
            df_out = df_novo
    except Exception:
        df_out = df_novo

    sobrescrever_aba("spo_dto_resumo", df_out)
    atualizar_status_arquivo("SPO - DTO GC", "✅ OK", f"Matinal:{round(matinal_real)}/4 | Vesp:{round(vespertina_real)}/4 | Coach:{round(coaching_real)}/2")
    print(f"  ✅ DTO GC: Matinal {round(matinal_real)}/4 | Vespertina {round(vespertina_real)}/4 | Coaching {round(coaching_real)}/2 | Status: {resultado[0]['status_final']}")
    return df_out


# ─── SPO — % VISITAS ABRINDO ABA DE PROMOÇÃO (Item 7) ───────────────────────

META_PROMO_PCT = 10  # 10% das visitas

def processar_aba_promocao(conteudo_bytes, mes_ref=None):
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

    # Coluna E (%Acesso Promoção) — valores decimais (0.2 = 20%).
    # Usamos posição fixa (índice 4) para evitar problemas de encoding com o nome.
    col_pct_promo = df.columns[4]
    df['_pct_raw'] = pd.to_numeric(df[col_pct_promo], errors='coerce').fillna(0)

    # Detalhe por PDV — pct vem direto da coluna % (mais fiel que recalcular)
    df_det = df[['_setor', '_cod', '_dia_visita', '_visitas', '_acesso', '_pct_raw']].copy()
    df_det.columns = ['setor', 'cod_pdv', 'dia_visita', 'visitas', 'acesso_promo', '_pct_raw']
    df_det['pct'] = (df_det['_pct_raw'] * 100).round(1)
    df_det = df_det.drop(columns=['_pct_raw'])
    df_det['mes_referencia'] = date.today().strftime("%Y-%m")
    sobrescrever_aba("spo_promo_detalhe", df_det)

    # Resumo por setor — usa média simples da coluna %Acesso Promoção (não razão de totais)
    setores_validos = SETORES_VALIDOS
    df_setor = df[df['_setor'].isin(setores_validos)].copy()

    resumo = []
    for setor in sorted(df_setor['_setor'].unique()):
        df_s = df_setor[df_setor['_setor'] == setor]
        total_vis = int(df_s['_visitas'].sum())
        total_ac  = int(df_s['_acesso'].sum())
        pct = round(df_s['_pct_raw'].mean() * 100, 1)
        resumo.append({
            'setor': setor,
            'visitas': total_vis,
            'acesso_promo': total_ac,
            'pct': pct,
            'meta': META_PROMO_PCT,
            'ok': "OK" if pct >= META_PROMO_PCT else "NOK",
            'mes_referencia': date.today().strftime("%Y-%m"),
        })
        print(f"  Setor {setor}: {total_ac}/{total_vis} ({pct}%)")

    # Total operação — média simples de TODOS os PDVs do arquivo
    total_op_vis = int(df['_visitas'].sum())
    total_op_ac  = int(df['_acesso'].sum())
    pct_op = round(df['_pct_raw'].mean() * 100, 1)
    resumo.append({
        'setor': 'OPERACAO',
        'visitas': total_op_vis,
        'acesso_promo': total_op_ac,
        'pct': pct_op,
        'meta': META_PROMO_PCT,
        'ok': "OK" if pct_op >= META_PROMO_PCT else "NOK",
        'mes_referencia': date.today().strftime("%Y-%m"),
    })

    df_resumo = pd.DataFrame(resumo)
    sobrescrever_aba("spo_promo_resumo", df_resumo)
    atualizar_status_arquivo("SPO - Aba Promoção", "✅ OK", f"Operação: {pct_op}% (média simples | {total_op_ac}/{total_op_vis} acessos/visitas)")
    print(f"  ✅ Aba Promoção: {pct_op}% operação (média simples) | {len([r for r in resumo if r['setor'] != 'OPERACAO'])} setores mapeados")
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
        mes_ref = _mes_ref_do_dado(df_ttc, "mes_ano")

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
        mes_ref = _mes_ref_do_dado(df_menu, "mes_ano")

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
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()

        resumo = []
        df_s = df[df["_setor"].isin(SETORES_LOCAL)].copy()

        # KPI 11 — somatório de tasks VALID (acumulado tri)
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
            print(f"  Setor {setor}: {validas}/{total} tasks ({pct}%)")

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

        # Detalhe: tasks em aberto por PDV (OPEN ou INVALID)
        df_det = df_s.copy()
        df_det["status_task"] = df_det["status"].astype(str).str.strip()
        df_aberto = df_det[~df_det["_valida"]][["_setor","cod_pdv","nome_pdv","dia_visita","status_task"]].drop_duplicates(subset=["cod_pdv"])
        df_aberto.columns = ["setor","cod_pdv","nome_pdv","dia_visita","status_task"]
        df_aberto["mes_referencia"] = mes_ref
        sobrescrever_aba("spo_tasks_cerveja_detalhe", df_aberto)

        atualizar_status_arquivo("SPO - Tasks Cerveja", "✅ OK",
                                 f"Operação: {pct_op}% ({validas_op}/{total_op} tasks)")
        print(f"  ✅ Tasks Cerveja: {pct_op}% operação ({validas_op}/{total_op} tasks)")
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
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()

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

def processar_score5(conteudo_bytes, mes_ref=None):
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

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes")
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
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()

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
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()

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
            # Log detalhado para diagnóstico
            cats_no_cluster = df_tasks[df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER]["categoria"].astype(str).str.strip().unique()
            print(f"  ⚠️ Categorias no cluster '{CLUSTER}': {cats_no_cluster.tolist()}")
            # Tentar busca parcial (contém "match")
            df_parcial = df_tasks[
                (df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == CLUSTER) &
                (df_tasks["categoria"].astype(str).str.strip().str.lower().str.contains("match", na=False))
            ].copy()
            print(f"  ⚠️ Tasks com 'match' parcial: {len(df_parcial)}")
            if not df_parcial.empty:
                print(f"  ⚠️ Valores únicos de categoria contendo match: {df_parcial['categoria'].unique().tolist()}")
                df = df_parcial  # usar busca parcial como fallback
            else:
                print(f"  ⚠️ Todas categorias únicas no arquivo: {df_tasks['categoria'].astype(str).str.strip().unique()[:15].tolist()}")
                return pd.DataFrame()
        df["_setor"]  = df["setor"].astype(str).str.strip()
        df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()
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
        mes_ref = _mes_ref_do_dado(df, "mes_ano")  # deriva do dado processado, não de date.today()

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

    # Deriva o mês dos dados UMA vez e passa para todas as funções —
    # evita usar date.today() que causaria gravar junho quando processando maio
    _mr = _mes_ref_do_dado(df_tasks, "mes_ano", "data_visita")
    print(f"  📅 Mês de referência derivado dos dados: {_mr}")

    funcoes = [
        ("Política Comercial",  lambda: _calcular_politica_com_df(df_tasks, mes_ref=_mr)),
        ("Execução Menu",       lambda: _calcular_menu_com_df(df_tasks, mes_ref=_mr)),
        ("Tasks Cerveja",       lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "beer",        None,           "spo_tasks_cerveja_resumo",    "SPO - Tasks Cerveja",    60, mes_ref=_mr)),
        ("Tasks NAB",           lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "nab",         None,           "spo_tasks_nab_resumo",        "SPO - Tasks NAB",        60, mes_ref=_mr)),
        ("Tasks Volume",        lambda: _calcular_tasks_com_df(df_tasks, "volume",                       None,          None,           "spo_tasks_volume_resumo",     "SPO - Tasks Volume",     60, mes_ref=_mr)),
        ("Tasks Marketplace",   lambda: _calcular_tasks_com_df(df_tasks, "marketplace",                  None,          None,           "spo_tasks_marketplace_resumo","SPO - Tasks Marketplace",60, mes_ref=_mr)),
        ("Tasks MATCH",         lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "match",       None,           "spo_tasks_match_resumo",      "SPO - Tasks MATCH",      60, mes_ref=_mr)),
        ("Tasks Cerveja Zero",  lambda: _calcular_tasks_com_df(df_tasks, "desenvolvimento de portfólio", "beer",        r"\bzero\b|\bcero\b", "spo_tasks_cerv_zero_resumo", "SPO - Tasks Cerveja Zero", 60, mes_ref=_mr)),
        ("Tasks Digitalização", lambda: _calcular_tasks_com_df(df_tasks, "digitalização bees",            None,          None,           "spo_tasks_digit_resumo",      "SPO - Tasks Digitalização",60, mes_ref=_mr)),
    ]

    for nome, fn in funcoes:
        try:
            fn()
            time.sleep(1)  # respeita quota entre escritas
        except Exception as e:
            print(f"  ❌ Erro em {nome}: {e}")

    print("  ✅ Todos os cálculos SPO tasks concluídos")


def _calcular_tasks_com_df(df_tasks, cluster, cesta, filtro_texto, aba, status_nome, meta, mes_ref=None):
    """Calcula tasks SPO com DataFrame já carregado — evita chamada extra ao Sheets."""
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    mask = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == cluster
    if cesta:
        mask &= df_tasks["categoria"].astype(str).str.strip().str.lower() == cesta
    df = df_tasks[mask].copy()

    if df.empty and cesta:
        # Fallback: busca parcial na categoria (ex: "match" dentro de "portfolio match")
        mask_parcial = df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == cluster
        mask_parcial &= df_tasks["categoria"].astype(str).str.strip().str.lower().str.contains(cesta, na=False)
        df = df_tasks[mask_parcial].copy()
        if not df.empty:
            print(f"  [{status_nome}] busca parcial '{cesta}' → {len(df)} tasks | categorias: {df['categoria'].unique().tolist()}")
        else:
            cats = df_tasks[df_tasks["cluster_primario"].astype(str).str.strip().str.lower() == cluster]["categoria"].astype(str).str.strip().unique().tolist()
            print(f"  [{status_nome}] ⚠️ 0 tasks — categorias no cluster '{cluster}': {cats[:10]}")
            return

    if filtro_texto:
        mask_txt = df["descricao"].astype(str).str.contains(filtro_texto, case=False, regex=True, na=False)
        df = df[mask_txt].copy()

    print(f"  [{status_nome}] {len(df)} tasks encontradas")
    if df.empty:
        return

    df["_setor"]  = df["setor"].astype(str).str.strip()
    df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
    # Deriva o mês do dado (campo mes_ano do BI), não da data atual
    mes_ref = mes_ref or _mes_ref_do_dado(df, "mes_ano", "data_visita")

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


def _calcular_politica_com_df(df_tasks, mes_ref=None):
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
    mes_ref = mes_ref or _mes_ref_do_dado(df_ttc, "mes_ano")

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


def _calcular_menu_com_df(df_tasks, mes_ref=None):
    """Wrapper de calcular_execucao_menu usando df já carregado."""
    TASKS_MENU = {"03_03_01", "03_05_09", "03_05_10"}
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}
    META = 46

    ids = df_tasks["id_task"].astype(str).str.strip()
    df_menu = df_tasks[ids.isin(TASKS_MENU)].copy()
    if df_menu.empty: return

    df_menu["_setor"] = df_menu["setor"].astype(str).str.strip()
    df_menu["_ok"]    = df_menu["status"].astype(str).str.strip().str.upper() == "VALID"
    mes_ref = mes_ref or _mes_ref_do_dado(df_menu, "mes_ano")

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

def processar_pedido_alone(conteudo_bytes, mes_ref=None):
    """
    Processa o relatório de Pedido Alone (export do BI Digitalização).
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
        mask_valid = df["unb_pdv"].astype(str).str.match(r"^\d+_\d+$")
        df = df[mask_valid].copy()
        print(f"  Linhas válidas: {len(df)}")

        # Extrair cod_pdv (após o _)
        df["cod_pdv"] = df["unb_pdv"].astype(str).str.split("_").str[1]

        def norm(v):
            s = str(v).strip()
            if s.endswith(".0"): s = s[:-2]
            return s

        df["cod_pdv_norm"] = df["cod_pdv"].apply(norm)

        # PDV alone = Pedidos Independentes > 0 (calculado antes do cruzamento)
        df["_alone"]         = pd.to_numeric(df["Pedidos Independentes"], errors="coerce").fillna(0) > 0
        df["_pedidos_alone"] = pd.to_numeric(df["Pedidos Independentes"], errors="coerce").fillna(0).astype(int)
        df["_pedidos_total"] = pd.to_numeric(df["Pedidos"], errors="coerce").fillna(0).astype(int)

        # Carregar pdv_base para cruzamento
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            mapa_setor  = df_base.set_index("_cod")["setor"].to_dict()
            if "nome_fantasia" in df_base.columns:
                mapa_nome = df_base.set_index("_cod")["nome_fantasia"].to_dict()
            elif "nome_pdv" in df_base.columns:
                mapa_nome = df_base.set_index("_cod")["nome_pdv"].to_dict()
            else:
                mapa_nome = {}
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_setor = mapa_nome = mapa_visita = {}

        df["setor"]      = df["cod_pdv_norm"].map(mapa_setor).fillna("").astype(str).str.strip()
        df["setor"]      = df["setor"].apply(lambda x: str(int(float(x))) if x.replace(".","").isdigit() and x != "" else x)
        df["nome_pdv"]   = df["cod_pdv_norm"].map(mapa_nome).fillna("").astype(str)
        df["dia_visita"] = df["cod_pdv_norm"].map(mapa_visita).fillna("").astype(str)

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")
        meta_op = META_ALONE.get(mes_ref, 0)

        df_s = df[df["setor"].isin(SETORES_LOCAL)]
        print(f"  PDVs nos setores locais: {len(df_s)} | Alone: {int(df_s['_alone'].sum())}")

        # Detalhe — inclui SKU mais comprado por PDV alone
        df_det = df_s[["cod_pdv_norm","nome_pdv","setor","dia_visita","_pedidos_total","_pedidos_alone","_alone"]].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","dia_visita","pedidos_total","pedidos_alone","is_alone"]
        df_det["is_alone"] = df_det["is_alone"].map({True: "SIM", False: "NÃO"})
        df_det["mes_referencia"] = mes_ref
        # SKU mais comprado: tenta coluna "Produto Mais Comprado" ou similar
        sku_cols = [c for c in df_s.columns if any(k in c.lower() for k in ["produto","sku","item","descri"])]
        if sku_cols:
            df_det["sku_top"] = df_s[sku_cols[0]].fillna("").astype(str).values
        else:
            df_det["sku_top"] = ""
        sobrescrever_aba("spo_pedido_alone_detalhe", df_det)

        # Resumo por setor — meta individual por RN com folga 5%
        # Meta operação dividida proporcionalmente por PDVs de cada setor, depois +5% de folga
        resumo = []
        setores_validos = sorted([s for s in df_s["setor"].unique() if s in SETORES_LOCAL])
        total_pdvs_op = len(df_s)
        for setor in setores_validos:
            grp = df_s[df_s["setor"] == setor]
            alone_s = int(grp["_alone"].sum())
            total_s = len(grp)
            # Meta proporcional com folga 5% (arredondado para cima)
            if total_pdvs_op > 0 and meta_op > 0:
                meta_s = int((total_s / total_pdvs_op) * meta_op * 1.05) + 1
            else:
                meta_s = 0
            pct_s = round(alone_s / meta_s * 100, 1) if meta_s > 0 else 0
            resumo.append({
                "setor":        setor,
                "pdvs_alone":   alone_s,
                "pdvs_total":   total_s,
                "meta":         meta_s,
                "pct":          pct_s,
                "ok":           "OK" if alone_s >= meta_s else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {alone_s}/{total_s} PDVs alone | meta {meta_s} ({pct_s}%)")

        alone_op = int(df_s["_alone"].sum())
        total_op = len(df_s)
        pct_op   = round(alone_op / meta_op * 100, 1) if meta_op > 0 else 0
        resumo.append({
            "setor":        "OPERACAO",
            "pdvs_alone":   alone_op,
            "pdvs_total":   total_op,
            "meta":         meta_op,
            "pct":          pct_op,
            "ok":           "OK" if alone_op >= meta_op else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_pedido_alone_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Pedido Alone", "✅ OK",
                                 f"Operação: {alone_op}/{meta_op} PDVs alone ({pct_op}%)")
        print(f"  ✅ Pedido Alone: {alone_op}/{meta_op} PDVs alone ({pct_op}%)")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — +RGB (Item 20) ─────────────────────────────────────────────────────

def processar_rgb(conteudo_bytes, mes_ref=None):
    """
    Processa o relatório +RGB (BI exportado).
    Colunas usadas: RN, Base, Unb Pdv, Bateu Meta, Desafio RGB, Task RGB, Nome PDV
    3 abas geradas: spo_rgb_total, spo_rgb_litrinho, spo_rgb_inteira
    1 detalhe: spo_rgb_detalhe (por PDV)
    Cruza cod_pdv com pdv_base para pegar dia_visita.
    """
    import io as _io
    print("📂 Processando +RGB (SPO Item 20)...")
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Filtrar só setores locais (coluna RN)
        df["setor"] = df["RN"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        print(f"  PDVs nos setores locais: {len(df)}")

        if df.empty:
            return pd.DataFrame()

        # Extrair cod_pdv (após o _)
        df["cod_pdv"] = df["Unb Pdv"].astype(str).str.split("_").str[-1].str.strip()

        # Cruzar com pdv_base para dia_visita
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            def norm(v):
                s = str(v).strip()
                if s.endswith(".0"): s = s[:-2]
                return s
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_visita = {}

        df["dia_visita"] = df["cod_pdv"].map(mapa_visita).fillna("").astype(str)

        # Normalizar campos
        df["base"]        = df["Base"].astype(str).str.strip().str.upper()
        df["nome_pdv"]    = df["Nome PDV"].astype(str).str.strip()
        df["bateu_meta"]  = df["Bateu Meta"].astype(str).str.strip() == "1"
        df["desafio_rgb"] = df["Desafio RGB"].astype(str).str.strip() == "1"
        df["task_rgb"]    = df["Task RGB"].astype(str).str.strip() == "1"

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[["cod_pdv","nome_pdv","setor","base","dia_visita",
                     "bateu_meta","desafio_rgb","task_rgb"]].copy()
        df_det["bateu_meta"]  = df_det["bateu_meta"].map({True:"SIM", False:"NÃO"})
        df_det["desafio_rgb"] = df_det["desafio_rgb"].map({True:"SIM", False:"NÃO"})
        df_det["task_rgb"]    = df_det["task_rgb"].map({True:"SIM", False:"NÃO"})
        df_det["mes_referencia"] = mes_ref
        sobrescrever_aba("spo_rgb_detalhe", df_det)

        # ── Função auxiliar: resumo por setor ─────────────────────────────────
        def resumo_por_setor(df_filtrado, nome_aba, nome_status):
            resumo = []
            for setor in sorted(df_filtrado["setor"].unique()):
                grp = df_filtrado[df_filtrado["setor"] == setor]
                total    = len(grp)
                bateu    = int(grp["bateu_meta"].sum())
                desafio  = int(grp["desafio_rgb"].sum())
                task     = int(grp["task_rgb"].sum())
                resumo.append({
                    "setor":          setor,
                    "pdvs_total":     total,
                    "pdvs_bateu_meta":bateu,
                    "pdvs_desafio":   desafio,
                    "pdvs_task":      task,
                    "pct_meta":       round(bateu / total * 100, 1) if total > 0 else 0,
                    "mes_referencia": mes_ref,
                })
                print(f"  [{nome_status}] Setor {setor}: {bateu}/{total} bateram meta")

            total_op   = len(df_filtrado)
            bateu_op   = int(df_filtrado["bateu_meta"].sum())
            desafio_op = int(df_filtrado["desafio_rgb"].sum())
            task_op    = int(df_filtrado["task_rgb"].sum())
            resumo.append({
                "setor":           "OPERACAO",
                "pdvs_total":      total_op,
                "pdvs_bateu_meta": bateu_op,
                "pdvs_desafio":    desafio_op,
                "pdvs_task":       task_op,
                "pct_meta":        round(bateu_op / total_op * 100, 1) if total_op > 0 else 0,
                "mes_referencia":  mes_ref,
            })
            df_res = pd.DataFrame(resumo)
            sobrescrever_aba(nome_aba, df_res)
            atualizar_status_arquivo(f"SPO - +RGB {nome_status}", "✅ OK",
                                     f"Operação: {bateu_op}/{total_op} PDVs bateram meta")
            print(f"  ✅ +RGB {nome_status}: {bateu_op}/{total_op} ({round(bateu_op/total_op*100,1) if total_op>0 else 0}%)")
            return df_res

        # Total
        resumo_por_setor(df, "spo_rgb_total", "Total")
        # Litrinho
        df_lit = df[df["base"] == "LITRINHO"].copy()
        if not df_lit.empty:
            resumo_por_setor(df_lit, "spo_rgb_litrinho", "Litrinho")
        # Inteira Verde
        df_int = df[df["base"] == "INTEIRA"].copy()
        if not df_int.empty:
            resumo_por_setor(df_int, "spo_rgb_inteira", "Inteira")

        return df

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — CUPONS DIGITAIS SCORE 5 (Item 21) ─────────────────────────────────

MESES_PT = {1:"Jan",2:"Fev",3:"Mar",4:"Abr",5:"Mai",6:"Jun",
            7:"Jul",8:"Ago",9:"Set",10:"Out",11:"Nov",12:"Dez"}

def processar_cupons_digitais(conteudo_bytes, mes_ref=None):
    """
    Processa o planificador de Cupons Digitais Score 5.
    Colunas: GV, RN, PDV, Nome PDV, Bloqueio, Mês, Cupons, Campanha, Data Próxima Visita
    
    Totalizador: Bloqueio=Não + Mês=vigente → soma de cupons por setor
    Detalhe: Bloqueio=Não + sem resgate no mês vigente → PDVs pendentes
    
    Gera: spo_cupons_resumo e spo_cupons_detalhe
    """
    import io as _io
    print("📂 Processando Cupons Digitais Score 5 (SPO Item 21)...")
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Mês vigente no formato do arquivo: Mai/2026
        hoje = date.today()
        mes_vigente = f"{MESES_PT[hoje.month]}/{hoje.year}"
        mes_ref = hoje.strftime("%Y-%m")
        print(f"  Mês vigente: {mes_vigente}")

        # Filtrar só setores locais
        df["setor"] = df["RN"].astype(str).str.strip()
        df["gv"]    = df["GV"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        df["_cupons"]   = pd.to_numeric(df["Cupons"], errors="coerce").fillna(0)
        df["_bloqueio"] = df["Bloqueio"].astype(str).str.strip() == "Não"
        df["_mes"]      = df["Mês"].astype(str).str.strip()

        # Cruzar com pdv_base para dia_visita
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            def norm(v):
                s = str(v).strip()
                if s.endswith(".0"): s = s[:-2]
                return s
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_visita = {}
        df["_pdv_norm"] = df["PDV"].astype(str).str.strip().apply(norm)
        df["dia_visita"] = df["_pdv_norm"].map(mapa_visita).fillna("").astype(str)

        # ── Totalizador: Bloqueio=Não + Mês vigente ─────────────────────────
        df_tot = df[(df["_bloqueio"]) & (df["_mes"] == mes_vigente)].copy()
        resumo = []
        for setor in sorted(df_tot["setor"].unique()):
            grp = df_tot[df_tot["setor"] == setor]
            cupons = int(grp["_cupons"].sum())
            resumo.append({"setor": setor, "gv": grp["gv"].iloc[0], "cupons_mes": cupons, "mes_referencia": mes_ref})
            print(f"  Setor {setor}: {cupons} cupons no mês")

        total_op = int(df_tot["_cupons"].sum())
        resumo.append({"setor": "OPERACAO", "gv": "", "cupons_mes": total_op, "mes_referencia": mes_ref})

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_cupons_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Cupons Digitais", "✅ OK",
                                 f"Operação: {total_op} cupons em {mes_vigente}")
        print(f"  ✅ Cupons mês: {total_op} total")

        # ── Detalhe: PDV+Campanha com cupons disponíveis e SEM resgate no mês vigente
        # Identifica pares PDV+Campanha que já resgataram no mês vigente
        df["_pdv"] = df["PDV"].astype(str).str.strip()
        df["_camp"] = df["Campanha"].astype(str).str.strip()
        
        resgatados_mes = set(
            df[(df["_bloqueio"]) & (df["_mes"] == mes_vigente)]
            .apply(lambda r: (r["_pdv"], r["_camp"]), axis=1)
        )

        # Pega todos os registros desbloqueados e filtra fora os que já resgataram no mês
        df_nao = df[df["_bloqueio"]].copy()
        df_nao = df_nao[~df_nao.apply(lambda r: (r["_pdv"], r["_camp"]) in resgatados_mes, axis=1)]

        # Agrupa por PDV + Campanha, pega o registro mais recente
        df_det_rows = []
        for (pdv, camp), grp in df_nao.groupby(["_pdv", "_camp"]):
            row = grp.sort_values("Mês").iloc[-1]
            df_det_rows.append({
                "pdv":             pdv,
                "nome_pdv":        str(row.get("Nome PDV","")).strip(),
                "setor":           str(row.get("RN","")).strip(),
                "gv":              str(row.get("GV","")).strip(),
                "campanha":        camp,
                "cupons":          int(row["_cupons"]),
                "proxima_visita":  str(row.get("Data Próxima Visita","")).split(" ")[0],
                "ultimo_resgate":  str(row.get("Data Último Resgate","")).split(" ")[0],
                "dia_visita":      str(row.get("dia_visita","")).strip(),
                "mes_referencia":  mes_ref,
            })

        df_detalhe = pd.DataFrame(df_det_rows) if df_det_rows else pd.DataFrame()
        if not df_detalhe.empty:
            sobrescrever_aba("spo_cupons_detalhe", df_detalhe)
            print(f"  ✅ Detalhe: {len(df_detalhe)} registros pendentes")
        else:
            print("  ⚠️ Nenhum PDV pendente")

        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — % LOJAS IDEAIS VIZINHANÇA (Item 22) ───────────────────────────────

META_LOJA_IDEAL = 40  # % — placeholder

def processar_loja_ideal(conteudo_bytes, mes_ref=None):
    """
    Processa o Planificador de Loja Ideal Vizinhança.
    Loja Ideal = Pontuação Total >= 60.
    Setores: 101, 102, 103 (OFF Trade / GV1).
    Cruza Cód. PDV com pdv_base para nome real.
    Gera: spo_loja_ideal_resumo e spo_loja_ideal_detalhe
    """
    import io as _io
    print("📂 Processando Loja Ideal Vizinhança (SPO Item 22)...")
    SETORES_LOCAL = {"101","102","103"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]
        df["setor"] = df["Setor Rn"].astype(str).str.strip()
        df["gv"]    = df["Setor Gv"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        print(f"  PDVs nos setores Vizinhança: {len(df)}")

        if df.empty:
            return pd.DataFrame()

        # Pontuações
        df["_pts_total"]     = pd.to_numeric(df["Pontuação Total"], errors="coerce").fillna(0)
        df["_pts_sort"]      = pd.to_numeric(df["[Sortimento] Pontos"], errors="coerce").fillna(0)
        df["_pts_exec"]      = pd.to_numeric(df["[Execução] Pontos"], errors="coerce").fillna(0)
        df["_pts_desafio"]   = pd.to_numeric(df["[Desafio] Pontos"], errors="coerce").fillna(0)
        df["_loja_ideal"]    = df["_pts_total"] >= 60
        df["_cod_pdv"]       = df["Cód. PDV"].astype(str).str.strip()

        # Cruzar com pdv_base para nome real
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            def norm(v):
                s = str(v).strip()
                if s.endswith(".0"): s = s[:-2]
                return s
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            col_nome = "nome_fantasia" if "nome_fantasia" in df_base.columns else "nome_pdv"
            mapa_nome = df_base.set_index("_cod")[col_nome].to_dict() if col_nome in df_base.columns else {}
            mapa_dia  = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_nome = mapa_dia = {}

        df["nome_pdv"]   = df["_cod_pdv"].apply(norm).map(mapa_nome).fillna(df["Nome do Pdv"]).astype(str)
        df["dia_visita"] = df["_cod_pdv"].apply(norm).map(mapa_dia).fillna("").astype(str)

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[["_cod_pdv","nome_pdv","setor","gv","dia_visita",
                     "_pts_total","_pts_sort","_pts_exec","_pts_desafio","_loja_ideal"]].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","gv","dia_visita",
                          "pts_total","pts_sortimento","pts_execucao","pts_desafio","loja_ideal"]
        df_det["loja_ideal"]    = df_det["loja_ideal"].map({True:"SIM", False:"NÃO"})
        df_det["mes_referencia"] = mes_ref
        sobrescrever_aba("spo_loja_ideal_detalhe", df_det)

        # ── Resumo por setor ─────────────────────────────────────────────────
        resumo = []
        for setor in sorted(df["setor"].unique()):
            grp = df[df["setor"] == setor]
            total  = len(grp)
            ideais = int(grp["_loja_ideal"].sum())
            pct    = round(ideais / total * 100, 1) if total > 0 else 0
            resumo.append({
                "setor":          setor,
                "gv":             grp["gv"].iloc[0],
                "pdvs_total":     total,
                "pdvs_ideais":    ideais,
                "pct":            pct,
                "meta":           META_LOJA_IDEAL,
                "ok":             "OK" if pct >= META_LOJA_IDEAL else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  Setor {setor}: {ideais}/{total} Lojas Ideais ({pct}%)")

        total_op  = len(df)
        ideais_op = int(df["_loja_ideal"].sum())
        pct_op    = round(ideais_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({
            "setor":          "OPERACAO",
            "gv":             "1",
            "pdvs_total":     total_op,
            "pdvs_ideais":    ideais_op,
            "pct":            pct_op,
            "meta":           META_LOJA_IDEAL,
            "ok":             "OK" if pct_op >= META_LOJA_IDEAL else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_loja_ideal_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Loja Ideal Vizinhança", "✅ OK",
                                 f"Operação: {pct_op}% ({ideais_op}/{total_op} Lojas Ideais)")
        print(f"  ✅ Loja Ideal: {pct_op}% ({ideais_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — EXPANSÃO SCANNTECH (Item 23) ──────────────────────────────────────

META_SCANNTECH = 20  # placeholder — PDVs ativos

def processar_scanntech(conteudo_bytes, mes_ref=None):
    """
    Processa a base de Expansão Scanntech.
    Ativo = Descrição Ambev começa com "ATIVO".
    Cruza Cód. PDV com pdv_base para setor e dia_visita.
    Gera: spo_scanntech_resumo e spo_scanntech_detalhe
    """
    import io as _io
    print("📂 Processando Expansão Scanntech (SPO Item 23)...")

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]
        print(f"  Total PDVs na base: {len(df)}")

        # Status: ativo = começa com "ATIVO"
        df["_status"]  = df["Descrição Ambev"].astype(str).str.strip()
        df["_ativo"]   = df["_status"].str.upper().str.startswith("ATIVO")
        df["_cod_pdv"] = df["Cód. PDV"].astype(str).str.strip()
        df["_gv"]      = df["Cód. GV"].astype(str).str.strip()
        df["_nome"]    = df["Nome PDV"].astype(str).str.strip()

        # Cruzar com pdv_base para setor e dia_visita
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            def norm(v):
                s = str(v).strip()
                if s.endswith(".0"): s = s[:-2]
                return s
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            mapa_setor  = df_base.set_index("_cod")["setor"].to_dict()
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_setor = mapa_visita = {}

        df["setor"]      = df["_cod_pdv"].apply(norm if df_base.empty == False else str).map(mapa_setor).fillna("").astype(str)
        df["dia_visita"] = df["_cod_pdv"].apply(norm if df_base.empty == False else str).map(mapa_visita).fillna("").astype(str)

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[["_cod_pdv","_nome","setor","_gv","dia_visita","_status","_ativo"]].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","gv","dia_visita","status_scanntech","is_ativo"]
        df_det["is_ativo"] = df_det["is_ativo"].map({True:"SIM", False:"NÃO"})
        df_det["mes_referencia"] = mes_ref
        # Remover linhas com GV vazio/nulo/nan
        df_det = df_det[df_det["gv"].astype(str).str.strip().str.lower().isin(["", "nan", "none"]) == False]
        sobrescrever_aba("spo_scanntech_detalhe", df_det)

        # ── Resumo por GV ─────────────────────────────────────────────────────
        # Filtrar GVs válidos (sem nan/vazio) antes de agrupar
        gvs_validos = [g for g in df["_gv"].unique() if str(g).strip().lower() not in ("nan", "none", "")]
        resumo = []
        for gv in sorted(gvs_validos):
            grp = df[df["_gv"] == gv]
            total  = len(grp)
            ativos = int(grp["_ativo"].sum())
            resumo.append({
                "gv":             gv,
                "setor":          f"GV{gv}",
                "pdvs_total":     total,
                "pdvs_ativos":    ativos,
                "mes_referencia": mes_ref,
            })
            print(f"  GV{gv}: {ativos}/{total} ativos")

        # Total operação
        total_op  = len(df)
        ativos_op = int(df["_ativo"].sum())
        resumo.append({
            "gv":             "",
            "setor":          "OPERACAO",
            "pdvs_total":     total_op,
            "pdvs_ativos":    ativos_op,
            "meta":           META_SCANNTECH,
            "ok":             "OK" if ativos_op >= META_SCANNTECH else "NOK",
            "mes_referencia": mes_ref,
        })

        # Distribuição de status
        status_counts = df["_status"].value_counts().to_dict()
        print(f"  Status: {status_counts}")

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_scanntech_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Expansão Scanntech", "✅ OK",
                                 f"Operação: {ativos_op}/{total_op} PDVs ativos")
        print(f"  ✅ Scanntech: {ativos_op}/{total_op} ativos")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — PORTFÓLIO IDEAL SCORE 5 (Item 24) ─────────────────────────────────

META_PORTFOLIO_IDEAL = 46  # % placeholder

MAPA_CATEGORIA_REAL = {
    "600":      {"HE": ["SPT 600","STL 600","STL PG 600","BUD 600","COR 600","ORI 600","OUTROS 600"],
                 "CORE": ["AP 600","BC 600","BUD 600_dup1","ORI 600_dup1","SK 600","SPT 600_dup1","STL 600_dup1","STL PG 600_dup1","OUTROS 600_dup1"]},
    "LN":       {"HE": ["BUD LN","COR LN","STL LN","STL PG LN","SPT LN","MIC LN","OUTROS LN"], "CORE": []},
    "LN ZERO":  {"HE": ["BUD LN ZERO","COR LN ZERO"], "CORE": []},
    "LITRINHO": {"HE": [], "CORE": ["AP 1000","BC 1000","BUD 1000","ORI 1000","SK 1000","OUTROS 1000"]},
    "INTEIRA":  {"HE": [], "CORE": ["AP 300","BC 300","BUD 300","ORI 300","SK 300","OUTROS 300"]},
    "LITRO":    {"HE": [], "CORE": ["AP 1000","BC 1000","BUD 1000","ORI 1000","SK 1000","OUTROS 1000"]},
    "RGB":      {"HE": [], "CORE": ["AP 600","BC 600","BUD 600_dup1","ORI 600_dup1","SK 600","SPT 600_dup1","STL 600_dup1","STL PG 600_dup1","OUTROS 600_dup1"]},
}

COLS_META_PI = ["600","LN","LN ZERO","LITRINHO","INTEIRA","LITRO","RGB"]

def processar_portfolio_ideal(conteudo_bytes, mes_ref=None):
    """
    Processa o ON_TRADE para Portfólio Ideal Score 5.
    META: colunas L-R | REAL HE: T-AI | REAL CORE: AJ-BD | Col S: descartar
    Portfólio Ideal = PDV que atingiu TODAS as categorias da sua meta (BATEU META==1).
    Detalhe: mostra categorias faltantes por PDV.
    """
    import io as _io
    print("📂 Processando Portfólio Ideal Score 5 (SPO Item 24)...")
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        # Renomear colunas duplicadas (ex: SPT 600 aparece em HE e CORE)
        seen = {}
        new_cols = []
        for c in df.columns:
            cs = c.strip()
            if cs in seen:
                seen[cs] += 1
                new_cols.append(f"{cs}_dup{seen[cs]}")
            else:
                seen[cs] = 0
                new_cols.append(cs)
        df.columns = new_cols

        df["setor"] = df["RN"].astype(str).str.strip()
        df["gv"]    = df["GV"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        print(f"  PDVs nos setores locais: {len(df)}")

        # Cruzar com pdv_base para dia_visita
        df_base = ler_aba("pdv_base")
        if not df_base.empty:
            def norm(v):
                s = str(v).strip()
                if s.endswith(".0"): s = s[:-2]
                return s
            df_base["_cod"] = df_base["cod_pdv"].apply(norm)
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict() if "dia_visita" in df_base.columns else {}
        else:
            mapa_visita = {}
            def norm(v): return str(v).strip()

        df["_cod_pdv"] = df["CHAVE PDV"].astype(str).str.split("_").str[-1].str.strip()
        df["dia_visita"] = df["_cod_pdv"].apply(norm).map(mapa_visita).fillna("").astype(str)

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")

        def _to_int(v):
            try:
                s = str(v).strip()
                if s in ("","nan","None","NaN"): return 0
                return int(float(s))
            except: return 0

        def categorias_faltantes(row):
            base = str(row.get("BASE","")).strip().upper()
            seg  = "HE" if "HIGH END" in base else "CORE"
            faltando = []
            for cat in COLS_META_PI:
                val_meta = str(row.get(cat,"")).strip()
                if val_meta in ("","nan","0","None","NaN") or not val_meta:
                    continue
                cols_real = [c for c in MAPA_CATEGORIA_REAL.get(cat,{}).get(seg,[]) if c in row.index]
                if not cols_real:
                    continue
                total_real = sum(_to_int(row.get(c,0)) for c in cols_real)
                if total_real == 0:
                    faltando.append(cat)
            return faltando

        df["_faltantes"]     = df.apply(categorias_faltantes, axis=1)
        df["_bateu"]         = df["BATEU META"].astype(str).str.strip() == "1"
        df["_faltantes_str"] = df["_faltantes"].apply(lambda x: ", ".join(x) if x else "—")

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[["_cod_pdv","NOME PDV","setor","gv","BASE","VISITA",
                     "dia_visita","_bateu","_faltantes_str"]].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","gv","base","dia_rota",
                          "dia_visita","portfolio_ideal","itens_faltantes"]
        df_det["portfolio_ideal"] = df_det["portfolio_ideal"].map({True:"SIM", False:"NÃO"})
        df_det["mes_referencia"]  = mes_ref
        sobrescrever_aba("spo_portfolio_ideal_detalhe", df_det)

        # ── Resumo por setor ─────────────────────────────────────────────────
        resumo = []
        for setor in sorted(df["setor"].unique()):
            grp = df[df["setor"] == setor]
            total  = len(grp)
            ideais = int(grp["_bateu"].sum())
            pct    = round(ideais / total * 100, 1) if total > 0 else 0
            resumo.append({"setor": setor, "gv": grp["gv"].iloc[0],
                           "pdvs_total": total, "pdvs_ideais": ideais,
                           "pct": pct, "ok": "OK" if pct >= META_PORTFOLIO_IDEAL else "NOK",
                           "mes_referencia": mes_ref})
            print(f"  Setor {setor}: {ideais}/{total} ({pct}%)")

        total_op  = len(df)
        ideais_op = int(df["_bateu"].sum())
        pct_op    = round(ideais_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({"setor": "OPERACAO", "gv": "",
                       "pdvs_total": total_op, "pdvs_ideais": ideais_op,
                       "pct": pct_op, "meta": META_PORTFOLIO_IDEAL,
                       "ok": "OK" if pct_op >= META_PORTFOLIO_IDEAL else "NOK",
                       "mes_referencia": mes_ref})

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_portfolio_ideal_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Portfólio Ideal Score 5", "✅ OK",
                                 f"Operação: {pct_op}% ({ideais_op}/{total_op} PDVs)")
        print(f"  ✅ Portfólio Ideal: {pct_op}% ({ideais_op}/{total_op})")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


# ─── SPO — ATENDIMENTO PRODUTIVO (Item 5) ────────────────────────────────────

META_AP = 0.90  # 90% dos RNs com AP OK

def processar_atendimento_produtivo(conteudo_bytes, mes_ref=None):
    """
    Processa o relatório de Atendimento Produtivo (BI exportado).
    Colunas principais:
      Cod Setor = RN/Setor
      Cod Gv    = GV
      Segmento  = Off_Independente / On_Trade
      KPIs OK   = quantos dos 4 pilares o RN bateu (0-4)
      AP OK     = Sim/Não — RN com Atendimento Produtivo
      Meta/Real/GAP       = Positivação Tasks Compra
      Meta.1/Real.1/GAP.1 = Carteira Ideal (Compradores)
      Meta.2/Real.2/GAP.2 = GPS
      Meta.3/Real.3/GAP.3 = Rota Efetiva
    Gera: spo_ap_resumo e spo_ap_detalhe
    """
    import io as _io
    print("📂 Processando Atendimento Produtivo (SPO Item 5)...")
    SETORES_LOCAL = {"101","102","103","104","105","106","301","302","303","304","305"}

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]
        df["setor"] = df["Cod Setor"].astype(str).str.strip()
        df["gv"]    = df["Cod Gv"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        print(f"  RNs nos setores locais: {len(df)}")

        def pct(v):
            try: return round(float(v) * 100, 1)
            except: return None

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data")

        # ── Detalhe por RN ────────────────────────────────────────────────────
        df_det = []
        for _, row in df.iterrows():
            df_det.append({
                "setor":           row["setor"],
                "gv":              row["gv"],
                "segmento":        str(row.get("Segmento","")).strip(),
                "ap_ok":           str(row.get("AP OK","")).strip(),
                "kpis_ok":         str(row.get("KPIs OK","")).strip(),
                # Positivação Tasks Compra
                "positiv_meta":    pct(row.get("Meta","")),
                "positiv_real":    pct(row.get("Real","")),
                "positiv_gap":     pct(row.get("GAP","")),
                # Carteira Ideal
                "carteira_meta":   str(row.get("Meta.1","")).strip(),
                "carteira_real":   str(row.get("Real.1","")).strip(),
                "carteira_gap":    str(row.get("GAP.1","")).strip(),
                # GPS
                "gps_meta":        pct(row.get("Meta.2","")),
                "gps_real":        pct(row.get("Real.2","")),
                "gps_gap":         pct(row.get("GAP.2","")),
                # Rota Efetiva
                "rota_meta":       pct(row.get("Meta.3","")),
                "rota_real":       pct(row.get("Real.3","")),
                "rota_gap":        pct(row.get("GAP.3","")),
                "mes_referencia":  mes_ref,
            })

        df_detalhe = pd.DataFrame(df_det)
        sobrescrever_aba("spo_ap_detalhe", df_detalhe)

        # ── rv_ap — popula aba usada pelo cálculo de RV ───────────────────────
        rv_ap_rows = []
        for _, row in df.iterrows():
            ap_ok_val = "OK" if str(row.get("AP OK", "")).strip().upper() == "SIM" else "NOK"
            rv_ap_rows.append({
                "setor":               row["setor"],
                "mes_referencia":      mes_ref,
                "tasks_compra_real":   pct(row.get("Real", "")),
                "tasks_compra_meta":   pct(row.get("Meta", "")),
                "compradores_real":    str(row.get("Real.1", "")).strip(),
                "compradores_meta":    str(row.get("Meta.1", "")).strip(),
                "rota_efetiva_real":   pct(row.get("Real.3", "")),
                "rota_efetiva_meta":   pct(row.get("Meta.3", "")),
                "gps_real":            pct(row.get("Real.2", "")),
                "gps_meta":            pct(row.get("Meta.2", "")),
                "ap_ok":               ap_ok_val,
            })
        df_rv_ap = pd.DataFrame(rv_ap_rows)
        sobrescrever_aba("rv_ap", df_rv_ap)
        print(f"  ✅ rv_ap atualizada: {len(df_rv_ap)} setores")

        # ── Resumo por GV e operação ──────────────────────────────────────────
        total_op = len(df)
        ap_ok_op = int((df["AP OK"].astype(str).str.strip().str.upper() == "SIM").sum())
        pct_op   = round(ap_ok_op / total_op * 100, 1) if total_op > 0 else 0

        resumo = []
        for gv in sorted(df["gv"].unique()):
            grp = df[df["gv"] == gv]
            total = len(grp)
            ap_ok = int((grp["AP OK"].astype(str).str.strip().str.upper() == "SIM").sum())
            pct_gv = round(ap_ok / total * 100, 1) if total > 0 else 0
            resumo.append({
                "gv":             gv,
                "setor":          f"GV{gv}",
                "rns_total":      total,
                "rns_ap_ok":      ap_ok,
                "pct":            pct_gv,
                "ok":             "OK" if pct_gv >= META_AP * 100 else "NOK",
                "mes_referencia": mes_ref,
            })
            print(f"  GV{gv}: {ap_ok}/{total} RNs com AP OK ({pct_gv}%)")

        resumo.append({
            "gv":             "",
            "setor":          "OPERACAO",
            "rns_total":      total_op,
            "rns_ap_ok":      ap_ok_op,
            "pct":            pct_op,
            "meta":           round(META_AP * 100, 1),
            "ok":             "OK" if pct_op >= META_AP * 100 else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        sobrescrever_aba("spo_ap_resumo", df_resumo)
        atualizar_status_arquivo("SPO - Atendimento Produtivo", "✅ OK",
                                 f"Operação: {pct_op}% ({ap_ok_op}/{total_op} RNs)")
        print(f"  ✅ AP: {pct_op}% ({ap_ok_op}/{total_op} RNs com AP OK)")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()
