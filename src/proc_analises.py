"""Processador HOP — análises derivadas dos pedidos (vendas, entregas, ruptura, notas, cobertura, mix, rank, volume diário).
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403



def _processar_vendas_diaria(df):
    """Volume diário por PDV e por produto (setor x cod x DIA), acumulado mês a mês.
    Base da comparação D-1 acumulada no dashboard (dia 01..D-1 deste mês vs mês anterior).
    Usa Volume Entrega (dias passados = entregue). Grava vd_pdv e vd_produto."""
    d = df[df["_data"].notna()]
    if d.empty:
        return
    dia = d["_data"].dt.strftime("%Y-%m-%d")
    mes = d["_data"].dt.strftime("%Y-%m")
    base = pd.DataFrame({
        "setor": d["_setor"].values,
        "cod_pdv": d["_cod_pdv"].values,
        "nome_pdv": d["Nome Cliente"].astype(str).str.strip().values,
        "cod_produto": d["Cod. Prod."].astype(str).str.strip().str.lstrip("0").values,
        "nome_produto": d["Nome Prod."].astype(str).str.strip().values,
        "data": dia.values,
        "mes_referencia": mes.values,
        "volume_hl": pd.to_numeric(d["_volume"], errors="coerce").fillna(0.0).values,
    })
    vp = base.groupby(["setor", "cod_pdv", "nome_pdv", "data", "mes_referencia"], as_index=False)["volume_hl"].sum()
    vp["volume_hl"] = vp["volume_hl"].round(3)
    sobrescrever_por_mes("vd_pdv", vp, "mes_referencia")
    vq = base.groupby(["setor", "cod_produto", "nome_produto", "data", "mes_referencia"], as_index=False)["volume_hl"].sum()
    vq["volume_hl"] = vq["volume_hl"].round(3)
    sobrescrever_por_mes("vd_produto", vq, "mes_referencia")
    print(f"  📅 vd_pdv: {len(vp)} · vd_produto: {len(vq)} linhas (volume diário)")



def _processar_vendas_cliente(df_mes):
    """Vendas do mês por setor x cliente x produto. Base da assistente Hop e da
    Cobertura & Distribuição por SKU.
    Regra do DIA ATUAL: hoje usa Volume Marcação (ainda não há faturamento);
    dias anteriores usam Volume Entrega — mesma lógica do Volume Diário."""
    cols_vazio = ["setor", "cod_pdv", "nome_pdv", "cod_produto", "nome_produto", "volume_hl", "mes_referencia"]
    hoje_str = hoje_brasilia().strftime("%d/%m/%Y")
    # MEMÓRIA (Render free 512MB): copia SÓ as colunas usadas — o df completo tem
    # _categorias (listas Python, pesadas) e mais ~10 colunas que não entram aqui.
    _COLS = ["_data", "_setor", "_cod_pdv", "Nome Cliente", "Cod. Prod.", "Nome Prod.",
             "_volume", "_volume_marcacao"]
    d = df_mes[_COLS].copy()
    d["_data_str"] = d["_data"].dt.strftime("%d/%m/%Y")
    eh_hoje = d["_data_str"] == hoje_str
    d["_vol_efetivo"] = d["_volume"]
    d.loc[eh_hoje, "_vol_efetivo"] = d.loc[eh_hoje, "_volume_marcacao"]

    df_v = d[d["_vol_efetivo"] > 0].copy()
    if df_v.empty:
        return
    # Import mensal: cada linha recebe o mês REAL do dado (não "hoje"), p/ acumular
    # por mês no SQL. Agrupa por setor x pdv x produto x MÊS.
    df_v["_mes"] = df_v["_data"].dt.strftime("%Y-%m")
    g = (
        df_v.groupby(["_setor", "_cod_pdv", "Nome Cliente", "Cod. Prod.", "Nome Prod.", "_mes"])["_vol_efetivo"]
        .sum().reset_index()
        .rename(columns={
            "_setor": "setor", "_cod_pdv": "cod_pdv", "Nome Cliente": "nome_pdv",
            "Cod. Prod.": "cod_produto", "Nome Prod.": "nome_produto",
            "_vol_efetivo": "volume_hl", "_mes": "mes_referencia",
        })
        .sort_values(["mes_referencia", "setor", "volume_hl"], ascending=[True, True, False])
    )
    g["volume_hl"] = g["volume_hl"].round(3)
    # Acumula: substitui só os meses presentes neste arquivo, mantém os demais.
    sobrescrever_por_mes("vendas_cliente_produto", g, "mes_referencia")
    print(f"  🤖 vendas_cliente_produto (Hop): {len(g)} linhas, meses {sorted(g['mes_referencia'].unique())}")



def _processar_entregas_efetivadas(df):
    """Volume entregue (efetivado) por setor x mês — denominador da taxa de
    frustração no Detalhamento HOP › Entrega. Usa Volume Entrega (faturado).
    Cobre todo o quadrimestre do arquivo para permitir análise por mês."""
    cols = ["setor", "mes_referencia", "volume_entregue_hl", "linhas"]
    df_v = df[df["_volume"] > 0].copy()
    if df_v.empty:
        sobrescrever_aba("entregas_efetivadas", pd.DataFrame(columns=cols))
        return
    df_v["_mes"] = df_v["_data"].dt.strftime("%Y-%m")
    g = (
        df_v.groupby(["_setor", "_mes"])
        .agg(volume_entregue_hl=("_volume", "sum"), linhas=("_volume", "size"))
        .reset_index()
        .rename(columns={"_setor": "setor", "_mes": "mes_referencia"})
        .sort_values(["mes_referencia", "setor"])
    )
    g["volume_entregue_hl"] = g["volume_entregue_hl"].round(3)
    sobrescrever_aba("entregas_efetivadas", g[cols])
    print(f"  📦 entregas_efetivadas: {len(g)} (setor x mês)")



def _processar_ruptura(df):
    """Ruptura de estoque: produtos com falta (Motivo contém FALTA).
    Volume em ruptura = Volume Marcacao (o que foi pedido mas não entregue).
    Gera UM detalhe por ocorrência (cliente x produto x dia) cobrindo todo o
    quadrimestre — o frontend agrega/filtra/faz drill-down a partir daí:
      • ruptura_detalhe — setor, cod_pdv, nome_pdv, cod_produto, nome_produto,
                          categoria, data, mes, qtd_faltas, volume_falta_hl
    """
    cols = ["setor", "cod_pdv", "nome_pdv", "cod_produto", "nome_produto",
            "categoria", "data", "mes", "qtd_faltas", "volume_falta_hl"]

    df_falta = df[df["Motivo"].astype(str).str.upper().str.contains("FALTA", na=False)].copy()
    if df_falta.empty:
        # acumula por mês: arquivo sem falta NÃO apaga os meses já gravados
        print("  📉 ruptura: nenhuma falta registrada")
        return

    # Volume em ruptura: usa marcação; se zerada, cai para volume entrega (>0)
    df_falta["_vol_ruptura"] = df_falta["_volume_marcacao"]
    sem_marc = df_falta["_vol_ruptura"] <= 0
    df_falta.loc[sem_marc, "_vol_ruptura"] = df_falta.loc[sem_marc, "_volume"].abs()
    df_falta = df_falta[df_falta["_vol_ruptura"] > 0].copy()
    df_falta["_mes"] = df_falta["_data"].dt.strftime("%Y-%m")
    df_falta["_data_str"] = df_falta["_data"].dt.strftime("%d/%m/%Y")
    df_falta["_categoria_str"] = df_falta["_categoria"].fillna("").astype(str)

    det = (
        df_falta.groupby(["_setor", "_cod_pdv", "Nome Cliente", "Cod. Prod.",
                          "Nome Prod.", "_categoria_str", "_data_str", "_mes"])
        .agg(qtd_faltas=("_vol_ruptura", "size"), volume_falta_hl=("_vol_ruptura", "sum"))
        .reset_index()
        .rename(columns={
            "_setor": "setor", "_cod_pdv": "cod_pdv", "Nome Cliente": "nome_pdv",
            "Cod. Prod.": "cod_produto", "Nome Prod.": "nome_produto",
            "_categoria_str": "categoria", "_data_str": "data", "_mes": "mes",
        })
        .sort_values(["mes", "volume_falta_hl"], ascending=[True, False])
    )
    det["volume_falta_hl"] = det["volume_falta_hl"].round(3)
    # ACUMULA POR MÊS (antes sobrescrevia a tabela inteira → o CORA, que só traz out/26 em
    # diante, apagava os meses do Promax e o quadrimestre ficava só com outubro).
    sobrescrever_por_mes("ruptura_detalhe", det[cols], "mes")
    print(f"  📉 ruptura_detalhe: {len(det)} ocorrências · meses {sorted(det['mes'].unique())}")



def _processar_nota_itens(df):
    """Itens (produtos) das notas frustradas — para abrir o pedido ao clicar
    numa linha do Detalhe da aba Entrega. Escopo: só as notas que aparecem em
    entregas_frustradas (mantém a aba pequena). Casa devolução.Nota com a
    coluna 'Nota Fiscal' dos pedidos.
    """
    cols = ["nota", "cod_produto", "nome_produto", "volume_marcacao_hl", "volume_entrega_hl"]
    try:
        fr = ler_aba("entregas_frustradas")
    except Exception:
        fr = pd.DataFrame()
    if fr.empty or "nota" not in fr.columns or "Nota Fiscal" not in df.columns:
        sobrescrever_aba("nota_itens", pd.DataFrame(columns=cols))
        print("  🧾 nota_itens: sem notas frustradas (aba vazia)")
        return

    notas = set(fr["nota"].astype(str).str.strip().str.lstrip("0"))
    d = df.copy()
    d["_nf"] = d["Nota Fiscal"].astype(str).str.strip().str.lstrip("0")
    d = d[d["_nf"].isin(notas) & (d["_nf"] != "")]
    if d.empty:
        sobrescrever_aba("nota_itens", pd.DataFrame(columns=cols))
        print("  🧾 nota_itens: nenhuma nota casou com os pedidos")
        return

    g = (
        d.groupby(["_nf", "Cod. Prod.", "Nome Prod."])
        .agg(volume_marcacao_hl=("_volume_marcacao", "sum"), volume_entrega_hl=("_volume", "sum"))
        .reset_index()
        .rename(columns={"_nf": "nota", "Cod. Prod.": "cod_produto", "Nome Prod.": "nome_produto"})
        .sort_values(["nota", "volume_marcacao_hl"], ascending=[True, False])
    )
    g["volume_marcacao_hl"] = g["volume_marcacao_hl"].round(3)
    g["volume_entrega_hl"] = g["volume_entrega_hl"].round(3)
    sobrescrever_aba("nota_itens", g[cols])
    print(f"  🧾 nota_itens: {g['nota'].nunique()} notas · {len(g)} itens")



def _processar_cobertura(df, df_clientes, hoje):
    """Gera cobertura por PDV x Categoria olhando o mês ATUAL (M) e os 3 meses ANTERIORES
    corridos (M-3, M-2, M-1) — v3.109 (antes: trimestre civil, que no 1º mês do tri não tinha
    mês anterior e jogava todo mundo em NOK):
      OK   = comprou no mês atual.
      EST  = estável: comprou nos 3 meses anteriores (em sequência), mas não no atual.
      PEND = pendente: comprou no mês passado (M-1), mas não no atual.
      IST  = instável: comprou em algum dos 3 anteriores, mas não no mês passado — ou comprou
             com buraco (mês sim, mês não: M-3 sim · M-2 não · M-1 sim).
      NOK  = não comprou em nenhum dos 3 meses anteriores nem no atual.
    Os meses anteriores vêm de vendas_cliente_produto (acumula por mês — o CORA só traz o mês
    atual); o mês atual (e o que mais vier no arquivo) vem do próprio `df`."""
    cats = list(CATEGORIAS_VALIDAS)
    _codn = lambda x: (str(x).strip().lstrip("0") or "0")

    mes_atual_d = date(hoje.year, hoje.month, 1)
    def _mes_menos(k):
        y, m = hoje.year, hoje.month - k
        while m <= 0:
            m += 12; y -= 1
        return date(y, m, 1)
    m3, m2, m1 = _mes_menos(3), _mes_menos(2), _mes_menos(1)
    janela = {m3, m2, m1, mes_atual_d}

    from collections import defaultdict
    bought = defaultdict(set)   # (cod_pdv, cat) → meses (1º dia) com compra (volume > 0)

    # 1) do arquivo (mês atual; arquivos do Promax trazem ~4 meses)
    _m1s = df["_data"].dt.to_period("M").dt.to_timestamp().dt.date
    dft = df[_m1s.isin(janela) & (df["_volume"] > 0)]
    for cod_raw, cats_row, mref in zip(dft["_cod_pdv"], dft["_categorias"], _m1s[dft.index]):
        cod = _codn(cod_raw)
        for cat in (cats_row or []):
            bought[(cod, cat)].add(mref)

    # 2) meses anteriores do histórico acumulado (vendas_cliente_produto) × categorias da base
    try:
        meses_ant = [m.strftime("%Y-%m") for m in (m3, m2, m1)]
        hist = ler_aba_meses("vendas_cliente_produto", "mes_referencia", meses_ant,
                             ["cod_pdv", "cod_produto", "mes_referencia", "volume_hl"])
        if not hist.empty:
            base_p = ler_aba("produtos_base")
            mapa = carregar_base_produtos(base_p) if not base_p.empty else {}
            hist = hist[pd.to_numeric(hist["volume_hl"], errors="coerce").fillna(0) > 0]
            for cod_raw, prod, mes in zip(hist["cod_pdv"], hist["cod_produto"], hist["mes_referencia"]):
                cats_p = mapa.get(str(prod).strip().replace(".0", "").lstrip("0") or "0") or []
                if not cats_p:
                    continue
                y, mo = str(mes)[:7].split("-")
                md = date(int(y), int(mo), 1)
                cod = _codn(cod_raw)
                for cat in cats_p:
                    bought[(cod, cat)].add(md)
            print(f"  📚 cobertura: histórico {meses_ant} — {len(hist)} linhas")
    except Exception as e:
        print(f"  ⚠️ cobertura: histórico dos meses anteriores não lido ({e}) — só o arquivo")

    # Base de PDVs vem da base de clientes (fallback: os PDVs do tri).
    if df_clientes is not None and not df_clientes.empty:
        pdvs = df_clientes[["cod_pdv", "nome_fantasia", "setor"]].drop_duplicates()
    else:
        pdvs = dft[["_cod_pdv", "_setor", "Nome Cliente"]].drop_duplicates(subset=["_cod_pdv"]) \
                  .rename(columns={"_cod_pdv": "cod_pdv", "_setor": "setor", "Nome Cliente": "nome_fantasia"})

    def status_de(cod, cat):
        bm = bought.get((cod, cat), ())
        if mes_atual_d in bm:
            return "OK"
        b3, b2, b1 = m3 in bm, m2 in bm, m1 in bm
        if not (b3 or b2 or b1):
            return "NOK"                      # nada nos 3 anteriores nem no atual
        if b3 and b2 and b1:
            return "EST"                      # comprou em sequência nos 3 anteriores
        if b1 and not (b3 and not b2):
            return "PEND"                     # comprou no mês passado (sem buraco antes)
        return "IST"                          # não comprou no mês passado, ou mês sim/mês não

    linhas = []
    for _, pdv in pdvs.iterrows():
        cod = _codn(pdv["cod_pdv"])
        setor = str(pdv.get("setor", pdv.get("_setor", ""))).strip()
        nome = str(pdv.get("nome_fantasia", "")).strip()
        for cat in cats:
            linhas.append({
                "setor": setor,
                "cod_pdv": cod,
                "nome_fantasia": nome,
                "categoria": cat,
                "status": status_de(cod, cat),
                "mes_referencia": mes_atual_d.strftime("%Y-%m"),
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



def _processar_volume_diario(df):
    """Agrega volume por data x setor x produto (para Volume Diário).

    Regra do DIA ATUAL: no dia de hoje ainda não há faturamento (Volume Entrega
    vem zerado), então usamos o Volume Marcacao (col AI) — o que está sendo
    marcado/pedido — para o RN já enxergar o volume do dia. Dias anteriores
    continuam usando Volume Entrega (faturado/entregue), que é o correto.
    No dia seguinte o que foi faturado já entra no Volume Entrega e o racional
    "pula" para o novo dia atual.
    """
    hoje_str = hoje_brasilia().strftime("%d/%m/%Y")
    df_d = df.copy()
    df_d["_data_str"] = df_d["_data"].dt.strftime("%d/%m/%Y")

    # Volume efetivo: marcação só no dia de hoje, entrega nos demais dias
    eh_hoje = df_d["_data_str"] == hoje_str
    df_d["_vol_efetivo"] = df_d["_volume"]
    df_d.loc[eh_hoje, "_vol_efetivo"] = df_d.loc[eh_hoje, "_volume_marcacao"]

    df_v = df_d[df_d["_vol_efetivo"] > 0]
    vol = (
        df_v.groupby(["_data_str", "_setor", "Cod. Prod.", "Nome Prod."])
        ["_vol_efetivo"].sum()
        .reset_index()
        .rename(columns={
            "_data_str":   "data",
            "_setor":      "setor",
            "Cod. Prod.":  "cod_produto",
            "Nome Prod.":  "nome_produto",
            "_vol_efetivo":"volume_hl",
        })
    )
    vol["volume_hl"] = vol["volume_hl"].round(3)
    sobrescrever_aba("volume_diario", vol)
    print(f"  📊 volume_diario: {len(vol)} linhas (dia atual {hoje_str} via marcação)")


__all__ = [n for n in dir() if not n.startswith("__")]
