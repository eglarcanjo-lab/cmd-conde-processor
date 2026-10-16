"""Processador HOP — RV — volume por categoria, faturamento marketplace, pontos BEES e cálculo completo.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403



def processar_volume_rv(df_pedidos, mes_ref=None):
    """
    Extrai volume por setor x categoria x MÊS (mês real de cada linha, via _data) para
    o cálculo de RV. Acumula por mês: substitui só os meses presentes no arquivo —
    importar junho em julho grava junho sem apagar julho. (mes_ref é ignorado; mantido
    na assinatura por compatibilidade.)
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

    # VETORIZADO (memória/CPU no Render free): explode as categorias em vez de
    # iterar linha a linha o arquivo inteiro.
    m = (df_pedidos["_volume"] > 0) & (df_pedidos["_data"].notna())
    d = pd.DataFrame({
        "setor": df_pedidos.loc[m, "_setor"].values,
        "mes_ref": df_pedidos.loc[m, "_data"].dt.strftime("%Y-%m").values,  # mês REAL da linha
        "volume": df_pedidos.loc[m, "_volume"].values,
        "categoria": df_pedidos.loc[m, "_categorias"].values,
    }).explode("categoria")
    d = d[d["categoria"].isin(cats_rv)]
    if d.empty:
        return

    resultado = (
        d.groupby(["setor", "categoria", "mes_ref"])["volume"]
        .sum()
        .reset_index()
    )
    resultado["volume"] = resultado["volume"].round(2)
    # Acumula por mês: substitui só os meses do arquivo, mantém os demais.
    sobrescrever_por_mes("rv_volume", resultado, "mes_ref")
    print(f"  ✅ Volume RV: {len(resultado)} linhas · meses {sorted(resultado['mes_ref'].unique())}")



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

SEGMENTO_ON  = {"104", "105", "106", "107", "108", "109", "110", "111",
                "301", "302", "303", "304", "305"}  # On Trade: Cerveja+NAB+Mktp (107–111 = ex-301–305)


# Pesos oficiais do regulamento de RV (% do PO). Pontos Force = 50% (Meios);
# os 50% restantes (Resultados) se dividem conforme o segmento.
# FIXOS no código — substituem o campo "peso" da planilha de metas (que vem corrompido).
PESOS_RV = {
    "OFF": {"pontos": 50, "cerveja": 25, "nab": 12.5, "marketplace": 7.5, "match": 5},
    "ON":  {"pontos": 50, "cerveja": 25, "nab": 15.0, "marketplace": 10.0, "match": 0},
}



