"""Processador HOP — clientes (0105070402), pedidos (03014701), faturados, buffer e CORA buffer.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403
from proc_analises import _processar_cobertura, _processar_entregas_efetivadas, _processar_mix, _processar_nota_itens, _processar_rank, _processar_ruptura, _processar_vendas_cliente, _processar_vendas_diaria, _processar_volume_diario
from proc_cadastros import _registrar_sem_categoria
from proc_rv import processar_volume_rv



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

    # PDVs dos setores que não podem ter pedido (102/995/997) — qualquer status — guardados à
    # parte p/ o estudo "Anomalia de Volume" (cidade/bairro p/ sugerir RN e dia de visita).
    try:
        fz = df[df["_setor"].isin(SETORES_ANOMALIA)]
        _cb = next((c for c in df.columns if c.upper() == "BAIRRO" or c.upper().startswith("BAIRRO ")), None)
        _ce = next((c for c in df.columns if c.upper().startswith(("ENDEREÇO", "ENDERECO", "LOGRADOURO"))), None)
        sobrescrever_aba("pdv_fora", pd.DataFrame({
            "cod_pdv": fz["Cód PDV"].astype(str).str.strip().str.lstrip("0").values,
            "nome_fantasia": fz["Nome Fantasia"].map(_txt).values if "Nome Fantasia" in fz.columns else "",
            "razao_social": fz["Razão Social"].map(_txt).values if "Razão Social" in fz.columns else "",
            "setor": fz["_setor"].values,
            "status": fz["_status"].values,
            "cidade": fz["Cidade"].map(_txt).values if "Cidade" in fz.columns else "",
            "bairro": fz[_cb].map(_txt).values if _cb else "",
            "endereco": fz[_ce].map(_txt).values if _ce else "",
            "segmento": fz["Segmento NGE"].map(_txt).values if "Segmento NGE" in fz.columns else "",
            "dia_visita": fz["Dia de Visita do VDE"].map(normalizar_dia_visita).values if "Dia de Visita do VDE" in fz.columns else "",
            "ultima_compra": fz["Data da Última Compra"].map(_txt).values if "Data da Última Compra" in fz.columns else "",
        }))
        print(f"  → pdv_fora (102/995/997): {len(fz)} PDVs")
    except Exception as e:
        print(f"  ⚠️ pdv_fora: {e}")

    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    df = df[df["_status"] == "ATIVO"]

    print(f"  → {len(df)} PDVs ativos nos setores válidos")

    hoje_idx = dia_semana_hoje()

    # Bairro / Categoria do PDV (p/ o relatório "Base de PDVs × dia de visita"): acha a
    # coluna pelo nome (o layout do Promax varia) — se não existir, fica vazio.
    def _col(*chaves):
        for c in df.columns:
            u = c.upper()
            if any(u == k or u.startswith(k + " ") for k in chaves):
                return c
        return None
    col_bairro = _col("BAIRRO")
    col_categoria = _col("CATEGORIA")
    print(f"  → bairro: {col_bairro or '—'} · categoria: {col_categoria or '—'}")

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
            "bairro": _txt(row.get(col_bairro, "")) if col_bairro else "",
            "categoria": _txt(row.get(col_categoria, "")) if col_categoria else "",
        })

    df_base = pd.DataFrame(resultados)

    # ── Aba pdv_base ─────────────────────────────────────────────────────────
    sobrescrever_aba("pdv_base", df_base)

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
    print(f"  ✅ Clientes processados: {len(df_base)} PDVs")
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
    # ── Memória: lê SÓ as 9 colunas usadas (o CSV do Promax tem ~71). ──────────
    # Isso derruba o pico de memória de ~255MB para ~35MB — essencial no Render free.
    COLS_USADAS = {"Setor", "Data", "Cliente", "Nome Cliente", "Cod. Prod.",
                   "Nome Prod.", "Volume Entrega", "Volume Marcacao", "Motivo",
                   "Qtde Entrega", "Qtde Marcacao",   # só no CORA (itens com 0 HL: balas, Halls…)
                   "Operacao", "HL Atendido",          # só no CORA (rastreio da Pesquisa Pedido)
                   "Desc Tipo Movimento", "Nota Fiscal",
                   # rastreabilidade do pedido BEES (Pesquisa Pedido)
                   "Pedido Cliente", "Pedido", "Nome Setor", "Mapa", "Entrega Real"}
    # Candidatos para o nº do pedido (col Z) — usado p/ cruzar Faturados x Buffer.
    # O nome exato no relatório varia; pegamos o primeiro que existir.
    PEDIDO_CANDIDATOS = ["Nr. Pedido", "Num Pedido", "Nr Pedido", "Numero Pedido",
                         "Número Pedido", "Pedido", "Cod. Pedido", "Nr. Ped."]
    df = ler_csv_inf(conteudo_bytes,
                     usecols=lambda c: c.strip() in COLS_USADAS or c.strip() in PEDIDO_CANDIDATOS)
    df.columns = [c.strip() for c in df.columns]
    gc.collect()
    _col_pedido = next((c for c in PEDIDO_CANDIDATOS if c in df.columns), None)
    print(f"  🔑 Coluna do nº do pedido: {_col_pedido or 'NÃO ENCONTRADA (cruzamento Faturados x Buffer ficará vazio)'}")

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
    # Nome vazio (CORA sem "Desc. produto") NÃO pode virar o texto "nan": limpa e, se
    # faltar, completa com o nome da base de produtos.
    df["Nome Prod."] = df["Nome Prod."].map(_txt)
    if (df["Nome Prod."] == "").any() and not df_prods_base.empty and "nome" in df_prods_base.columns:
        _nomes_base = {str(c).strip().replace(".0", "").lstrip("0"): _txt(n)
                       for c, n in zip(df_prods_base["cod"], df_prods_base["nome"]) if _txt(n)}
        _v = df["Nome Prod."] == ""
        df.loc[_v, "Nome Prod."] = cod_norm[_v].map(lambda c: _nomes_base.get(c, "")).values
    del cod_norm
    gc.collect()

    # Normalizar volume (Volume Entrega = faturado/entregue — base padrão)
    df["_volume"] = pd.to_numeric(
        df["Volume Entrega"].astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce"
    ).fillna(0.0)

    # Volume Marcacao (col AI) = o que foi pedido/marcado. Usado para:
    #  • Volume Diário do DIA ATUAL (ainda não há faturamento/entrega)
    #  • Ruptura de estoque (volume marcado que não foi entregue por falta)
    df["_volume_marcacao"] = pd.to_numeric(
        df.get("Volume Marcacao", pd.Series(index=df.index, dtype=str))
          .astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce"
    ).fillna(0.0)

    # Quantidade vendida (só o CORA manda). Mantém em vendas_cliente_produto os itens de
    # marketplace que pesam 0,00 HL (bala, pirulito, Halls…) — senão nunca contam como
    # distribuição/cobertura. Promax: colunas ausentes → 0 (comportamento antigo).
    for _c, _dst in (("Qtde Entrega", "_qtd"), ("Qtde Marcacao", "_qtd_marcacao")):
        df[_dst] = (pd.to_numeric(df[_c].astype(str).str.replace(",", ".", regex=False).str.strip(), errors="coerce")
                    .fillna(0.0) if _c in df.columns else 0.0)

    # Normalizar cod cliente
    df["_cod_pdv"] = df["Cliente"].str.strip().str.lstrip("0").str.strip()

    # ── Chave de pedido (p/ cruzar Faturados x Buffer) ────────────────────────
    # Tabela compacta: nº do pedido × tipo de operação → volume marcação (HL) somado.
    if _col_pedido:
        _num_ped = df[_col_pedido].astype(str).str.strip().str.lstrip("0")
        _tipo = df["Desc Tipo Movimento"].astype(str).str.strip() if "Desc Tipo Movimento" in df.columns else ""
        chave = pd.DataFrame({
            "num_pedido": _num_ped,
            "tipo_operacao": _tipo,
            "volume_marcacao": df["_volume_marcacao"],
        })
        chave = chave[chave["num_pedido"] != ""]
        chave = chave.groupby(["num_pedido", "tipo_operacao"], as_index=False)["volume_marcacao"].sum()
        chave["volume_marcacao"] = chave["volume_marcacao"].round(3)
        sobrescrever_aba("pedido_chave", chave)
        print(f"  🔑 pedido_chave: {len(chave)} linhas (nº pedido × tipo operação)")

        # ── Produtos por pedido (drill-down do Faturados × Buffer) ────────────
        # nº pedido × produto → volume marcação. Snapshot (substitui a cada import).
        prod_ped = pd.DataFrame({
            "num_pedido": _num_ped,
            "cod_produto": df["Cod. Prod."].astype(str).str.strip().str.lstrip("0"),
            "nome_produto": df["Nome Prod."].astype(str).str.strip(),
            "volume_marcacao": df["_volume_marcacao"],
        })
        prod_ped = prod_ped[prod_ped["num_pedido"] != ""]
        prod_ped = prod_ped.groupby(
            ["num_pedido", "cod_produto", "nome_produto"], as_index=False)["volume_marcacao"].sum()
        prod_ped["volume_marcacao"] = prod_ped["volume_marcacao"].round(3)
        sobrescrever_aba("pedido_produtos", prod_ped)
        print(f"  🧾 pedido_produtos: {prod_ped['num_pedido'].nunique()} pedidos · {len(prod_ped)} linhas")

        # ── Detalhe de pedidos dos SKUs dos Verdes p/ export Excel e mês por dia ──────
        # Stella Pure Gold 600: 33857 (retornável) + 38172 (one way). Linha a linha: nº pedido,
        # NF, data, PDV, produto, volume. Acumula por mês.
        try:
            _SKUS_V = {"33857", "38172"}
            _codn = df["Cod. Prod."].astype(str).str.strip().str.lstrip("0")
            dv = df[_codn.isin(_SKUS_V)]
            if not dv.empty:
                _dt = pd.to_datetime(dv["Data"].astype(str).str.strip(), format="%d/%m/%Y", errors="coerce")
                det_v = pd.DataFrame({
                    "num_pedido":   dv[_col_pedido].astype(str).str.strip().str.lstrip("0").values,
                    "nota_fiscal":  (dv["Nota Fiscal"].astype(str).str.strip().values if "Nota Fiscal" in dv.columns else ""),
                    "data":         dv["Data"].astype(str).str.strip().values,
                    "setor":        dv["_setor"].values,
                    "cod_pdv":      dv["_cod_pdv"].values,
                    "nome_pdv":     (dv["Nome Cliente"].astype(str).str.strip().values if "Nome Cliente" in dv.columns else ""),
                    "cod_produto":  _codn[dv.index].values,
                    "nome_produto": dv["Nome Prod."].astype(str).str.strip().values,
                    "volume_hl":    dv["_volume"].round(3).values,
                    "mes_referencia": _dt.dt.strftime("%Y-%m").values,
                })
                # Só VENDA efetiva (volume > 0) — bate com a cobertura do painel; exclui
                # linhas zeradas/devolução que inflavam a contagem de PDVs no export.
                det_v = det_v[det_v["mes_referencia"].notna() & (det_v["mes_referencia"] != "") & (det_v["volume_hl"] > 0)]
                sobrescrever_por_mes("verdes_pedidos", det_v, "mes_referencia")
                print(f"  🌿 verdes_pedidos (SKUs {sorted(_SKUS_V)}): {len(det_v)} linhas")
        except Exception as _ev:
            print(f"  ⚠️ verdes_pedidos: {_ev}")

    # ── Pedido BEES (rastreabilidade / Pesquisa Pedido) — 1 linha por Pedido Cliente ──
    # 'Pedido Cliente' (col BG) = nº do pedido no BEES. Agrega os itens do pedido:
    # NF, setor, PDV, data, HL (marcação × entrega), mapa, motivo e entrega real.
    if "Pedido Cliente" in df.columns:
        try:
            _mot = df["Motivo"].astype(str).str.strip() if "Motivo" in df.columns else pd.Series("", index=df.index)
            pb = pd.DataFrame({
                "bees":         df["Pedido Cliente"].astype(str).str.strip().str.lstrip("0"),
                "pedido":       df["Pedido"].astype(str).str.strip() if "Pedido" in df.columns else "",
                "nf":           df["Nota Fiscal"].astype(str).str.strip().str.lstrip("0") if "Nota Fiscal" in df.columns else "",
                "setor":        df["_setor"],
                "nome_setor":   df["Nome Setor"].astype(str).str.strip() if "Nome Setor" in df.columns else "",
                "cod_pdv":      df["_cod_pdv"],
                "nome_pdv":     df["Nome Cliente"].astype(str).str.strip() if "Nome Cliente" in df.columns else "",
                "data_pedido":  df["Data"].astype(str).str.strip(),
                "mapa":         df["Mapa"].astype(str).str.strip() if "Mapa" in df.columns else "",
                "entrega_real": df["Entrega Real"].astype(str).str.strip() if "Entrega Real" in df.columns else "",
                "motivo":       _mot,
                "hl_marcacao":  df["_volume_marcacao"],
                # CORA: HL de qualquer item ATENDIDO (venda, bonificação, remessa…) — rastreio
                # mostra o que foi entregue; Promax: Volume Entrega (como antes).
                "hl_entrega":   (pd.to_numeric(df["HL Atendido"].astype(str).str.replace(",", ".", regex=False).str.strip(),
                                               errors="coerce").fillna(0.0) if "HL Atendido" in df.columns else df["_volume"]),
                "operacao":     df["Operacao"].astype(str).str.strip().replace("nan", "") if "Operacao" in df.columns else "",
            })
            pb = pb[pb["bees"] != ""]
            if not pb.empty:
                ag = pb.groupby("bees", as_index=False).agg(
                    pedido=("pedido", "first"), nf=("nf", "first"), setor=("setor", "first"),
                    nome_setor=("nome_setor", "first"), cod_pdv=("cod_pdv", "first"),
                    nome_pdv=("nome_pdv", "first"), data_pedido=("data_pedido", "first"),
                    mapa=("mapa", "first"), entrega_real=("entrega_real", "first"),
                    hl_marcacao=("hl_marcacao", "sum"), hl_entrega=("hl_entrega", "sum"))
                # Motivo de devolução = 1º motivo diferente de "Pedido Normal" entre os itens.
                # FALTA (item cortado) não é devolução — não marca o pedido como devolvido.
                nn = pb[~pb["motivo"].str.upper().isin(["PEDIDO NORMAL", "FALTA", ""])].groupby("bees")["motivo"].first()
                ag["motivo"] = ag["bees"].map(nn).fillna("")
                ag["hl_marcacao"] = ag["hl_marcacao"].round(3)
                ag["hl_entrega"]  = ag["hl_entrega"].round(3)
                sobrescrever_aba("pedido_bees", ag)
                print(f"  📦 pedido_bees: {len(ag)} pedidos BEES")

                # Itens (produtos) de cada pedido BEES — abre na Pesquisa Pedido.
                it = pd.DataFrame({
                    "bees":         pb["bees"],
                    "cod_produto":  df.loc[pb.index, "Cod. Prod."].astype(str).str.strip(),
                    "nome_produto": df.loc[pb.index, "Nome Prod."].astype(str).str.strip(),
                    "hl_marcacao":  pb["hl_marcacao"],
                    "hl_entrega":   pb["hl_entrega"],
                    "operacao":     pb["operacao"],
                })
                it = (it.groupby(["bees", "cod_produto", "nome_produto", "operacao"], as_index=False)
                        .agg(hl_marcacao=("hl_marcacao", "sum"), hl_entrega=("hl_entrega", "sum"))
                        .sort_values(["bees", "hl_marcacao"], ascending=[True, False]))
                it["hl_marcacao"] = it["hl_marcacao"].round(3)
                it["hl_entrega"]  = it["hl_entrega"].round(3)
                sobrescrever_aba("pedido_bees_itens", it)
                print(f"  📦 pedido_bees_itens: {len(it)} itens")
        except Exception as _eb:
            print(f"  ⚠️ pedido_bees: {_eb}")

    hoje = date.today()
    mes_atual = hoje.replace(day=1)
    mes_anterior = (mes_atual - pd.DateOffset(months=1)).date()
    quatro_meses_atras = (mes_atual - pd.DateOffset(months=4)).date()

    _datas = df["_data"].dt.date
    df_mes_atual = df[_datas >= mes_atual]
    df_mes_ant = df[(_datas >= mes_anterior) & (_datas < mes_atual)]
    df_4m = df[_datas >= quatro_meses_atras]
    del _datas

    # Import de mês ANTIGO (arquivo sem linhas do mês corrente): não recalcula os
    # snapshots do "mês atual" (cobertura/volume RV) — senão zeraria o que está valendo.
    _tem_mes_atual = not df_mes_atual.empty

    # ── Cobertura ─────────────────────────────────────────────────────────────
    if _tem_mes_atual:
        _processar_cobertura(df, df_clientes_base, hoje)
    else:
        print("  ⏭️ Cobertura: arquivo sem linhas do mês corrente — snapshot mantido")
    del df_mes_ant
    gc.collect()

    # ── PDV Mix (últimos 4 meses) ─────────────────────────────────────────────
    _processar_mix(df_4m)
    gc.collect()

    # ── Rank clientes ─────────────────────────────────────────────────────────
    _processar_rank(df_4m)
    del df_4m
    gc.collect()

    # ── Volume RV (todos os meses do arquivo — acumula por mês) ───────────────
    processar_volume_rv(df)

    # ── Vendas por cliente x produto (TODOS os meses do arquivo — acumula por mês) ──
    # Passa o df completo: a função agrupa pelo mês REAL de cada linha e substitui só
    # os meses presentes no arquivo (import de junho em julho grava junho certinho).
    _processar_vendas_cliente(df)
    del df_mes_atual
    gc.collect()

    # ── Entregas efetivadas (setor x mês — Detalhamento HOP › Entrega) ────────
    _processar_entregas_efetivadas(df)
    gc.collect()

    # ── Volume DIÁRIO por PDV e por produto (comparação D-1 acumulado no dashboard) ──
    _processar_vendas_diaria(df)
    gc.collect()

    # ── Ruptura de estoque (detalhe por cliente x produto x dia) ──────────────
    _processar_ruptura(df)
    gc.collect()

    # ── Itens das notas frustradas (para abrir o pedido na aba Entrega) ───────
    _processar_nota_itens(df)
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



def _montar_detalhe_pedidos(base):
    """base: DataFrame distinto com _setor,_cod_pdv,_nome_pdv,_num_pedido.
    Junta com 'pedido_chave' (nº pedido × tipo operação → volume marcação) e devolve
    o detalhe por pedido × tipo. Pedidos sem correspondência entram com volume 0."""
    chave = ler_aba("pedido_chave")
    if chave is None or chave.empty:
        det = base.copy()
        det["tipo_operacao"] = ""
        det["volume_marcacao"] = 0.0
    else:
        chave = chave.copy()
        chave["num_pedido"] = chave["num_pedido"].astype(str).str.strip().str.lstrip("0")
        chave["volume_marcacao"] = pd.to_numeric(chave["volume_marcacao"], errors="coerce").fillna(0.0)
        det = base.merge(chave, left_on="_num_pedido", right_on="num_pedido", how="left")
        det["tipo_operacao"] = det["tipo_operacao"].fillna("").astype(str)
        det["volume_marcacao"] = pd.to_numeric(det["volume_marcacao"], errors="coerce").fillna(0.0).round(3)
    return pd.DataFrame({
        "setor": det["_setor"].values,
        "cod_pdv": det["_cod_pdv"].values,
        "nome_pdv": det["_nome_pdv"].values,
        "num_pedido": det["_num_pedido"].values,
        "tipo_operacao": det["tipo_operacao"].values,
        "volume_marcacao": det["volume_marcacao"].values,
    })



def processar_faturados(conteudo_bytes):
    """Rotina 030237 — clientes com NF faturada (pedidos que saíram p/ entrega).
    Cols: C=Setor-nf, M=Cliente(cod PDV), N=Nome(PDV), AC=Nr. Pedido. Cruza o nº do
    pedido com pedido_chave p/ trazer tipo de operação + volume marcação (HL).
    Gera 'faturados_detalhe' (snapshot — substitui a cada import)."""
    print("📂 Processando Faturados (030237)...")
    df = ler_csv_inf(conteudo_bytes,
                     usecols=lambda c: c.strip() in {"Setor - nf", "Cliente", "Nome", "Nr. Pedido", "Nota", "Total", "Mapa"})
    df.columns = [c.strip() for c in df.columns]
    df["_setor"] = df["Setor - nf"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    df["_cod_pdv"] = df["Cliente"].astype(str).str.strip().str.lstrip("0")
    df["_nome_pdv"] = df["Nome"].astype(str).str.strip()
    df["_num_pedido"] = df["Nr. Pedido"].astype(str).str.strip().str.lstrip("0")
    base = df[["_setor", "_cod_pdv", "_nome_pdv", "_num_pedido"]].drop_duplicates()
    base = base[base["_num_pedido"] != ""]
    out = _montar_detalhe_pedidos(base)
    sobrescrever_aba("faturados_detalhe", out)

    # ── Valor por NF (rastreabilidade de pedido) — soma Total por Nota + Mapa ──
    if "Nota" in df.columns:
        _nf = df["Nota"].astype(str).str.strip().str.lstrip("0")
        _tot = pd.to_numeric(
            df.get("Total", pd.Series("0", index=df.index)).astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce").fillna(0.0)
        fnf = pd.DataFrame({
            "nf": _nf, "setor": df["_setor"], "cod_pdv": df["_cod_pdv"], "nome_pdv": df["_nome_pdv"],
            "mapa": df["Mapa"].astype(str).str.strip() if "Mapa" in df.columns else "",
            "valor": _tot, "linhas": 1,
        })
        fnf = fnf[fnf["nf"] != ""]
        if not fnf.empty:
            fnf_ag = fnf.groupby("nf", as_index=False).agg(
                setor=("setor", "first"), cod_pdv=("cod_pdv", "first"), nome_pdv=("nome_pdv", "first"),
                mapa=("mapa", "first"), valor=("valor", "sum"), itens=("linhas", "sum"))
            fnf_ag["valor"] = fnf_ag["valor"].round(2)
            sobrescrever_aba("faturado_nf", fnf_ag)
            print(f"  🧾 faturado_nf: {len(fnf_ag)} notas (valor por NF)")

    n_ped = base["_num_pedido"].nunique()
    atualizar_status_arquivo("030237 (Faturados)", "✅ OK", f"{n_ped} pedidos, {len(out)} linhas")
    print(f"  ✅ Faturados: {n_ped} pedidos, {len(out)} linhas")
    return out



def processar_buffer(conteudo_bytes):
    """Rotina 030111 — pedidos parados no Buffer (ainda não faturados).
    Cols: G=Num Pedido, H=Cod. Vendedor(setor), I=Cod. Cliente, J=Nome Cliente. Cruza
    o nº do pedido com pedido_chave p/ trazer tipo de operação + volume marcação (HL).
    Gera 'buffer_detalhe' (snapshot — substitui a cada import)."""
    print("📂 Processando Buffer (030111)...")
    df = ler_csv_inf(conteudo_bytes,
                     usecols=lambda c: c.strip() in {"Cod. Vendedor", "Num Pedido", "Cod. Cliente", "Nome Cliente"})
    df.columns = [c.strip() for c in df.columns]
    df["_setor"] = df["Cod. Vendedor"].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)]
    df["_cod_pdv"] = df["Cod. Cliente"].astype(str).str.strip().str.lstrip("0")
    df["_nome_pdv"] = df["Nome Cliente"].astype(str).str.strip()
    df["_num_pedido"] = df["Num Pedido"].astype(str).str.strip().str.lstrip("0")
    base = df[["_setor", "_cod_pdv", "_nome_pdv", "_num_pedido"]].drop_duplicates()
    base = base[base["_num_pedido"] != ""]
    out = _montar_detalhe_pedidos(base)
    sobrescrever_aba("buffer_detalhe", out)
    n_ped = base["_num_pedido"].nunique()
    atualizar_status_arquivo("030111 (Buffer)", "✅ OK", f"{n_ped} pedidos, {len(out)} linhas")
    print(f"  ✅ Buffer: {n_ped} pedidos, {len(out)} linhas")
    return out



# ── Consulta-pedidos do CORA — fonte única do Buffer (novo) e do Deck (D+7) ──────
def _norm_cabecalho(c):
    """Normaliza um cabeçalho pra casar sem acento/ponto/caixa. 'Cód. produto'→'cod produto'."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(c)).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.replace(".", " ").lower().split())



