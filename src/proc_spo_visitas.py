"""Processador HOP — SPO — visitação, coaching, DTO, promoção, política, menu, tasks cerveja/NAB, score 5, rotina+ e rota efetiva.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403



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
    df["_setor"]  = df[col_setor].str.strip().map(setor_hop)
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
    df["_setor"]  = df["Setor"].str.strip().map(setor_hop)
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
        "1": ["101","102","103","104","105","106","107","108","109","110","111"],   # 107–111 = ex-301–305 (GV1)
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
    if _tem_planificador():
        print("  ↪ KPI 8 vem do Planificador TTC (importado) — cálculo por tasks ignorado")
        return pd.DataFrame()

    TASKS_TTC_LOCAL = {"03_05_09", "03_03_01", "03_04_01", "03_05_10"}
    META_POLITICA_LOCAL = 60
    SETORES_LOCAL = SETORES_VALIDOS

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

        df_ttc["_setor"] = df_ttc["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS

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

        df_menu["_setor"] = df_menu["setor"].astype(str).str.strip().map(setor_hop)
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
        print(f"  Tasks Cerveja encontradas: {len(df)}")

        if df.empty:
            print(f"  ⚠️ Cluster únicos: {df_tasks['cluster_primario'].astype(str).str.strip().unique()[:5].tolist()}")
            print(f"  ⚠️ Categoria únicos: {df_tasks['categoria'].astype(str).str.strip().unique()[:5].tolist()}")
            return pd.DataFrame()

        df["_setor"]  = df["setor"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS

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
    Processa o relatório de Task de Faturamento (Score 5 / SPO Item 12).
    Base "task fat": cada linha é um PDV score 5. A coluna **TASK FAT** (col T)
    marca "1" quando o PDV validou a task de faturamento.
    FÓRMULA OFICIAL (doc do KPI):
      % = (PDVs com task VALIDADA  +  PDVs SEM task) / PDVs TOTAIS score 5
      → PDV que NÃO recebeu a task de faturamento conta como OK.
      pdvs_ok    = TASK FAT==1  OU  sem task (POSSUI TASK != S)
      pdvs_total = TODOS os PDVs score 5 do relatório (não só os com task)
    TRI = média ponderada: soma(pdvs_ok) / soma(pdvs_total) mês a mês.
    Gera duas abas:
      - spo_score5_resumo  : setor | pdvs_total | pdvs_ok | pct | sem_task | meta | ok | mes_referencia
      - spo_score5_detalhe : setor | cod_pdv | nome_pdv | bateu | possui_task |
                             meta_task | real_task | mes_referencia (1 linha por PDV com task)
    """
    import io as _io
    print("📂 Processando Score 5 / Task de Faturamento (SPO Item 12)...")

    SETORES_LOCAL = SETORES_VALIDOS

    def _col(df, *nomes, pos=None):
        """Acha uma coluna por nome (case-insensitive, ignora espaços); fallback posicional."""
        norm = {str(c).strip().upper(): c for c in df.columns}
        for n in nomes:
            c = norm.get(n.strip().upper())
            if c is not None:
                return c
        if pos is not None and pos < len(df.columns):
            return df.columns[pos]
        return None

    def _um(v):
        """True quando o valor representa 1 (aceita '1', '1.0', 1, 'sim')."""
        s = str(v).strip().lower()
        if s in ("1", "1.0", "sim", "s", "true"):
            return True
        try:
            return int(float(s)) == 1
        except Exception:
            return False

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [str(c).strip() for c in df.columns]

        c_rn      = _col(df, "RN", "Setor VDE", "Setor", pos=5)
        c_pdv     = _col(df, "CHAVE PDV", "Chave PDV", "COD PDV", pos=6)
        c_nome    = _col(df, "NOME PDV", "Nome PDV", "RAZAO SOCIAL", pos=8)
        c_taskfat = _col(df, "TASK FAT", "BATEU META", pos=19)
        c_possui  = _col(df, "POSSUI TASK", "POSSUI TASK FAT")
        c_meta    = _col(df, "META TASK")
        c_real    = _col(df, "REAL TASK")

        df["_setor"] = df[c_rn].apply(normalizar_setor)
        df = df[df["_setor"].isin(SETORES_LOCAL)]
        df = df[df[c_pdv].notna() & (df[c_pdv].astype(str).str.strip() != "")]

        df["_bateu"]  = df[c_taskfat].apply(_um)
        df["_possui"] = df[c_possui].apply(_um) if c_possui else True
        # PDV com task = POSSUI TASK==1; quem bateu (TASK FAT==1) também conta como tendo task
        df["_comtask"] = df["_possui"] | df["_bateu"]
        # Fórmula oficial: % = (PDVs com task validada + PDVs SEM task) / PDVs totais score 5.
        # PDV que NÃO recebeu task de faturamento conta como OK. Denominador = TODOS.
        df["_okfinal"] = df["_bateu"] | (~df["_comtask"])

        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes")

        def _base(grp):
            """Universo do KPI (doc oficial): TODOS os PDVs score 5 do relatório."""
            return len(grp)

        resumo, detalhe = [], []
        for setor in sorted(df["_setor"].unique()):
            grp   = df[df["_setor"] == setor]
            base  = _base(grp)
            ok    = int(grp["_okfinal"].sum())
            pct   = round(ok / base * 100, 1) if base > 0 else 0
            resumo.append({
                "setor": setor, "pdvs_total": base, "pdvs_ok": ok, "pct": pct,
                "sem_task": int((~grp["_comtask"]).sum()),
                "meta": META_SCORE5, "ok": "OK" if pct >= META_SCORE5 else "NOK",
                "mes_referencia": mes_ref,
            })
            # Detalhe: só PDVs com task (acionável)
            comtask = grp[grp["_comtask"]]
            for _, r in comtask.iterrows():
                detalhe.append({
                    "setor": setor,
                    "cod_pdv": str(r[c_pdv]).strip(),
                    "nome_pdv": (str(r[c_nome]).strip() if c_nome else ""),
                    "bateu": "Sim" if r["_bateu"] else "Não",
                    "possui_task": "Sim" if r["_possui"] else "Não",
                    "meta_task": (str(r[c_meta]).strip() if c_meta else ""),
                    "real_task": (str(r[c_real]).strip() if c_real else ""),
                    "mes_referencia": mes_ref,
                })
            print(f"  Setor {setor}: {ok}/{base} ({pct}%)")

        base_op = _base(df)
        ok_op   = int(df["_okfinal"].sum())
        pct_op  = round(ok_op / base_op * 100, 1) if base_op > 0 else 0
        resumo.append({
            "setor": "OPERACAO", "pdvs_total": base_op, "pdvs_ok": ok_op, "pct": pct_op,
            "sem_task": int((~df["_comtask"]).sum()),
            "meta": META_SCORE5, "ok": "OK" if pct_op >= META_SCORE5 else "NOK",
            "mes_referencia": mes_ref,
        })

        df_resumo = pd.DataFrame(resumo)
        # Detalhe ordenado: setor, depois "Não" antes de "Sim"
        df_det = pd.DataFrame(detalhe)
        if not df_det.empty:
            df_det["_ord"] = (df_det["bateu"] == "Sim").astype(int)
            df_det = df_det.sort_values(["setor", "_ord", "nome_pdv"]).drop(columns=["_ord"])
        sobrescrever_aba("spo_score5_resumo", df_resumo)
        sobrescrever_aba("spo_score5_detalhe", df_det)
        atualizar_status_arquivo("SPO - Score 5 (ON_TRADE)", "✅ OK",
                                 f"Operação: {ok_op}/{base_op} PDVs ({pct_op}%) bateram a task de fat")
        print(f"  ✅ Score 5: {pct_op}% operação ({ok_op}/{base_op} PDVs com task) · detalhe: {len(df_det)} linhas")
        return df_resumo

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  ❌ Erro: {e}")
        return pd.DataFrame()