def processar_faturamento_mktp(conteudo_bytes, mes_ref=None):
    """
    Processa o arquivo 030509 (GMV Marketplace por setor).
    mes_ref (YYYY-MM, opcional): mês de referência vindo da UI — permite importar meses
    anteriores. Sem ele, usa o mês corrente. Acumula por mês (substitui só o mês importado).
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
    _mes = str(mes_ref).strip() if mes_ref else date.today().strftime("%Y-%m")
    # De out/2026 em diante o Faturamento Mktp vem do CORA. O 030509 não tem data (o mês vem
    # do "mês de referência" da tela) — sem esta trava, importar esquecendo o mês apagaria o
    # mês corrente do CORA. Só aceita meses ANTERIORES à virada.
    _VIRADA_CORA = "2026-10"
    if _mes >= _VIRADA_CORA:
        raise ValueError(f"030509 é só para meses do Promax (antes de {_VIRADA_CORA}); o mês {_mes} vem do CORA. "
                         "Escolha o mês de referência (ex.: 2026-09) antes de importar.")
    resultado["mes_referencia"] = _mes
    resultado["faturamento_mktp_real"] = resultado["faturamento_mktp_real"].round(2)

    # Acumula por mês: substitui só o mês importado, mantém os demais.
    sobrescrever_por_mes("rv_faturamento_mktp", resultado, "mes_referencia")
    atualizar_status_arquivo("030509 (Faturamento Mktp)", "✅ OK", f"{len(resultado)} setores · mês {_mes}")
    print(f"  ✅ Faturamento Mktp: {len(resultado)} setores · mês {_mes}")
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

    # Acumula por mês (o mês vem do próprio arquivo): junho e julho convivem.
    sobrescrever_por_mes("rv_pontos_bees", resultado, "mes_referencia")
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



def _mes_rv_fechado(mes):
    """True se a RV do mês foi fechada no app (tabela rv_fechamentos, criada pelo backend)."""
    try:
        df = ler_aba("rv_fechamentos")
        return (not df.empty) and "mes" in df.columns and str(mes) in set(df["mes"].astype(str).str.strip())
    except Exception:
        return False



def calcular_rv_completa(mes_ref=None):
    """
    Calcula a RV para todos os RNs com base nos dados já processados.
    mes_ref (YYYY-MM, opcional): calcula a RV DAQUELE mês (fontes filtradas pelo mês).
    Sem ele, usa o mês corrente. Grava em rv_resultado ACUMULANDO por mês
    (substitui só o mês calculado — o histórico dos outros meses fica).
    """
    _mes_rv = str(mes_ref).strip() if mes_ref else date.today().strftime("%Y-%m")
    # RV FECHADA (congelada no app): não recalcula — o que foi pago não muda.
    if _mes_rv_fechado(_mes_rv):
        print(f"🔒 RV de {_mes_rv} está FECHADA — recálculo ignorado.")
        return pd.DataFrame()
    print(f"📊 Calculando RV completa · mês {_mes_rv}...")

    # Linhas sem mês contam como o mês-alvo (compatibilidade com dados antigos).
    def _do_mes(row, col="mes_referencia"):
        _m = str(row.get(col, "") or "").strip()
        return (not _m) or _m == _mes_rv

    # Carrega todas as fontes
    df_metas     = ler_aba("metas")
    # (cobertura e rank_clientes não são usados no cálculo — leituras removidas)
    df_pontos    = ler_aba("rv_pontos_bees")
    df_mktp      = ler_aba("rv_faturamento_mktp")
    df_ap        = ler_aba("rv_ap")

    # Mapa de metas por setor/categoria (só do mês-alvo)
    metas_map = {}
    po_total_map = {}
    for _, row in df_metas.iterrows():
        if not _do_mes(row):
            continue
        setor = setor_hop(row.get("setor", ""))
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

    # Setor sempre pelo de-para (setor_hop: 301–305 → 107–111), igual às metas acima. Antes
    # pontos/Mktp/AP usavam o código cru: no recálculo de set/26 (gravado como 301–305 no
    # Promax) os RNs 107–111 ficavam com Mktp/pontos/AP ZERADOS.
    # Mapa de pontos Bees (só do mês-alvo)
    pontos_map = {}
    for _, row in df_pontos.iterrows():
        if not _do_mes(row):
            continue
        s = setor_hop(row.get("setor", ""))
        v = float(row.get("pontos_real", 0) or 0)
        if v or s not in pontos_map:          # código antigo e novo no mesmo mês: fica o não-zerado
            pontos_map[s] = v

    # Mapa de faturamento Mktp (só do mês-alvo) — soma (antigo + novo, se houver os dois)
    mktp_map = {}
    for _, row in df_mktp.iterrows():
        if not _do_mes(row):
            continue
        s = setor_hop(row.get("setor", ""))
        mktp_map[s] = mktp_map.get(s, 0.0) + float(row.get("faturamento_mktp_real", 0) or 0)

    # Mapa AP (atendimento produtivo — só do mês-alvo)
    ap_map = {}
    for _, row in df_ap.iterrows():
        if not _do_mes(row):
            continue
        s = setor_hop(row.get("setor", ""))
        v = str(row.get("ap_ok", "NOK")).strip().upper()
        if v == "OK" or s not in ap_map:
            ap_map[s] = v

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
        if not _do_mes(row, col="mes_ref"):
            continue
        setor = setor_hop(row.get("setor", ""))
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
    # Códigos antigos (301–305) já foram somados nos novos (107–111) pelo de-para — não
    # gera linha zerada para eles.
    todos_setores = [s for s in (SEGMENTO_OFF | SEGMENTO_ON) if s not in DE_PARA_SETOR]

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
        # AP não bloqueia mais a RV — o Pontos Force paga independente do Atendimento
        # Produtivo (ap_ok segue gravado em rv_resultado apenas como informativo).
        rv_pontos   = (po_total * peso_pontos / 100) * (pct_pontos / 100)

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
            "mes_referencia": _mes_rv,
        })

    df_resultado = pd.DataFrame(resultados)
    # Acumula por mês: substitui só o mês calculado, preserva o histórico dos demais.
    sobrescrever_por_mes("rv_resultado", df_resultado, "mes_referencia")
    print(f"  ✅ RV calculada: {len(df_resultado)} setores · mês {_mes_rv}")
    return df_resultado


__all__ = [n for n in dir() if not n.startswith("__")]