# nome-normalizado do CORA → nome interno
_CORA_COLS = {
    "numero pedido": "num_pedido",
    "data entrega": "data_entrega",
    "cod cliente": "cod_pdv",
    "nome cliente": "nome_pdv",
    "tipo pedido": "tipo_pedido",
    "situacao pedido": "situacao",
    "tipo buffer": "tipo_buffer",
    "cod setor": "cod_setor",
    "desc setor": "desc_setor",
    "cod produto": "cod_produto",
    "desc produto": "nome_produto",
    "quant venda": "quant_venda",
    "volume hectolitro": "volume_hl",
}



def processar_cora(conteudo_bytes):
    """Consulta-pedidos exportada do CORA (~19 colunas). Fonte ÚNICA do Buffer e insumo
    do Deck (Agendados D+7). Gera dois snapshots (substituem a cada import):
      • buffer_detalhe  — linhas 'Tipo buffer' ≠ vazio, por pedido × tipo (HL).
      • deck_agendados  — Agendados D+7 por produto (caixas): ENTREGA + REGISTRADO
                          com Data entrega entre hoje+2 e hoje+7.
    Faturados segue vindo do Promax (030237) por ora."""
    from datetime import timedelta
    print("📂 Processando CORA (consulta-pedidos)...")

    # Lê só as colunas conhecidas (casando por cabeçalho normalizado, sem acento/ponto).
    usecols = lambda c: _norm_cabecalho(c) in _CORA_COLS
    df = ler_csv_inf(conteudo_bytes, usecols=usecols)
    df.columns = [_CORA_COLS.get(_norm_cabecalho(c), _norm_cabecalho(c)) for c in df.columns]

    for col in _CORA_COLS.values():
        if col not in df.columns:
            df[col] = ""

    df["_setor"] = df["cod_setor"].apply(normalizar_setor)
    df["_cod_pdv"] = df["cod_pdv"].astype(str).str.strip().str.lstrip("0")
    df["_nome_pdv"] = df["nome_pdv"].astype(str).str.strip()
    df["_num_pedido"] = df["num_pedido"].astype(str).str.strip().str.lstrip("0")
    df["_tipo_buffer"] = df["tipo_buffer"].astype(str).str.strip()
    df["_tipo_pedido"] = df["tipo_pedido"].astype(str).str.strip().str.upper()
    df["_situacao"] = df["situacao"].astype(str).str.strip().str.upper()
    df["_hl"] = pd.to_numeric(
        df["volume_hl"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce").fillna(0.0)
    df["_cx"] = pd.to_numeric(
        df["quant_venda"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce").fillna(0.0)
    df["_cod_produto"] = df["cod_produto"].astype(str).str.strip().str.lstrip("0")
    df["_data_entrega"] = pd.to_datetime(df["data_entrega"].astype(str).str.strip(),
                                         format="%d/%m/%Y", errors="coerce")

    # ── 1) BUFFER — 'Tipo buffer' = REPROGRAMADO E 'Situação pedido' = AGUARDANDO_ROTEIRIZACAO
    #    (o que ainda está parado). Só REPROGRAMADO pegava o mês inteiro (já entregues,
    #    devolvidos, em rota…) → ~2.700 HL. Conferido c/ o PDF do faturista em 21/10/2026:
    #    316,79 HL · 202 PDVs, batendo por setor.
    buf = df[(df["_tipo_buffer"].str.upper() == "REPROGRAMADO") & (df["_situacao"] == "AGUARDANDO_ROTEIRIZACAO") &
             (df["_setor"].isin(SETORES_VALIDOS)) & (df["_num_pedido"] != "")].copy()
    if buf.empty:
        det_buf = pd.DataFrame(columns=["setor", "cod_pdv", "nome_pdv", "num_pedido", "tipo_operacao", "volume_marcacao"])
    else:
        det_buf = (buf.groupby(["_num_pedido", "_tipo_buffer"], as_index=False)
                      .agg(setor=("_setor", "first"), cod_pdv=("_cod_pdv", "first"),
                           nome_pdv=("_nome_pdv", "first"), volume_marcacao=("_hl", "sum")))
        det_buf = det_buf.rename(columns={"_num_pedido": "num_pedido", "_tipo_buffer": "tipo_operacao"})
        det_buf["volume_marcacao"] = det_buf["volume_marcacao"].round(3)
        det_buf = det_buf[["setor", "cod_pdv", "nome_pdv", "num_pedido", "tipo_operacao", "volume_marcacao"]]
    sobrescrever_aba("buffer_detalhe", det_buf)
    print(f"  ✅ buffer_detalhe (CORA): {det_buf['num_pedido'].nunique()} pedidos · {len(det_buf)} linhas")

    # ── 2) DECK — Agendados D+7 por produto (caixas) ──────────────────────────────
    hoje = hoje_brasilia()
    d_ini = pd.Timestamp(hoje + timedelta(days=2))   # depois de amanhã (amanhã = entrega normal)
    d_fim = pd.Timestamp(hoje + timedelta(days=7))
    ag = df[(df["_tipo_pedido"] == "ENTREGA") & (df["_situacao"] == "REGISTRADO") &
            (df["_data_entrega"] >= d_ini) & (df["_data_entrega"] <= d_fim) &
            (df["_cod_produto"] != "")].copy()
    if ag.empty:
        deck = pd.DataFrame(columns=["cod_produto", "nome_produto", "caixas"])
    else:
        deck = (ag.groupby("_cod_produto", as_index=False)
                  .agg(nome_produto=("nome_produto", "first"), caixas=("_cx", "sum")))
        deck = deck.rename(columns={"_cod_produto": "cod_produto"})
        deck["caixas"] = deck["caixas"].round(0).astype(int)
        deck = deck.sort_values("caixas", ascending=False)
    sobrescrever_aba("deck_agendados", deck)
    print(f"  ✅ deck_agendados (D+7 {d_ini.date()}→{d_fim.date()}): {len(deck)} produtos · {int(deck['caixas'].sum()) if len(deck) else 0} cx")

    atualizar_status_arquivo("CORA (Pedidos)", "✅ OK",
                             f"buffer {len(det_buf)} linhas · deck {len(deck)} produtos")
    return det_buf


__all__ = [n for n in dir() if not n.startswith("__")]