# ─── SPO — ROTINA+ (% de Visitas Medianas ou Excelentes) ─────────────────────
META_ROTINA_MAIS = 49  # meta TRI (%)


def processar_rotina_mais(conteudo_bytes, mes_ref=None):
    """Rotina+ (SPO) — KPI '% de Visitas Medianas ou Excelentes'. Export do BI, visita a
    visita (já traz Passo 2-6 OK/NOK, Qtd Passos OK e Cluster Visita). Apuradas = GPS OK.
    Crítica ≤2 passos, Mediana 3, Excelente ≥4. KPI = (Med+Exc)/apuradas.
    Gera spo_rotina_mais_resumo (setor + OPERACAO, com abertura por passo) e _detalhe."""
    import io
    print("📂 Processando Rotina+ (SPO)...")
    mes_ref = mes_ref or date.today().strftime("%Y-%m")
    try:
        try:
            df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
        except Exception:
            df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        c_setor = next((c for c in df.columns if c.strip().lower() == "setor"), None)
        c_pdv   = next((c for c in df.columns if "cod pdv" in c.strip().lower()), None)
        c_nome  = next((c for c in df.columns if "nome pdv" in c.strip().lower()), None)
        c_gps   = next((c for c in df.columns if c.strip().upper() == "GPS"), None)
        c_cl    = next((c for c in df.columns if "cluster visita" in c.strip().lower()), None)
        c_qtd   = next((c for c in df.columns if "qtd" in c.strip().lower() and "passo" in c.strip().lower()), None)
        if not all([c_setor, c_gps, c_cl]):
            raise ValueError(f"Colunas Setor/GPS/Cluster não encontradas. Disponíveis: {df.columns.tolist()}")
        passos = [c for c in df.columns if c.strip().lower().startswith("passo ")]

        df["_setor"] = df[c_setor].apply(normalizar_setor)
        df = df[df["_setor"].isin(SETORES_VALIDOS)]
        if c_pdv:
            df = df[df[c_pdv].notna() & (df[c_pdv].astype(str).str.strip() != "")]
        import unicodedata
        def _norm(s):
            s = unicodedata.normalize("NFKD", str(s))
            return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()
        df["_gps"] = df[c_gps].astype(str).str.strip().str.upper()
        # Sem acento/case e por prefixo → cobre critico|critica, mediano|mediana, excelente.
        df["_cl"]  = df[c_cl].apply(_norm)
        g = df[df["_gps"] == "OK"].copy()
        if g.empty:
            raise ValueError(f"Nenhuma visita com GPS = OK ({len(df)} linhas de setor válido). "
                             f"Valores de GPS no arquivo: {sorted(df['_gps'].unique().tolist())[:10]}")

        def _linha(setor, sub):
            base = len(sub)
            cri = int(sub["_cl"].str.startswith("crit").sum())
            med = int(sub["_cl"].str.startswith("median").sum())
            exc = int(sub["_cl"].str.startswith("excelen").sum())
            pct_me = round((med + exc) / base * 100, 1) if base > 0 else 0
            row = {
                "setor": setor, "visitas": base, "criticas": cri, "medianas": med, "excelentes": exc,
                "pct_critica":   round(cri / base * 100, 1) if base > 0 else 0,
                "pct_mediana":   round(med / base * 100, 1) if base > 0 else 0,
                "pct_excelente": round(exc / base * 100, 1) if base > 0 else 0,
                "pct_med_exc":   pct_me,
                "meta": META_ROTINA_MAIS, "ok": "OK" if pct_me >= META_ROTINA_MAIS else "NOK",
                "mes_referencia": mes_ref,
            }
            for pc in passos:
                key = "passo" + "".join(ch for ch in pc if ch.isdigit())
                okp = int((sub[pc].astype(str).str.strip().str.upper() == "OK").sum())
                row[key] = round(okp / base * 100, 1) if base > 0 else 0
            return row

        resumo, detalhe = [], []
        for setor, sub in g.groupby("_setor"):
            resumo.append(_linha(setor, sub))
            for _, r in sub.iterrows():
                detalhe.append({
                    "setor": setor,
                    "cod_pdv": (str(r[c_pdv]).strip() if c_pdv else ""),
                    "nome_pdv": (str(r[c_nome]).strip() if c_nome else ""),
                    "cluster": str(r[c_cl]).strip(),
                    "qtd_passos_ok": (str(r[c_qtd]).strip() if c_qtd else ""),
                    "mes_referencia": mes_ref,
                })
        resumo.append(_linha("OPERACAO", g))

        sobrescrever_aba("spo_rotina_mais_resumo", pd.DataFrame(resumo))
        sobrescrever_aba("spo_rotina_mais_detalhe", pd.DataFrame(detalhe))
        op = resumo[-1]
        atualizar_status_arquivo("SPO - Rotina+", "✅ OK",
                                 f"Operação: {op['pct_med_exc']}% Med+Exc ({op['medianas']+op['excelentes']}/{op['visitas']} visitas)")
        print(f"  ✅ Rotina+: {op['pct_med_exc']}% operação Med+Exc ({op['visitas']} visitas GPS OK)")
        return pd.DataFrame(resumo)
    except Exception as e:
        # Repassa o erro: antes o import mostrava ✅ sem gravar nada (farol/SPO ficavam sem Rotina+).
        import traceback
        traceback.print_exc()
        atualizar_status_arquivo("SPO - Rotina+", "❌ ERRO", str(e)[:200])
        print(f"  ❌ Erro: {e}")
        raise



