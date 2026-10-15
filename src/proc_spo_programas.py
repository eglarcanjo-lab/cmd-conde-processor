"""Processador HOP — SPO — pedido alone, +RGB, cupons, loja ideal, scanntech, portfólio ideal e atendimento produtivo.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403




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
    SETORES_LOCAL = SETORES_VALIDOS
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
    SETORES_LOCAL = SETORES_VALIDOS

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Filtrar só setores locais (coluna RN)
        df["setor"] = df["RN"].astype(str).str.strip().map(setor_hop)
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
    SETORES_LOCAL = SETORES_VALIDOS

    try:
        try:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), sheet_name="Export", dtype=str)
        except Exception:
            df = pd.read_excel(_io.BytesIO(conteudo_bytes), dtype=str)

        df.columns = [c.strip() for c in df.columns]

        # Mês vigente no formato do arquivo: Mai/2026 (horário de Brasília — evita
        # apontar o mês seguinte perto da virada, quando o UTC já passou da meia-noite)
        hoje = hoje_brasilia()
        mes_vigente = f"{MESES_PT[hoje.month]}/{hoje.year}"
        mes_ref = hoje.strftime("%Y-%m")
        print(f"  Mês vigente: {mes_vigente}")

        # Filtrar só setores locais
        df["setor"] = df["RN"].astype(str).str.strip().map(setor_hop)
        df["gv"]    = df["GV"].astype(str).str.strip()
        df = df[df["setor"].isin(SETORES_LOCAL)].copy()
        df["_cupons"]   = pd.to_numeric(df["Cupons"], errors="coerce").fillna(0)
        # Bloqueio = "Não" tolerante a acento/maiúsculas/espaços (Não/NAO/nao…)
        df["_bloqueio"] = df["Bloqueio"].astype(str).str.strip().str.upper().isin(["NÃO", "NAO"])
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

        # O arquivo Scanntech é um snapshot sem coluna de mês → se a UI não mandar
        # "Mês de referência", usa o mês atual (Brasília). Para importar um mês
        # passado, preencha o "Mês de referência" no formulário de Arquivos.
        mes_ref = mes_ref or _mes_ref_do_dado(df, "Mês", "Mês Referência", "Período", "Data", "mes", "data",
                                              fallback=hoje_brasilia().strftime("%Y-%m"))

        # ── Detalhe ──────────────────────────────────────────────────────────
        df_det = df[["_cod_pdv","_nome","setor","_gv","dia_visita","_status","_ativo"]].copy()
        df_det.columns = ["cod_pdv","nome_pdv","setor","gv","dia_visita","status_scanntech","is_ativo"]
        df_det["is_ativo"] = df_det["is_ativo"].map({True:"SIM", False:"NÃO"})
        df_det["mes_referencia"] = mes_ref
        # Remover linhas com GV vazio/nulo/nan
        df_det = df_det[df_det["gv"].astype(str).str.strip().str.lower().isin(["", "nan", "none"]) == False]
        # Acumula por mês (substitui só o mês importado) — mantém os demais meses do tri.
        sobrescrever_por_mes("spo_scanntech_detalhe", df_det, "mes_referencia")

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
        sobrescrever_por_mes("spo_scanntech_resumo", df_resumo, "mes_referencia")
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
    SETORES_LOCAL = SETORES_VALIDOS

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

        df["setor"] = df["RN"].astype(str).str.strip().map(setor_hop)
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
      Layout ATUAL do BI (mudou — 3 formatos diferentes por KPI):
        Positivação (Tasks): Meta='53' · Visitas Positivadas='57' (=real) · GAP  [número absoluto]
        Carteira (Compradores): Meta.1='100' · Real='134' · GAP.1                       [contagem]
        GPS: Meta.2='0.882' · Real.1='1.244' · GAP.2                                     [fração 0-1]
        Rota Efetiva: Meta.3='0.8' · Real.2='0.995' · GAP.3                              [fração 0-1]
    Gera: spo_ap_resumo e spo_ap_detalhe
    """
    import io as _io
    print("📂 Processando Atendimento Produtivo (SPO Item 5)...")
    SETORES_LOCAL = SETORES_VALIDOS

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

        # 3 formatos: % em texto ("52.6%"), fração (0-1 → ×100) e contagem (134).
        def _pstr(v):   # "52.6%" → 52.6
            s = str(v).replace("%", "").replace(",", ".").strip()
            try: return round(float(s), 1)
            except: return None
        def _pfrac(v):  # "0.882" → 88.2
            s = str(v).replace(",", ".").strip()
            try: return round(float(s) * 100, 1)
            except: return None
        def _inteiro(v):  # "134" → 134
            s = str(v).replace(",", ".").strip()
            try: return int(round(float(s)))
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
                # Positivação Tasks Compra (Meta / Visitas Positivadas / GAP — número absoluto)
                "positiv_meta":    _inteiro(row.get("Meta","")),
                "positiv_real":    _inteiro(row.get("Visitas Positivadas","")),
                "positiv_gap":     _inteiro(row.get("GAP","")),
                # Carteira Ideal / Compradores (Meta.1 / Real / GAP.1 — contagem)
                "carteira_meta":   _inteiro(row.get("Meta.1","")),
                "carteira_real":   _inteiro(row.get("Real","")),
                "carteira_gap":    _inteiro(row.get("GAP.1","")),
                # GPS (Meta.2 / Real.1 / GAP.2 — fração)
                "gps_meta":        _pfrac(row.get("Meta.2","")),
                "gps_real":        _pfrac(row.get("Real.1","")),
                "gps_gap":         _pfrac(row.get("GAP.2","")),
                # Rota Efetiva (Meta.3 / Real.2 / GAP.3 — fração)
                "rota_meta":       _pfrac(row.get("Meta.3","")),
                "rota_real":       _pfrac(row.get("Real.2","")),
                "rota_gap":        _pfrac(row.get("GAP.3","")),
                "mes_referencia":  mes_ref,
            })

        df_detalhe = pd.DataFrame(df_det)
        # Acumula por mês (substitui só o mês do arquivo) — mantém os meses do tri
        # disponíveis para o seletor de mês na tela do KPI 18.
        sobrescrever_por_mes("spo_ap_detalhe", df_detalhe, "mes_referencia")

        # ── rv_ap — popula aba usada pelo cálculo de RV ───────────────────────
        rv_ap_rows = []
        for _, row in df.iterrows():
            ap_ok_val = "OK" if str(row.get("AP OK", "")).strip().upper() == "SIM" else "NOK"
            rv_ap_rows.append({
                "setor":               row["setor"],
                "mes_referencia":      mes_ref,
                "tasks_compra_real":   _inteiro(row.get("Visitas Positivadas", "")),
                "tasks_compra_meta":   _inteiro(row.get("Meta", "")),
                "compradores_real":    _inteiro(row.get("Real", "")),
                "compradores_meta":    _inteiro(row.get("Meta.1", "")),
                "rota_efetiva_real":   _pfrac(row.get("Real.2", "")),
                "rota_efetiva_meta":   _pfrac(row.get("Meta.3", "")),
                "gps_real":            _pfrac(row.get("Real.1", "")),
                "gps_meta":            _pfrac(row.get("Meta.2", "")),
                "ap_ok":               ap_ok_val,
            })
        df_rv_ap = pd.DataFrame(rv_ap_rows)
        # Acumula por mês: substitui só o mês do arquivo, mantém os demais.
        sobrescrever_por_mes("rv_ap", df_rv_ap, "mes_referencia")
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
        sobrescrever_por_mes("spo_ap_resumo", df_resumo, "mes_referencia")
        atualizar_status_arquivo("SPO - Atendimento Produtivo", "✅ OK",
                                 f"Operação: {pct_op}% ({ap_ok_op}/{total_op} RNs)")
        print(f"  ✅ AP: {pct_op}% ({ap_ok_op}/{total_op} RNs com AP OK)")
        return df_resumo

    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ❌ Erro: {e}"); return pd.DataFrame()


__all__ = [n for n in dir() if not n.startswith("__")]
