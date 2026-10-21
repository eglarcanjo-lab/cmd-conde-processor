"""Processador HOP — CORA fonte única (consulta-pedidos completa), coleta e histórico de pedidos.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403
from proc_analises import _processar_vendas_cliente, _processar_vendas_diaria, _processar_ruptura
from proc_cadastros import _registrar_sem_categoria
from proc_clientes_pedidos import _montar_detalhe_pedidos, _norm_cabecalho, processar_cora, processar_pedidos
from proc_rv import processar_volume_rv



# ══ CORA — FONTE ÚNICA (consulta-pedidos completa, 143 colunas, 1 linha por ITEM) ══════
# Um só arquivo alimenta: Pedidos (venda/volume → todas as análises e RV), Devoluções,
# Faturados (NF), Faturamento Marketplace e Buffer + Deck D+7.
#
# Regras (Eduardo, out/2026):
#   • VENDA = Cód. operação (AR) = 1 · item ATENDIDO (Situação atend. item) · item/pedido
#     não devolvido/cancelado (Situação item). Inclui o que está em rota (MPD_*), não só ENTREGUE.
#   • Volume = Volume hectolitro (CS), lançado no DIA DE EMISSÃO DA NF (EB). Sem NF → entrega − 1 dia (D+1 = venda do dia).
#   • Devolução = Situação pedido FATURADO_NF_DEVOLVIDA; motivo = código (EH) → tabela de motivos;
#     código desconhecido vira ocorrência (motivos_devolucao_pendentes) p/ cadastrar.
#   • Buffer = Tipo buffer (W) = REPROGRAMADO e Situação pedido = AGUARDANDO_ROTEIRIZACAO.
#   • Proteção de mês: só são gravados os meses que o arquivo COBRE (há entregas no mês, col D).
#     NF de um mês não coberto (ex.: NF 30/09 entregue 01/10 num arquivo só de outubro) é
#     ignorada — senão o import apagaria o mês anterior inteiro (as tabelas substituem por mês).

# Virada de fonte: até set/2026 os dados são do Promax (fechamento de setembro = Promax);
# o CORA só grava de out/2026 em diante — nunca sobrescreve mês anterior.
CORA_INICIO = "2026-10"


MOTIVOS_DEVOLUCAO_PADRAO = {
    "37": "PDV FECHADO", "45": "TEMPO INSUFICIENTE", "33": "NAO FEZ PEDIDO", "48": "PDV FECHADO APOS",
    "38": "SEM DINHEIRO", "39": "CLIENTE CANCELOU", "71": "SEM DINHEIRO/CHEQUE", "47": "DIFICIL ACESSO",
    "46": "ENDERECO NAO ENCONTRADO", "41": "SEM VASILHAME", "64": "SEM VASILHAME",
    "23": "(C)PEDIDOS DUPLICADOS", "50": "CARGA ERRADA ARMAZEM", "86": "HORARIO DE ENTREGA",
}



def _motivos_devolucao():
    """Código → descrição: padrão + os cadastrados no app (tabela motivos_devolucao)."""
    m = dict(MOTIVOS_DEVOLUCAO_PADRAO)
    try:
        t = ler_aba("motivos_devolucao")
        if not t.empty and "cod" in t.columns:
            for _, r in t.iterrows():
                c = str(r.get("cod", "")).strip().lstrip("0")
                d = str(r.get("descricao", "") or "").strip().upper()
                if c and d:
                    m[c] = d
    except Exception:
        pass
    return m



def _num_br(serie):
    return pd.to_numeric(serie.astype(str).str.strip().str.replace(".", "", regex=False)
                         .str.replace(",", ".", regex=False), errors="coerce").fillna(0.0)



def processar_cora_completo(conteudo_bytes, mes_ref=None, df_clientes=None):
    """Importa o consulta-pedidos COMPLETO do CORA e alimenta todas as rotinas que antes
    vinham de relatórios separados do Promax. Retorna um resumo por rotina."""
    import io as _io
    print("📂 Processando CORA completo (consulta-pedidos)...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [str(c).strip() for c in df.columns]
    obrig = ["Número pedido", "Data entrega", "Cód. cliente", "Situação pedido", "Situação atend. item",
             "Cód. operação", "Cód. setor", "Cód. produto", "Volume hectolitro", "Data emissão NF", "Número NF"]
    falta = [c for c in obrig if c not in df.columns]
    if falta:
        # Arquivo antigo (só ~19 colunas): segue só com Buffer + Deck, como antes.
        print(f"  ⚠️ CORA sem colunas do relatório completo ({falta[:4]}…) — só Buffer + Deck.")
        d = processar_cora(conteudo_bytes)
        return {"buffer_deck": f"{len(d)} linhas (arquivo reduzido — importe o relatório completo)"}

    t = lambda c: df[c].astype(str).str.strip().replace("nan", "") if c in df.columns else pd.Series("", index=df.index)
    df["_setor"] = df["Cód. setor"].apply(normalizar_setor)
    df["_sit_ped"] = t("Situação pedido").str.upper()
    df["_sit_item"] = t("Situação item").str.upper()
    df["_atend_item"] = t("Situação atend. item").str.upper()
    df["_op"] = t("Cód. operação").str.lstrip("0")
    df["_hl"] = _num_br(t("Volume hectolitro"))
    d_ent = pd.to_datetime(t("Data entrega"), format="%d/%m/%Y", errors="coerce")
    d_nf = pd.to_datetime(t("Data emissão NF"), format="%d/%m/%Y", errors="coerce")
    # Sem NF ainda → entrega − 1 dia (a NF sai sempre na véspera da entrega). Assim o pedido de
    # hoje p/ entrega amanhã (D+1) já conta como venda/volume diário de HOJE e não muda de dia
    # quando a NF for emitida.
    df["_data"] = d_nf.fillna(d_ent - pd.Timedelta(days=1))
    df["_mes"] = df["_data"].dt.strftime("%Y-%m")

    # Proteção de mês: só meses COBERTOS pelo arquivo (entregas dentro do mês) e a partir da
    # VIRADA para o CORA (CORA_INICIO). Meses anteriores são do Promax e ficam intocados.
    cobertos = {m for m in d_ent.dt.strftime("%Y-%m").dropna() if m >= CORA_INICIO}
    fora = df["_mes"].notna() & ~df["_mes"].isin(cobertos)
    if fora.any():
        print(f"  ⚠️ {int(fora.sum())} linhas com NF em mês não coberto pelo arquivo "
              f"({sorted(df.loc[fora, '_mes'].unique())}) — ignoradas p/ não sobrescrever o mês.")
    df = df[~fora & df["_mes"].notna()].copy()
    meses = sorted(df["_mes"].unique())
    print(f"  📅 Meses cobertos e gravados: {meses}")
    if df.empty:
        # Nada a gravar (ex.: arquivo só de mês do Promax). NÃO roda pedidos/devoluções —
        # senão os snapshots (cobertura, mix…) seriam refeitos vazios. Buffer/Deck = estado atual.
        print("  ⏭️ Nenhum mês do CORA a gravar (anteriores à virada ficam com o Promax) — só Buffer + Deck.")
        d_bd = processar_cora(conteudo_bytes)
        return {"aviso": f"nenhum mês a partir de {CORA_INICIO} no arquivo — vendas/devoluções não alteradas",
                "buffer_deck": f"buffer {d_bd['num_pedido'].nunique() if len(d_bd) else 0} pedidos"}

    CANCEL = {"CANCELADO", "FATURADO_NF_DEVOLVIDA", "FATURADO_NF_CANCELADA"}
    df["_venda"] = ((df["_op"] == "1") & (df["_atend_item"] == "ATENDIDO") &
                    ~df["_sit_item"].isin(CANCEL) & ~df["_sit_ped"].isin(CANCEL))
    df["_devolvido"] = df["_sit_ped"] == "FATURADO_NF_DEVOLVIDA"
    motivos = _motivos_devolucao()
    df["_cod_motivo"] = t("Motivo devol./cancel. NF").str.lstrip("0")   # "0" = sem motivo → ""
    df["_desc_motivo"] = df["_cod_motivo"].map(lambda c: motivos.get(c, f"MOTIVO {c} (NÃO CADASTRADO)") if c else "")
    res = {}

    # ── 2) DEVOLUÇÕES → entregas_frustradas (1 linha por NF devolvida, acumula por mês) ──
    dv = df[df["_devolvido"] & (t("Número NF").loc[df.index] != "") & df["_setor"].isin(SETORES_VALIDOS)].copy()
    cols_out = ["setor", "data", "nota", "cod_pdv", "nome_pdv", "placa", "valor", "volume_hl",
                "data_devol", "cod_motivo", "desc_motivo", "mes_referencia"]
    if not dv.empty:
        dv["_nota"] = t("Número NF").loc[dv.index].str.lstrip("0")
        dv["_vnf"] = _num_br(t("Valor total NF").loc[dv.index])
        out = (dv.groupby("_nota", as_index=False).agg(
            setor=("_setor", "first"), data=("_data", "first"), cod_pdv=("Cód. cliente", "first"),
            nome_pdv=("Nome cliente", "first"), placa=("Placa", "first"), valor=("_vnf", "first"),
            volume_hl=("_hl", "sum"), data_devol=("Data devol./cancel. NF", "first"),
            cod_motivo=("_cod_motivo", "first"), desc_motivo=("_desc_motivo", "first"), mes_referencia=("_mes", "first"))
            .rename(columns={"_nota": "nota"}))
        out["data"] = out["data"].dt.strftime("%d/%m/%Y")
        out["cod_pdv"] = out["cod_pdv"].astype(str).str.strip().str.lstrip("0")
        for c in ("nome_pdv", "placa", "data_devol"):
            out[c] = out[c].astype(str).str.strip().replace("nan", "")
        out["valor"] = out["valor"].round(2); out["volume_hl"] = out["volume_hl"].round(3)
        out = out[cols_out]
    else:
        out = pd.DataFrame(columns=cols_out)
    try:
        antigo = ler_aba("entregas_frustradas")
        if not antigo.empty and "mes_referencia" in antigo.columns:
            antigo = antigo[~antigo["mes_referencia"].astype(str).isin(meses)]
            for c in cols_out:
                if c not in antigo.columns:
                    antigo[c] = ""
            out = pd.concat([antigo[cols_out], out], ignore_index=True)
    except Exception as e:
        print(f"  ⚠️ devoluções anteriores não lidas: {e}")
    sobrescrever_aba("entregas_frustradas", out.sort_values(["mes_referencia", "setor", "data"]))
    novos = out[out["mes_referencia"].astype(str).isin(meses)]
    res["devolucoes"] = f"{len(novos)} NFs devolvidas"
    atualizar_status_arquivo("Devoluções (Entregas Frustradas)", "✅ OK", f"CORA · {len(novos)} NFs · meses {', '.join(meses)}")

    # Ocorrências: códigos de motivo sem cadastro (p/ cadastrar no app)
    pend = dv[~dv["_cod_motivo"].isin(motivos.keys()) & (dv["_cod_motivo"] != "")] if not dv.empty else dv
    if not pend.empty:
        oc = (pend.groupby("_cod_motivo").agg(nfs=("Número NF", "nunique"), ultima=("_data", "max")).reset_index()
              .rename(columns={"_cod_motivo": "cod"}))
        oc["ultima"] = oc["ultima"].dt.strftime("%d/%m/%Y")
    else:
        oc = pd.DataFrame(columns=["cod", "nfs", "ultima"])
    sobrescrever_aba("motivos_devolucao_pendentes", oc)
    if len(oc):
        res["motivos_novos"] = f"⚠️ {len(oc)} código(s) de motivo sem cadastro: {', '.join(oc['cod'])}"

    # ── 1b) PAINEL DO DIA (igual à "Visão geral" do CORA) → cora_dia ─────────────────
    # TODOS os setores (inclui 997/995 = PDVs sem RN, que o painel do CORA conta) e TODAS as
    # situações do item (ATENDIDO, FALTA, ANULADO, AGUARDANDO_*), por dia (data = NF ou
    # entrega − 1, a mesma da HOP) × setor × PDV × produto. De 7 dias atrás em diante.
    # Volume Diário usa p/ os cards Dia/Operação/Atendidos/Ag./Faltas e as faltas por PDV/produto.
    # MEMÓRIA (Render free 512MB): nos blocos 1b–1d NÃO copiar o df (são ~140 colunas) —
    # trabalhar com o índice das linhas e ler só as colunas usadas (ts).
    def ts(col, idx):
        if col not in df.columns:
            return pd.Series("", index=idx)
        return df.loc[idx, col].astype(str).str.strip().replace("nan", "")

    try:
        ini = pd.Timestamp(hoje_brasilia()) - pd.Timedelta(days=7)   # 7 dias p/ trás + futuros (pedidos já marcados)
        ci = df.index[df["_data"] >= ini]
        dia_df = pd.DataFrame({
            "data": df.loc[ci, "_data"].dt.strftime("%d/%m/%Y").values,
            "setor": df.loc[ci, "_setor"].values,
            "cod_pdv": ts("Cód. cliente", ci).str.lstrip("0").values,
            "nome_pdv": ts("Nome cliente", ci).values,
            "cod_produto": ts("Cód. produto", ci).str.replace(r"\.0$", "", regex=True).str.lstrip("0").values,
            "nome_produto": ts("Desc. produto", ci).map(_txt).values,
            "situacao": df.loc[ci, "_atend_item"].replace("", "SEM_SITUACAO").values,
            "volume_hl": df.loc[ci, "_hl"].values,
        })
        dia_df = (dia_df.groupby(["data", "setor", "cod_pdv", "nome_pdv", "cod_produto", "nome_produto", "situacao"], as_index=False)
                  ["volume_hl"].sum())
        dia_df["volume_hl"] = dia_df["volume_hl"].round(3)
        sobrescrever_aba("cora_dia", dia_df)
        res["cora_dia"] = f"{len(dia_df)} linhas (painel do dia, {dia_df['data'].nunique()} dias)"
    except Exception as e:
        print(f"  ⚠️ cora_dia: {e}")

    # ── 1c) ANOMALIA DE VOLUME → anomalia_pedidos ────────────────────────────────────
    # Setores que NÃO podem ter pedido (102 = sem RN, 995/997 = PDVs sem RN): TODO item do mês
    # ATUAL desses setores, com o máximo de informação do pedido (zera todo mês: só o mês vivo).
    # Base do estudo "Anomalia de Volume" (Outros relatórios), que sugere RN e dia de visita.
    try:
        mes_vivo = hoje_brasilia().strftime("%Y-%m")
        ai = df.index[df["_setor"].isin(SETORES_ANOMALIA) & (df["_mes"] == mes_vivo)]
        an = df.loc[ai, ["_setor", "_hl", "_data", "_venda"]]
        cols_an = {
            "num_pedido": "Número pedido", "pedido_cliente": "Número pedido cliente", "id_pedido": "ID do pedido",
            "tipo_pedido": "Tipo pedido", "data_entrada": "Data entrada", "data_entrega": "Data entrega",
            "data_nf": "Data emissão NF", "nf": "Número NF", "situacao_pedido": "Situação pedido",
            "situacao_atend_pedido": "Situação atend. pedido", "situacao_item": "Situação item",
            "situacao_atend_item": "Situação atend. item", "canal_origem": "Canal origem", "canal_marcacao": "Canal marcação",
            "usuario_digitacao": "Usuário digitação", "motivo_digitacao": "Motivo digitação",
            "cod_operacao": "Cód. operação", "operacao": "Desc. operação", "tipo_movimento": "Desc. tipo movimento",
            "setor_cora": "Cód. setor", "desc_setor": "Desc. setor", "cod_vendedor": "Cód. vendedor", "vendedor": "Nome vendedor",
            "cnpj": "CPF/CNPJ cliente", "cod_pdv": "Cód. cliente", "nome_pdv": "Nome cliente", "nome_fantasia": "Nome fantasia",
            "municipio": "Desc. município", "forma_pagamento": "Forma de pagamento", "condicao_pagamento": "Condição de pagamento",
            "cod_produto": "Cód. produto", "nome_produto": "Desc. produto", "quantidade": "Quant. venda", "unidade": "Unidade venda",
            "valor_unitario": "Valor unitário líquido", "valor_item": "Valor líquido item", "volume_hl_txt": "Volume hectolitro",
            "itinerario": "Itinerário", "placa": "Placa", "valor_total_nf": "Valor total NF",
            "motivo_cancelamento": "Motivo cancelamento", "motivo_corte": "Motivo corte",
        }
        out_an = pd.DataFrame({k: ts(v, ai).map(_txt).values for k, v in cols_an.items()})
        out_an["setor"] = an["_setor"].values
        out_an["cod_pdv"] = out_an["cod_pdv"].str.replace(r"\.0$", "", regex=True).str.lstrip("0")
        out_an["cod_produto"] = out_an["cod_produto"].str.replace(r"\.0$", "", regex=True).str.lstrip("0")
        out_an["volume_hl"] = an["_hl"].round(4).values
        out_an["data"] = an["_data"].dt.strftime("%d/%m/%Y").values
        out_an["venda"] = an["_venda"].map({True: "SIM", False: "NÃO"}).values
        out_an["mes_referencia"] = mes_vivo
        out_an = out_an.drop(columns=["volume_hl_txt"])
        sobrescrever_aba("anomalia_pedidos", out_an)
        res["anomalia"] = f"{out_an['cod_pdv'].nunique()} PDVs · {out_an['num_pedido'].nunique()} pedidos em setores sem RN ({mes_vivo})"
    except Exception as e:
        print(f"  ⚠️ anomalia_pedidos: {e}")

    # ── 1d) RUPTURA POR PEDIDO → ruptura_pedidos + ruptura_pedidos_base ──────────────
    # Pedidos de VENDA (op 1) com algum item em FALTA. TOTAL = nenhum item atendido (o pedido
    # é cancelado/fica sem nada → o cliente não comprou nada naquele pedido); PARCIAL = faltou
    # parte. "recomprou" = o PDV teve item atendido em OUTRO pedido de venda: MESMO DIA (pedido
    # dividido — comprou o resto) · ATÉ 7 DIAS (voltou a comprar) · NÃO (venda perdida em 7 dias).
    # ruptura_pedidos_base = total de pedidos de venda por dia × setor (p/ o % de pedidos).
    try:
        vi = df.index[(df["_op"] == "1") & df["_setor"].isin(SETORES_VALIDOS)]
        vv = df.loc[vi, ["_setor", "_data", "_mes", "_sit_ped", "_atend_item", "_hl"]].copy()
        vv["_ped"] = ts("Número pedido", vi).str.replace(r"\.0$", "", regex=True)
        vv["_pdv"] = ts("Cód. cliente", vi).str.lstrip("0")
        vv["_falta"] = vv["_atend_item"] == "FALTA"
        vv["_atend"] = vv["_atend_item"] == "ATENDIDO"
        vv["_vl"] = _num_br(ts("Valor líquido item", vi))
        vv["_hlf"] = vv["_hl"].where(vv["_falta"], 0.0)
        vv["_vlf"] = vv["_vl"].where(vv["_falta"], 0.0)
        vv["_prod"] = ts("Desc. produto", vi).map(_txt)
        vv["_dstr"] = vv["_data"].dt.strftime("%d/%m/%Y")
        # (sem groupby.apply(include_groups=…): o Render roda pandas 2.1, que não tem esse parâmetro)
        vv["_nome"] = ts("Nome cliente", vi).map(_txt)
        vv["_pcli"] = ts("Número pedido cliente", vi).map(_txt)
        g = vv.groupby("_ped")
        ped = pd.DataFrame({
            "data": g["_dstr"].first(), "_dt": g["_data"].first(), "mes_referencia": g["_mes"].first(),
            "setor": g["_setor"].first(), "cod_pdv": g["_pdv"].first(),
            "nome_pdv": g["_nome"].first(),
            "pedido_cliente": g["_pcli"].first(),
            "situacao_pedido": g["_sit_ped"].first(),
            "itens": g.size(), "itens_falta": g["_falta"].sum(), "itens_atendidos": g["_atend"].sum(),
            "hl_pedido": g["_hl"].sum(), "hl_falta": g["_hlf"].sum(), "valor_falta": g["_vlf"].sum(),
        })
        base = ped.groupby(["mes_referencia", "data", "setor"]).size().reset_index(name="pedidos")
        rp = ped[ped["itens_falta"] > 0].copy()
        faltas = vv[vv["_falta"]].groupby("_ped")["_prod"].apply(lambda s: " | ".join(sorted(set(p for p in s if p))))
        rp["produtos_falta"] = faltas.reindex(rp.index).fillna("").str.slice(0, 400)
        rp["tipo"] = rp["itens_atendidos"].map(lambda n: "PARCIAL" if n > 0 else "TOTAL")
        # Recompra: outro pedido do mesmo PDV com item atendido, do dia do pedido até +7 dias
        comp = vv[vv["_atend"]].groupby(["_pdv", "_ped"])["_data"].min().reset_index()
        rec, dias = [], []
        for idx, r in rp.iterrows():
            c = comp[(comp["_pdv"] == r["cod_pdv"]) & (comp["_ped"] != idx) &
                     (comp["_data"] >= r["_dt"]) & (comp["_data"] <= r["_dt"] + pd.Timedelta(days=7))]
            d0 = int((c["_data"].min() - r["_dt"]).days) if len(c) else None
            rec.append("NÃO" if d0 is None else ("MESMO DIA" if d0 == 0 else "ATÉ 7 DIAS"))  # mesmo dia = pedido dividido
            dias.append(int((c["_data"].min() - r["_dt"]).days) if len(c) else "")
        rp["recomprou"], rp["dias_ate_recompra"] = rec, dias
        rp["num_pedido"] = rp.index
        for c in ("hl_pedido", "hl_falta"): rp[c] = rp[c].round(3)
        rp["valor_falta"] = rp["valor_falta"].round(2)
        rp = rp.drop(columns=["_dt"]).reset_index(drop=True)
        sobrescrever_por_mes("ruptura_pedidos", rp, "mes_referencia")
        sobrescrever_por_mes("ruptura_pedidos_base", base, "mes_referencia")
        tot = int((rp["tipo"] == "TOTAL").sum())
        res["ruptura_pedidos"] = f"{len(rp)} pedidos com falta · {tot} ruptura total · {len(rp) - tot} parcial"
        atualizar_status_arquivo("Ruptura por pedido (CORA)", "✅ OK", res["ruptura_pedidos"])
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ⚠️ ruptura_pedidos: {e}")
        atualizar_status_arquivo("Ruptura por pedido (CORA)", "❌ ERRO", f"{type(e).__name__}: {str(e)[:180]}")
    # libera os intermediários dos blocos 1b–1d antes de processar_pedidos (o passo mais pesado)
    dia_df = out_an = an = vv = g = ped = rp = comp = base = faltas = None
    import gc as _gc; _gc.collect()

    # ── 1) PEDIDOS → formato Promax 03014701 → processar_pedidos (todas as análises) ──
    dd = lambda s: s.dt.strftime("%d/%m/%Y").fillna("")
    fmt = lambda v: v.round(3).map(lambda x: f"{x:.3f}".replace(".", ","))
    # Alguns itens do CORA vêm com "Desc. produto" VAZIO → virava "nan" em todo lugar
    # (pedido_bees_itens, vendas, ruptura…). Completa pelo cadastro (produtos_full → base).
    nome_prod = t("Desc. produto").loc[df.index].map(_txt)
    if (nome_prod == "").any():
        nomes = {}
        for aba in ("produtos_base", "produtos_full"):
            try:
                pb = ler_aba(aba)
                for _, r in pb.iterrows():
                    c = str(r.get("cod", "")).strip().replace(".0", "").lstrip("0")
                    n = _txt(r.get("nome", ""))
                    if c and n:
                        nomes[c] = n
            except Exception as e:
                print(f"  ⚠️ {aba} não lida p/ nomes: {e}")
        cods = t("Cód. produto").loc[df.index].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.lstrip("0")
        vazio = nome_prod == ""
        nome_prod = nome_prod.where(~vazio, cods.map(lambda c: nomes.get(c, "")))
        print(f"  → {int(vazio.sum())} itens sem descrição no CORA; {int((vazio & (nome_prod != '')).sum())} completados pelo cadastro")
    ped = pd.DataFrame({
        "Setor":              df["_setor"],
        "Nome Setor":         t("Desc. setor").loc[df.index],
        "Data":               dd(df["_data"]),
        "Cliente":            t("Cód. cliente").loc[df.index],
        "Nome Cliente":       t("Nome cliente").loc[df.index],
        "Cod. Prod.":         t("Cód. produto").loc[df.index],
        "Nome Prod.":         nome_prod,
        "Volume Marcacao":    fmt(df["_hl"]),
        "Volume Entrega":     fmt(df["_hl"].where(df["_venda"], 0.0)),
        "Motivo":             df["_desc_motivo"].where(df["_devolvido"],
                                  pd.Series("Pedido Normal", index=df.index).where(df["_atend_item"] != "FALTA", "FALTA")),
        "Desc Tipo Movimento": t("Desc. tipo movimento").loc[df.index].str.upper(),
        "Nota Fiscal":        t("Número NF").loc[df.index],
        "Pedido Cliente":     t("Número pedido cliente").loc[df.index],
        "Pedido":             t("Número pedido").loc[df.index],
        "Nr. Pedido":         t("Número pedido").loc[df.index],
        "Mapa":               t("Itinerário").loc[df.index],
        "Entrega Real":       dd(d_ent.loc[df.index].where(df["_sit_ped"] == "ENTREGUE")),
    })
    buf = _io.StringIO(); ped.to_csv(buf, sep=";", index=False)
    processar_pedidos(buf.getvalue().encode("utf-8"), df_clientes)
    v = df[df["_venda"]]
    res["pedidos"] = f"{v['_hl'].sum():,.1f} HL vendidos · {v['Número pedido'].nunique()} pedidos · meses {', '.join(meses)}"

    # ── 3) FATURADOS (NF) → faturados_detalhe + faturado_nf ──────────────────────────
    nfok = (t("Número NF").loc[df.index] != "") & (df["_sit_ped"] != "FATURADO_NF_CANCELADA") & df["_setor"].isin(SETORES_VALIDOS)
    ft = df[nfok].copy()
    ft["_num_pedido"] = t("Número pedido").loc[ft.index].str.lstrip("0")
    ft["_cod_pdv"] = t("Cód. cliente").loc[ft.index].str.lstrip("0")
    ft["_nome_pdv"] = t("Nome cliente").loc[ft.index]
    base_f = ft[["_setor", "_cod_pdv", "_nome_pdv", "_num_pedido"]].drop_duplicates()
    sobrescrever_aba("faturados_detalhe", _montar_detalhe_pedidos(base_f[base_f["_num_pedido"] != ""]))
    if not ft.empty:
        ft["_nf"] = t("Número NF").loc[ft.index].str.lstrip("0")
        ft["_vnf"] = _num_br(t("Valor total NF").loc[ft.index])
        ft["_mapa"] = t("Itinerário").loc[ft.index]
        fnf = ft.groupby("_nf", as_index=False).agg(
            setor=("_setor", "first"), cod_pdv=("_cod_pdv", "first"), nome_pdv=("_nome_pdv", "first"),
            mapa=("_mapa", "first"), valor=("_vnf", "first"), itens=("_hl", "size")).rename(columns={"_nf": "nf"})
        fnf["valor"] = fnf["valor"].round(2)
        sobrescrever_aba("faturado_nf", fnf)
        res["faturados"] = f"{len(fnf)} NFs · R$ {fnf['valor'].sum():,.2f}"
    atualizar_status_arquivo("030237 (Faturados)", "✅ OK", f"CORA · {res.get('faturados', '0 NFs')}")

    # ── 4) FATURAMENTO MARKETPLACE → rv_faturamento_mktp (R$ sem ADF, por setor × mês) ──
    df_prods = ler_aba("produtos_base")
    mktp = set()
    if not df_prods.empty and "cod" in df_prods.columns:
        for _, r in df_prods.iterrows():
            c = str(r.get("cod", "")).strip().lstrip("0")
            if c and "MKTP" in str(r.get("categorias", "")).upper():
                mktp.add(c)
    vm = df[df["_venda"] & df["_setor"].isin(SETORES_VALIDOS)].copy()
    vm["_cod_prod"] = t("Cód. produto").loc[vm.index].str.lstrip("0")
    vm = vm[vm["_cod_prod"].isin(mktp)]
    vm["_rs"] = _num_br(t("Valor sem ADF").loc[vm.index])
    fm = (vm.groupby(["_setor", "_mes"])["_rs"].sum().reset_index()
            .rename(columns={"_setor": "setor", "_mes": "mes_referencia", "_rs": "faturamento_mktp_real"}))
    # garante linha (zerada) p/ todo mês coberto — substitui o mês mesmo sem venda Mktp
    if fm.empty:
        fm = pd.DataFrame({"setor": ["101"] * len(meses), "mes_referencia": meses, "faturamento_mktp_real": [0.0] * len(meses)})
    fm["faturamento_mktp_real"] = fm["faturamento_mktp_real"].round(2)
    sobrescrever_por_mes("rv_faturamento_mktp", fm[["setor", "faturamento_mktp_real", "mes_referencia"]], "mes_referencia")
    res["faturamento_mktp"] = f"R$ {fm['faturamento_mktp_real'].sum():,.2f} ({len(mktp)} produtos MKTP)"
    atualizar_status_arquivo("030509 (Faturamento Mktp)", "✅ OK", f"CORA · {res['faturamento_mktp']}")

    # ── 5) BUFFER + DECK (rotina existente; lê o mesmo arquivo) ──────────────────────
    d_bd = processar_cora(conteudo_bytes)
    res["buffer_deck"] = f"buffer {d_bd['num_pedido'].nunique() if len(d_bd) else 0} pedidos"
    atualizar_status_arquivo("CORA (Consulta-pedidos completo)", "✅ OK", " · ".join(f"{k}: {v}" for k, v in res.items()))
    return res



def processar_coleta(conteudo_bytes):
    """Planilha de coleta (xlsx). Gera dois snapshots:
      • shelf            — guia 'FAROL PZC' (produtos perto do vencimento p/ a aba Shelf).
      • deck_vencimento  — guia 'BASE' (menor validade por código = 1º Vencimento do Deck)."""
    print("📂 Processando Coleta (Shelf + Vencimento)...")
    buf = io.BytesIO(conteudo_bytes)
    xls = pd.ExcelFile(buf, engine="openpyxl")

    def achar_aba(alvo):
        for s in xls.sheet_names:
            if _norm_cabecalho(s) == _norm_cabecalho(alvo):
                return s
        return None

    # ── FAROL PZC → shelf ────────────────────────────────────────────────────
    aba_pzc = achar_aba("FAROL PZC")
    n_shelf = 0
    if aba_pzc:
        # Cabeçalho na 5ª linha (CODIGO | DESCRICAO | QTD CX | VALIDADE | DIAS PRA VENCER | VALOR SHELF)
        dfp = pd.read_excel(buf, engine="openpyxl", sheet_name=aba_pzc, skiprows=4, dtype=str)
        dfp.columns = [_norm_cabecalho(c) for c in dfp.columns]
        col = {c: c for c in dfp.columns}
        gc_ = lambda *names: next((col[_norm_cabecalho(n)] for n in names if _norm_cabecalho(n) in col), None)
        c_cod, c_desc = gc_("codigo"), gc_("descricao")
        c_qtd, c_val = gc_("qtd cx"), gc_("validade")
        c_dias, c_valor = gc_("dias pra vencer"), gc_("valor shelf")
        if c_cod:
            dfp = dfp[dfp[c_cod].notna() & (dfp[c_cod].astype(str).str.strip() != "")]
            val = pd.to_datetime(dfp[c_val], errors="coerce") if c_val else pd.Series([pd.NaT] * len(dfp))
            shelf = pd.DataFrame({
                "cod_produto": dfp[c_cod].astype(str).str.strip().str.lstrip("0"),
                "descricao": (dfp[c_desc].astype(str).str.strip() if c_desc else ""),
                "qtd_cx": (pd.to_numeric(dfp[c_qtd], errors="coerce").fillna(0).round(0).astype(int) if c_qtd else 0),
                "validade": val.dt.strftime("%d/%m/%Y").fillna(""),
                "dias_vencer": (pd.to_numeric(dfp[c_dias], errors="coerce").fillna(0).round(0).astype(int) if c_dias else 0),
                "valor_shelf": (pd.to_numeric(dfp[c_valor], errors="coerce").fillna(0).round(2) if c_valor else 0.0),
            })
            shelf = shelf[shelf["cod_produto"] != ""].sort_values("dias_vencer")
            sobrescrever_aba("shelf", shelf)
            n_shelf = len(shelf)
    print(f"  ✅ shelf: {n_shelf} itens")

    # ── BASE → deck_vencimento (menor validade por código) ───────────────────
    aba_base = achar_aba("BASE")
    n_venc = 0
    if aba_base:
        dfb = pd.read_excel(buf, engine="openpyxl", sheet_name=aba_base, dtype=str)
        dfb.columns = [_norm_cabecalho(c) for c in dfb.columns]
        cb = lambda *names: next((c for c in dfb.columns if c in {_norm_cabecalho(n) for n in names}), None)
        c_cod, c_val = cb("cod"), cb("validade")
        if c_cod and c_val:
            dfb = dfb[dfb[c_cod].notna() & (dfb[c_cod].astype(str).str.strip() != "")].copy()
            dfb["_cod"] = dfb[c_cod].astype(str).str.strip().str.lstrip("0")
            dfb["_val"] = pd.to_datetime(dfb[c_val], errors="coerce")
            dfb = dfb[dfb["_val"].notna()]
            venc = dfb.groupby("_cod", as_index=False)["_val"].min()
            deck_v = pd.DataFrame({
                "cod_produto": venc["_cod"],
                "validade": venc["_val"].dt.strftime("%d/%m/%Y"),
            })
            sobrescrever_aba("deck_vencimento", deck_v)
            n_venc = len(deck_v)
    print(f"  ✅ deck_vencimento: {n_venc} produtos")

    atualizar_status_arquivo("Coleta (Shelf/Vencimento)", "✅ OK",
                             f"shelf {n_shelf} · vencimento {n_venc}")
    return {"shelf": n_shelf, "vencimento": n_venc}



def processar_pedidos_historico(conteudo_bytes):
    """Import HISTÓRICO de pedidos (meses antigos, ex.: 2025). Popula SÓ as tabelas que
    ACUMULAM por mês — vendas_cliente_produto, vd_pdv, vd_produto, rv_volume — e NÃO toca
    nos snapshots do mês corrente (cobertura, pdv_mix, rank_clientes, entregas_efetivadas,
    ruptura_detalhe, volume_diario). Cada mês substitui só o seu mês. Leve de memória."""
    import gc
    print("📂 Import HISTÓRICO de pedidos (só tabelas mensais)...")
    COLS_USADAS = {"Setor", "Data", "Cliente", "Nome Cliente", "Cod. Prod.",
                   "Nome Prod.", "Volume Entrega", "Volume Marcacao", "Motivo"}
    df = ler_csv_inf(conteudo_bytes, usecols=lambda c: c.strip() in COLS_USADAS)
    df.columns = [c.strip() for c in df.columns]
    df["_setor"] = df["Setor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    df["_data"] = pd.to_datetime(df["Data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    df = df[df["_data"].notna()]
    if df.empty:
        atualizar_status_arquivo("Pedidos (histórico)", "⚠️ vazio", "nenhuma linha válida")
        return {"linhas": 0, "meses": []}

    df_prods_base = ler_aba("produtos_base")
    mapa_produtos = carregar_base_produtos(df_prods_base) if not df_prods_base.empty else {}
    cod_norm = df["Cod. Prod."].astype(str).str.strip().str.lstrip("0").replace("", "0")
    df["_categorias"] = cod_norm.map(mapa_produtos)
    df["_categorias"] = df["_categorias"].apply(lambda x: x if isinstance(x, list) else [])
    df["_categoria"]  = df["_categorias"].apply(lambda x: x[0] if x else None)
    del cod_norm; gc.collect()

    df["_volume"] = pd.to_numeric(
        df["Volume Entrega"].astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce").fillna(0.0)
    df["_volume_marcacao"] = pd.to_numeric(
        df.get("Volume Marcacao", pd.Series(index=df.index, dtype=str)).astype(str)
          .str.replace(",", ".", regex=False).str.strip(), errors="coerce").fillna(0.0)
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0").str.strip()
    gc.collect()

    _processar_vendas_cliente(df); gc.collect()   # vendas_cliente_produto (por mês)
    _processar_vendas_diaria(df);  gc.collect()   # vd_pdv / vd_produto (por mês)
    processar_volume_rv(df);       gc.collect()   # rv_volume (por mês)
    # Ruptura (faltas) dos meses do PROMAX — só meses antes da virada p/ o CORA (os do CORA
    # vêm do import do CORA e não podem ser sobrescritos por um arquivo antigo).
    if "Motivo" in df.columns:
        try:
            _hist = df[df["_data"].dt.strftime("%Y-%m") < CORA_INICIO]
            if not _hist.empty:
                _processar_ruptura(_hist)
        except Exception as e:
            print(f"  ⚠️ ruptura (histórico): {e}")
        gc.collect()

    # Registra SKUs novos (dos arquivos históricos) na produtos_base, preservando as
    # categorias já cadastradas — os sem categoria caem em "Produtos sem categoria"
    # pra você preencher; ao reimportar, o volume deles passa a somar na categoria.
    try:
        _registrar_sem_categoria(df, mapa_produtos); gc.collect()
    except Exception as _e:
        print(f"  ⚠️ sem_categoria (histórico): {_e}")

    meses = sorted(df["_data"].dt.strftime("%Y-%m").unique().tolist())
    n = len(df)
    del df; gc.collect()
    atualizar_status_arquivo("Pedidos (histórico)", "✅ OK", f"{n} linhas · meses {','.join(meses)}")
    print(f"  ✅ Histórico: {n} linhas · meses {meses}")
    return {"linhas": n, "meses": meses}


__all__ = [n for n in dir() if not n.startswith("__")]