# ─── ROTA EFETIVA (BI de produtividade de visitas) ───────────────────────────
def _re_linha(setor, mes, sub):
    tot = len(sub)
    ef  = int(sub["_ef"].sum())
    gps = int(sub["_gps"].sum())
    sup = int(sub["_sup"].sum())
    return {
        "setor": setor, "mes_referencia": mes,
        "planejadas": tot, "efetivas": ef,
        "re_pct": round(ef / tot * 100, 1) if tot else 0,
        "gps_ok": gps, "gps_pct": round(gps / tot * 100, 1) if tot else 0,
        "supervisitas": sup,
    }



def processar_rota_efetiva(conteudo_bytes, mes_ref=None):
    """Rota Efetiva (BI). 1 linha por visita planejada. RE% = Visita Efetiva=1 /
    planejadas (excluindo dias expurgados). Gera:
      - rota_efetiva_resumo: setor × mês (+ OPERACAO) → planejadas, efetivas, RE%, GPS%, supervisitas
      - rota_efetiva_pdv:    cod_pdv × mês → planejadas, efetivas, ult_visita
    Acumula por mês (o arquivo traz o ano todo)."""
    import io
    print("📂 Processando Rota Efetiva...")
    try:
        try:
            df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, sheet_name="Export")
        except Exception:
            df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        low = {c.strip().lower(): c for c in df.columns}
        col = lambda *names: next((low[n.lower()] for n in names if n.lower() in low), None)
        c_setor = col("Setor"); c_pdv = col("PDV"); c_dia = col("Dia de Visita")
        c_ef = col("Visita Efetiva"); c_exp = col("Flag Dia Expurgado"); c_gps = col("GPS Check In OK")
        c_sup = col("É Supervisita?", "É Supervisita?", "Supervisita?")
        if not all([c_setor, c_pdv, c_dia, c_ef]):
            raise ValueError(f"Colunas essenciais ausentes. Disponíveis: {df.columns.tolist()[:40]}")

        df["_setor"] = df[c_setor].apply(normalizar_setor)
        df = df[df["_setor"].isin(SETORES_VALIDOS)].copy()
        # "Dia de Visita" vem em ISO (YYYY-MM-DD HH:MM:SS). NÃO usar dayfirst=True:
        # com dayfirst o pandas falha em todo dia > 12 (dropava ~60% das linhas).
        dia = pd.to_datetime(df[c_dia].astype(str).str.strip(), errors="coerce")
        df["_mes"] = dia.dt.strftime("%Y-%m")
        df["_dia_iso"] = dia.dt.strftime("%Y-%m-%d")
        df = df[df["_mes"].notna() & (df["_mes"] != "")]
        df["_pdv"] = df[c_pdv].astype(str).str.strip().str.lstrip("0")
        df["_exp"] = df[c_exp].astype(str).str.strip().str.lower().eq("sim") if c_exp else False
        df["_ef"]  = df[c_ef].astype(str).str.strip().eq("1")
        df["_gps"] = df[c_gps].astype(str).str.strip().eq("1") if c_gps else False
        df["_sup"] = df[c_sup].astype(str).str.strip().eq("1") if c_sup else False

        plan = df[~df["_exp"]].copy()  # planejadas (fora dias expurgados)
        if plan.empty:
            raise ValueError("Sem linhas válidas após filtro de setor/expurgo.")

        resumo = []
        for (setor, mes), sub in plan.groupby(["_setor", "_mes"]):
            resumo.append(_re_linha(setor, mes, sub))
        for mes, sub in plan.groupby("_mes"):
            resumo.append(_re_linha("OPERACAO", mes, sub))
        sobrescrever_por_mes("rota_efetiva_resumo", pd.DataFrame(resumo), "mes_referencia")

        rows = []
        for (setor, pdv, mes), sub in plan.groupby(["_setor", "_pdv", "_mes"]):
            ef = int(sub["_ef"].sum())
            ult = sub.loc[sub["_ef"], "_dia_iso"].max() if ef else ""
            rows.append({"setor": setor, "cod_pdv": pdv, "planejadas": len(sub), "efetivas": ef,
                         "ult_visita": ult if isinstance(ult, str) else "", "mes_referencia": mes})
        sobrescrever_por_mes("rota_efetiva_pdv", pd.DataFrame(rows), "mes_referencia")

        meses = sorted(plan["_mes"].dropna().unique().tolist())
        atualizar_status_arquivo("Rota Efetiva", "✅ OK", f"{len(plan)} visitas · meses {meses[0]}–{meses[-1]}")
        print(f"  ✅ Rota Efetiva: {len(plan)} planejadas, meses {meses}")
        return pd.DataFrame(resumo)
    except Exception as e:
        import traceback
        traceback.print_exc()
        atualizar_status_arquivo("Rota Efetiva", "❌ ERRO", str(e)[:200])
        print(f"  ❌ Erro Rota Efetiva: {e}")
        return pd.DataFrame()


