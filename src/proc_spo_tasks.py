"""Processador HOP — SPO — tasks volume/marketplace/match/zero, orquestrador, SKU/PDV, +LN e política/menu com df.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403
from proc_spo_visitas import TASKS_TTC, _tem_planificador



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
    SETORES_LOCAL = SETORES_VALIDOS

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

        df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS

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

        df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS
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
        df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS
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

        df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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
        ("Tasks SKU/PDV TT",    lambda: _calcular_sku_pdv_com_df(df_tasks, mes_ref=_mr)),
        # +LN saiu daqui: agora vem de um arquivo dedicado (processar_ln / campo spo_ln).
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
    SETORES_LOCAL = SETORES_VALIDOS

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

    df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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



def _sem_acento(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c)).lower().strip()



# Texto da task SKU/PDV TT: "... N SKUs distintos de {CERVEJA|NAB|MATCH|MARKETPLACE}".
# Cestas (600mL, Long Neck, RGB...) não casam porque exigem a categoria logo após "de".
_RE_SKU_PDV = r"skus?\s+distintos\s+de\s+(cervejas?|nab|match|marketplace)\b"



def _calcular_sku_pdv_com_df(df_tasks, mes_ref=None):
    """Tarefas de SKU/PDV TT (KPI 26 / ord 13). Doc: tasks com o texto
    "SKUs distintos de {CERVEJA, NAB, MATCH, MARKETPLACE}" nos clusters
    "Desenvolvimento de Portfólio" e "Marketplace". Realizado = Σ tasks VALID na
    operação. Tri = acumulado (soma meta/real dos 3 meses, via spo_metas).
    Gera spo_tasks_sku_pdv_resumo (por mês, com quebra por categoria) e _detalhe."""
    SETORES_LOCAL = SETORES_VALIDOS
    CLUSTERS = {"desenvolvimento de portfolio", "marketplace"}
    META = 60
    status_nome = "SPO - Tasks SKU/PDV TT"

    clus = df_tasks["cluster_primario"].apply(_sem_acento)
    cat = df_tasks["descricao"].apply(_sem_acento).str.extract(_RE_SKU_PDV, expand=False)
    df = df_tasks[clus.isin(CLUSTERS) & cat.notna()].copy()
    df["_cat"] = cat[df.index].str.rstrip("s").str.upper()   # CERVEJAS → CERVEJA
    df["_setor"] = df["setor"].astype(str).str.strip().map(setor_hop)
    df = df[df["_setor"].isin(SETORES_LOCAL)]
    print(f"  [{status_nome}] {len(df)} tasks encontradas")
    if df.empty:
        return
    df["_valida"] = df["status"].astype(str).str.strip().str.upper() == "VALID"
    mes_ref = mes_ref or _mes_ref_do_dado(df, "mes_ano", "data_visita")

    CATS = ["CERVEJA", "NAB", "MATCH", "MARKETPLACE"]
    def _linha(setor, grp):
        total = len(grp); validas = int(grp["_valida"].sum())
        pct = round(validas / total * 100, 1) if total > 0 else 0
        linha = {"setor": setor, "tasks_total": total, "tasks_validas": validas,
                 "pct": pct, "ok": "OK" if pct >= META else "NOK"}
        for c in CATS:
            g = grp[grp["_cat"] == c]
            linha[f"{c.lower()}_total"] = len(g)
            linha[f"{c.lower()}_validas"] = int(g["_valida"].sum())
        linha["mes_referencia"] = mes_ref
        return linha

    resumo = [_linha(s, df[df["_setor"] == s]) for s in sorted(df["_setor"].unique())]
    op = _linha("OPERACAO", df)
    resumo.append(op)
    sobrescrever_por_mes("spo_tasks_sku_pdv_resumo", pd.DataFrame(resumo), "mes_referencia")

    col = lambda c: df[c].astype(str).str.strip() if c in df.columns else ""
    det = pd.DataFrame({
        "setor": df["_setor"], "gv": col("gv"), "cod_pdv": col("cod_pdv"),
        "categoria": df["_cat"], "descricao": col("descricao"),
        "qtd_solicitada": col("qtd_solicitada"), "qtd_comprada": col("qtd_comprada"),
        "status": col("status").str.upper(), "data_visita": col("data_visita"),
        "mes_referencia": mes_ref,
    }).sort_values(["setor", "status", "categoria"])
    sobrescrever_aba("spo_tasks_sku_pdv_detalhe", det)

    atualizar_status_arquivo(status_nome, "✅ OK",
        f"Operação: {op['tasks_validas']} validadas de {op['tasks_total']} ({op['pct']}%)")
    print(f"    ✅ SKU/PDV TT ({mes_ref}): {op['tasks_validas']}/{op['tasks_total']} validadas")



_MES_PT_REV = {"jan":1,"fev":2,"mar":3,"abr":4,"mai":5,"jun":6,"jul":7,"ago":8,"set":9,"out":10,"nov":11,"dez":12}

def _mes_pt_para_ym(serie):
    """Converte 'Set/2026' / 'Set/26' → '2026-09' (pega o 1º valor válido da série)."""
    for v in serie.dropna().astype(str):
        s = v.strip().lower().replace(" ", "")
        if "/" not in s: continue
        a, b = s.split("/")[:2]
        m = _MES_PT_REV.get(a[:3]); y = b.strip()
        if m and y.isdigit():
            if len(y) == 2: y = "20" + y
            return f"{y}-{m:02d}"
    return None



def processar_ln(conteudo_bytes, mes_ref=None):
    """+LN (KPI 27) — base DEDICADA (relatório +LN do BI, aba Export). Cada linha = 1 PDV
    com Meta/Real de SKUs distintos de Long Neck HE e a coluna 'Bateu' (1=OK / 0=NOK).
    Realizado = nº de PDVs que bateram. Detalhe traz Gap e o status por produto
    (STE, STE PG, COR, CORZ, SPT, MIC). Acumula por mês. Gera spo_tasks_ln_resumo/detalhe."""
    import io as _io
    print("📂 Processando +LN (SPO Item 27) — base dedicada...")
    SETORES_LOCAL = SETORES_VALIDOS
    META = 60
    try:
        try: df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception: df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)
        df.columns = [c.strip() for c in df.columns]

        mes_ref = mes_ref or _mes_pt_para_ym(df.get("Mes/Ano", pd.Series(dtype=str))) or hoje_brasilia().strftime("%Y-%m")

        df["setor"] = df["RN"].astype(str).str.strip().map(setor_hop)
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        if df.empty:
            print("  ⚠️ +LN: nenhuma linha nos setores locais"); return
        df["_bateu"] = pd.to_numeric(df["Bateu"], errors="coerce").fillna(0) >= 1
        # cod_pdv = parte após o "_" em 'Unb Pdv' (1035185_20017 → 20017)
        df["_cod"] = df["Unb Pdv"].astype(str).str.strip().apply(lambda s: s.split("_")[-1] if "_" in s else s)

        # dia_visita via pdv_base
        df_base = ler_aba("pdv_base"); mapa_visita = {}
        if not df_base.empty and "dia_visita" in df_base.columns:
            def _n(v):
                s = str(v).strip()
                return s[:-2] if s.endswith(".0") else s
            df_base["_cod"] = df_base["cod_pdv"].apply(_n)
            mapa_visita = df_base.set_index("_cod")["dia_visita"].to_dict()
        df["dia_visita"] = df["_cod"].map(mapa_visita).fillna("").astype(str)

        # ── Resumo por setor + OPERAÇÃO (realizado = PDVs que bateram) ──
        resumo = []
        for setor in sorted(df["setor"].unique()):
            grp = df[df["setor"] == setor]
            total = len(grp); validas = int(grp["_bateu"].sum())
            pct = round(validas / total * 100, 1) if total > 0 else 0
            resumo.append({"setor": setor, "tasks_total": total, "tasks_validas": validas,
                           "pct": pct, "ok": "OK" if pct >= META else "NOK", "mes_referencia": mes_ref})
        total_op = len(df); validas_op = int(df["_bateu"].sum())
        pct_op = round(validas_op / total_op * 100, 1) if total_op > 0 else 0
        resumo.append({"setor": "OPERACAO", "tasks_total": total_op, "tasks_validas": validas_op,
                       "pct": pct_op, "ok": "OK" if pct_op >= META else "NOK", "mes_referencia": mes_ref})
        sobrescrever_por_mes("spo_tasks_ln_resumo", pd.DataFrame(resumo), "mes_referencia")

        # ── Detalhe por PDV com Gap + flag por produto LN ──
        prod = {"STE": "ste", "STE PG": "ste_pg", "COR": "cor", "CORZ": "corz", "SPT": "spt", "MIC": "mic"}
        det = pd.DataFrame({
            "setor": df["setor"], "cod_pdv": df["_cod"],
            "nome_pdv": df["Nome PDV"].astype(str).str.strip(),
            "dia_visita": df["dia_visita"],
            "meta": df["Meta"].astype(str).str.strip(),
            "real": df["Real"].astype(str).str.strip(),
            "bateu": df["_bateu"].map({True: "OK", False: "NOK"}),
            "gap": df["Gaps SKUs"].astype(str).str.strip() if "Gaps SKUs" in df.columns else "",
        })
        for src, dst in prod.items():
            det[dst] = df[src].astype(str).str.strip() if src in df.columns else ""
        det["mes_referencia"] = mes_ref
        det = det.sort_values(["setor", "bateu"])
        sobrescrever_por_mes("spo_tasks_ln_detalhe", det, "mes_referencia")

        atualizar_status_arquivo("SPO - +LN", "✅ OK", f"Operação: {validas_op}/{total_op} PDVs bateram ({pct_op}%)")
        print(f"  ✅ +LN ({mes_ref}): {validas_op}/{total_op} PDVs bateram ({pct_op}%)")
    except Exception as e:
        print(f"  ❌ Erro +LN: {e}")
        atualizar_status_arquivo("SPO - +LN", "❌ ERRO", str(e)[:200])



def _calcular_politica_com_df(df_tasks, mes_ref=None):
    """Wrapper de calcular_politica_comercial usando df já carregado."""
    if _tem_planificador():   # KPI 8 oficial vem do Planificador TTC
        return
    TASKS_TTC = {"03_05_09", "03_03_01", "03_04_01", "03_05_10"}
    SETORES_LOCAL = SETORES_VALIDOS
    META = 60

    ids = df_tasks["id_task"].astype(str).str.strip()
    df_ttc = df_tasks[ids.isin(TASKS_TTC)].copy()
    if df_ttc.empty: return

    df_ttc["_setor"] = df_ttc["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS
    META = 46

    ids = df_tasks["id_task"].astype(str).str.strip()
    df_menu = df_tasks[ids.isin(TASKS_MENU)].copy()
    if df_menu.empty: return

    df_menu["_setor"] = df_menu["setor"].astype(str).str.strip().map(setor_hop)
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


__all__ = [n for n in dir() if not n.startswith("__")]