# ─── SPO — PLANIFICADOR DE EXECUÇÃO TTC (KPI 8 · Aderência da Política Comercial) ──
# Fonte oficial do KPI 8 a partir do Q4/2026 (antes era aproximado só pelas tasks).
# Uma linha por PDV Comprador RGB Coletável (base). REAL = Aderidos E Executados / base.

def _tem_planificador():
    """True se já existe planificador importado — aí o KPI 8 sai dele, não das tasks."""
    try:
        return not ler_aba("planificador_ttc").empty
    except Exception:
        return False


def _resumo_planificador(df, mes_ref):
    """Monta o spo_politica_resumo (por setor + OPERACAO) a partir do planificador."""
    def linha(setor, g):
        base = len(g)
        ad   = int((g["aderido"] == "SIM").sum())
        ex   = int((g["executado"] == "SIM").sum())
        ae   = int((g["aderido_exec"] == "SIM").sum())
        rec  = int(g[["fraude", "regra_60", "sem_sku", "outros"]].sum().sum())
        return {
            "setor": setor, "gv": str(g["gv"].iloc[0]) if setor != "OPERACAO" and base else "",
            "base": base, "pdvs_aderidos": ad, "pdvs_executados": ex, "pdvs_execucao": ae,
            "pct": round(ae / base * 100, 1) if base else 0,
            "pct_aderidos": round(ad / base * 100, 1) if base else 0,
            "pct_executados": round(ex / base * 100, 1) if base else 0,
            "aderidos_sem_exec": int(((g["aderido"] == "SIM") & (g["executado"] != "SIM")).sum()),
            "tasks_totais": int(g["totais"].sum()), "validadas": int(g["validadas"].sum()),
            "justificadas": int(g["justificadas"].sum()), "sem_visita": int(g["sem_visita"].sum()),
            "recusadas": rec, "fonte": "planificador", "mes_referencia": mes_ref,
        }
    resumo = [linha(s, g) for s, g in sorted(df.groupby("setor"), key=lambda x: x[0])]
    resumo.append(linha("OPERACAO", df))
    return pd.DataFrame(resumo)


def processar_planificador_ttc(conteudo_bytes):
    """Planificador de Execução TTC do BI (export da tabela): Operação | UNB | PDV | GV |
    Setor | Segmentação Primária | Nome PDV | Frequência Visita | PDV Aderido e Executado |
    PDV Aderido | Peso Volume Aderido | PDV Executado | Validadas | Totais | Justificadas |
    Sem Visita | Fraude | Regra 60% | Sem Identificação SKU | Outros (tasks TTC L60d).
    Gera `planificador_ttc` (1 linha/PDV) e o `spo_politica_resumo` (KPI 8).
    Arquivo FILTRADO (só alguns setores) atualiza só esses setores."""
    import re as _re
    print("📂 Processando Planificador de Execução TTC (SPO Item 8)...")
    bruto = pd.read_excel(io.BytesIO(conteudo_bytes), header=None, dtype=str)
    # o cabeçalho é a 1ª linha que tem "PDV Aderido" (o export às vezes traz filtros antes)
    hdr = next((i for i in range(min(10, len(bruto)))
                if any("PDV ADERIDO" in _txt(v).upper() for v in bruto.iloc[i])), None)
    if hdr is None:
        raise ValueError("Cabeçalho do planificador não encontrado (coluna 'PDV Aderido').")
    df = bruto.iloc[hdr + 1:].copy()
    df.columns = [_txt(c) for c in bruto.iloc[hdr]]

    def norm(t):
        t = _txt(t).upper()
        for a, b in (("Ã", "A"), ("Á", "A"), ("Ç", "C"), ("Ê", "E"), ("É", "E"), ("Ê", "E"), ("Ó", "O"), ("Ú", "U"), ("Í", "I")):
            t = t.replace(a, b)
        return _re.sub(r"\s+", " ", t)
    mapa = {norm(c): c for c in df.columns if c}

    def col(*nomes, obrig=True):
        for n in nomes:
            if norm(n) in mapa: return mapa[norm(n)]
        if obrig:
            raise ValueError(f"Coluna não encontrada: {nomes[0]}. Disponíveis: {list(mapa.values())}")
        return None

    c_setor, c_pdv, c_ae = col("Setor"), col("PDV"), col("PDV Aderido e Executado")
    c_ad, c_ex = col("PDV Aderido"), col("PDV Executado")

    def num(c):
        if not c or c not in df.columns: return pd.Series(0, index=df.index)
        return pd.to_numeric(df[c].map(_txt).str.replace("%", "").str.replace(",", "."), errors="coerce").fillna(0)

    def simnao(c):
        return df[c].map(_txt).str.upper().map(lambda s: "SIM" if s.startswith("S") else "NÃO")

    df = df[df[c_pdv].map(_txt) != ""]
    df["_setor"] = df[c_setor].apply(normalizar_setor)
    # 997/995 (PDV sem RN) ficam: entram na base da OPERAÇÃO como no BI (306 = 302 + 4 do 997)
    df = df[df["_setor"].isin(SETORES_VALIDOS) | df["_setor"].isin(["995", "997"])].copy()

    peso = num(col("Peso Volume Aderido", obrig=False))
    if len(peso) and peso.max() > 1.5:   # veio em 0–100 em vez de 0–1
        peso = peso / 100
    hoje = hoje_brasilia()
    mes_ref = f"{hoje.year}-{hoje.month:02d}"
    c_gv, c_nome = col("GV", obrig=False), col("Nome PDV", obrig=False)
    c_seg, c_freq = col("Segmentação Primária", "Segmentacao Primaria", obrig=False), col("Frequência Visita", "Frequencia Visita", obrig=False)
    txt = lambda c: df[c].map(_txt) if c else ""
    out = pd.DataFrame({
        "setor":        df["_setor"],
        "gv":           txt(c_gv).str.replace(r"\.0$", "", regex=True) if c_gv else "",
        "cod_pdv":      df[c_pdv].map(_txt).str.replace(r"\.0$", "", regex=True).str.lstrip("0"),
        "nome_pdv":     txt(c_nome),
        "segmento":     txt(c_seg),
        "dia_visita":   txt(c_freq).str.strip("/ ") if c_freq else "",
        "aderido_exec": simnao(c_ae),
        "aderido":      simnao(c_ad),
        "peso_aderido": (peso * 100).round(1),
        "executado":    simnao(c_ex),
        "validadas":    num(col("Validadas", obrig=False)).astype(int),
        "totais":       num(col("Totais", obrig=False)).astype(int),
        "justificadas": num(col("Justificadas", obrig=False)).astype(int),
        "sem_visita":   num(col("Sem Visita", obrig=False)).astype(int),
        "fraude":       num(col("Fraude", obrig=False)).astype(int),
        "regra_60":     num(col("Regra 60%", "Regra 60", obrig=False)).astype(int),
        "sem_sku":      num(col("Sem Identificação SKU", "Sem Identificacao SKU", obrig=False)).astype(int),
        "outros":       num(col("Outros", obrig=False)).astype(int),
        "mes_referencia": mes_ref,
    })

    setores_novos = sorted(set(out["setor"])) if not out.empty else []
    mantidos = []
    try:
        antigo = ler_aba("planificador_ttc")
        if not antigo.empty and "setor" in antigo.columns and setores_novos:
            antigo["setor"] = antigo["setor"].astype(str).str.strip().map(setor_hop)
            fora = antigo[~antigo["setor"].isin(setores_novos)]
            if not fora.empty:
                mantidos = sorted(set(fora["setor"]))
                out = pd.concat([out, fora[[c for c in out.columns if c in fora.columns]]], ignore_index=True)
    except Exception as e:
        print(f"  ⚠️ planificador anterior não lido (segue só com o arquivo): {e}")

    for c in ("validadas", "totais", "justificadas", "sem_visita", "fraude", "regra_60", "sem_sku", "outros"):
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype(int)
    sobrescrever_aba("planificador_ttc", out)
    resumo = _resumo_planificador(out, mes_ref)
    sobrescrever_aba("spo_politica_resumo", resumo)

    op = resumo[resumo["setor"] == "OPERACAO"].iloc[0]
    msg = (f"{int(op['base'])} PDVs base · {int(op['pdvs_execucao'])} aderidos e executados ({op['pct']}%) · "
           f"{int(op['aderidos_sem_exec'])} aderidos sem execução · setores: {', '.join(setores_novos) or '—'}")
    if mantidos:
        msg += f" · ⚠️ arquivo parcial — mantidos da importação anterior: {', '.join(mantidos)}"
    atualizar_status_arquivo("Planificador TTC (BI)", "⚠️ Parcial" if mantidos else "✅ OK", msg)
    atualizar_status_arquivo("SPO - Política Comercial", "✅ OK", f"Planificador: {op['pct']}% ({int(op['pdvs_execucao'])}/{int(op['base'])} PDVs)")
    print(f"  {msg}")
    return out


__all__ = [n for n in dir() if not n.startswith("__")]
