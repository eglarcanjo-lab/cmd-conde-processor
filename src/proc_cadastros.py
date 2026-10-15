"""Processador HOP — devoluções, produtos sem categoria, comodatos, inadimplência, tasks, base de produtos e grade.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
from proc_base import *  # noqa: F401,F403



def processar_devolucoes_relatorio(conteudo_bytes, mes_ref=None):
    """Processa o relatório de DEVOLUÇÕES (entregas frustradas).

    Layout posicional (separador ';', cabeçalho com 'Setor' duplicado):
      0 Setor(00xxxx) · 1 Data · 3 Nota · 6 Cod.Cliente · 7 Nome Cliente ·
      10 Setor(xxx) · 12 Valor · 13 Volume · 14 Data Devol. · 15 Cod.Motivo ·
      16 Desc.Motivo
    Gera as abas:
      • entregas_frustradas        — detalhe por nota (motivo, volume, valor)
      • entregas_resumo_motivo     — resumo por setor x motivo (qtd, volume, valor)
    """
    print("📂 Processando devoluções (entregas frustradas)...")
    df = ler_csv_inf(conteudo_bytes)
    cols = list(df.columns)
    if len(cols) < 17:
        raise ValueError(f"Relatório de devoluções com estrutura inesperada: {len(cols)} colunas.")

    def col(i):
        return df[cols[i]].astype(str).str.strip()

    out = pd.DataFrame({
        "setor":       col(10).apply(normalizar_setor),
        "data":        col(1),
        "nota":        col(3),
        "cod_pdv":     col(6).str.lstrip("0").str.strip(),
        "nome_pdv":    col(7),
        "placa":       col(11),
        "valor":       col(12),
        "volume_hl":   col(13),
        "data_devol":  col(14),
        "cod_motivo":  col(15),
        "desc_motivo": col(16),
    })
    out = out[out["setor"].isin(SETORES_VALIDOS)]

    # Numéricos no padrão BR (vírgula decimal)
    out["valor"] = pd.to_numeric(out["valor"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce").fillna(0.0).round(2)
    out["volume_hl"] = pd.to_numeric(out["volume_hl"].str.replace(",", ".", regex=False), errors="coerce").fillna(0.0).round(3)
    out["desc_motivo"] = out["desc_motivo"].str.strip().str.rstrip(".").str.upper()

    # Mês de referência: derivado da Data (DD/MM/YYYY), fallback no parâmetro/UI
    fallback = mes_ref or date.today().strftime("%Y-%m")
    datas = pd.to_datetime(out["data"], format="%d/%m/%Y", errors="coerce")
    out["mes_referencia"] = datas.dt.strftime("%Y-%m").fillna(fallback)

    if out.empty:
        sobrescrever_aba("entregas_frustradas", pd.DataFrame(columns=[
            "setor","data","nota","cod_pdv","nome_pdv","placa","valor","volume_hl","data_devol","cod_motivo","desc_motivo","mes_referencia"]))
        atualizar_status_arquivo("Devoluções (Entregas Frustradas)", "⚠️ Sem dados", "Nenhum setor válido")
        print("  ⚠️ Devoluções: nenhuma linha válida")
        return out

    # ── ACUMULA por mês ───────────────────────────────────────────────────────
    # Permite importar mês a mês e montar o quadrimestre. Cada importação
    # SUBSTITUI apenas os meses presentes no arquivo novo; meses antigos ficam.
    cols_out = ["setor","data","nota","cod_pdv","nome_pdv","placa","valor","volume_hl","data_devol","cod_motivo","desc_motivo","mes_referencia"]
    meses_novos = set(out["mes_referencia"].unique())
    try:
        antigo = ler_aba("entregas_frustradas")
        if not antigo.empty and "mes_referencia" in antigo.columns:
            antigo = antigo[~antigo["mes_referencia"].astype(str).isin(meses_novos)]
            for c in cols_out:
                if c not in antigo.columns:
                    antigo[c] = ""
            antigo = antigo[cols_out]
            antigo["valor"] = pd.to_numeric(antigo["valor"], errors="coerce").fillna(0.0)
            antigo["volume_hl"] = pd.to_numeric(antigo["volume_hl"], errors="coerce").fillna(0.0)
            out = pd.concat([antigo, out[cols_out]], ignore_index=True)
    except Exception as e:
        print(f"  ⚠️ Não consegui ler devoluções anteriores (seguindo só com o novo): {e}")

    out = out.sort_values(["mes_referencia", "setor", "data"])
    sobrescrever_aba("entregas_frustradas", out)

    meses_lbl = ", ".join(sorted(out["mes_referencia"].unique()))
    atualizar_status_arquivo("Devoluções (Entregas Frustradas)", "✅ OK", f"{len(out)} devoluções · meses: {meses_lbl}")
    print(f"  ✅ Devoluções: {len(out)} linhas acumuladas · meses {meses_lbl}")
    return out



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
                        "nome": _txt(row.get("nome", "")),
                        "categorias": _txt(row.get("categorias", "")),
                        "atualizado_em": _txt(row.get("atualizado_em", "")),
                    }
    except Exception:
        mapa_base = {}

    # Produtos que aparecem nos pedidos
    todos_prods = df[["_cod_str", "Nome Prod."]].drop_duplicates()
    todos_prods = todos_prods.rename(columns={"_cod_str": "cod", "Nome Prod.": "nome_pedido"})

    linhas_base = []
    for _, row in todos_prods.iterrows():
        cod = str(row["cod"]).strip()
        nome_pedido = _txt(row["nome_pedido"])
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



def processar_comodatos(conteudo_bytes):
    """Relatório de COMODATOS do Promax (equipamentos/vasilhame cedidos ao PDV).
    Quem aparece na relação TEM comodato. Gera a aba `comodatos` (snapshot): 1 linha por
    equipamento/contrato com qtd comodatada, baixada e em aberto (= comodatado − baixado).
    Usada na prévia de INATIVAÇÃO de PDV (Solicitações › Migração)."""
    print("📂 Processando comodatos...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [str(c).strip() for c in df.columns]
    obrig = ["Cliente", "Nome / Razao", "Codigo Setor", "Descricao", "Comodatado", "Baixado"]
    falta = [c for c in obrig if c not in df.columns]
    if falta:
        raise ValueError(f"Arquivo de comodatos fora do layout — faltam colunas: {falta}")

    def txt(c):
        return df[c].astype(str).str.strip() if c in df.columns else ""

    def inteiro(c):
        return pd.to_numeric(txt(c).str.replace(".", "", regex=False), errors="coerce").fillna(0).astype(int)

    def valor(c):
        return pd.to_numeric(txt(c).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
                             errors="coerce").fillna(0.0)

    out = pd.DataFrame({
        "cod_pdv":         txt("Cliente").str.lstrip("0"),
        "nome_pdv":        txt("Nome / Razao"),
        "setor":           df["Codigo Setor"].apply(normalizar_setor),
        "nro_comodato":    txt("Nro Comodato").str.lstrip("0"),
        "cod_produto":     txt("Codigo Produto").str.lstrip("0"),
        "descricao":       txt("Descricao").str.rstrip(",").str.replace(",", " ", regex=False).str.replace(r"\s+", " ", regex=True),
        "tipo_material":   txt("Tipo Material"),
        "comodatado":      inteiro("Comodatado"),
        "baixado":         inteiro("Baixado"),
        "valor":           valor("Valor").round(2),
        "status_cliente":  txt("Status Cliente"),
        "data_emissao":    txt("Data Emissao"),
        "vencimento":      txt("Vencimento"),
        "status_auditoria": txt("Status Auditoria"),
        "data_recolha":    txt("Data Recolha").replace("00/00/0000", ""),
    })
    out["em_aberto"] = (out["comodatado"] - out["baixado"]).clip(lower=0)
    out = out[out["cod_pdv"] != ""]
    sobrescrever_aba("comodatos", out)
    n_pdv = out["cod_pdv"].nunique()
    atualizar_status_arquivo("Comodatos", "✅ OK", f"{n_pdv} PDVs · {len(out)} equipamentos/contratos")
    print(f"  ✅ comodatos: {n_pdv} PDVs · {len(out)} linhas")
    return {"pdvs": n_pdv, "linhas": len(out)}



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

    # Proteção (v3.85.7): arquivo do BI FILTRADO (só alguns RNs) apagava as tasks de todos
    # os outros setores (caso real: só 109/110 → o 108 ficou sem nenhuma task). Agora o
    # arquivo atualiza só os setores que vieram nele; os demais ficam como estavam.
    setores_novos = sorted(set(df_tasks["setor"])) if not df_tasks.empty else []
    mantidos = []
    try:
        antigo = ler_aba("tasks")
        if not antigo.empty and "setor" in antigo.columns and setores_novos:
            antigo["setor"] = antigo["setor"].astype(str).str.strip().map(setor_hop)
            fora = antigo[~antigo["setor"].isin(setores_novos)]
            if not fora.empty:
                mantidos = sorted(set(fora["setor"]))
                df_tasks = pd.concat([df_tasks, fora[[c for c in df_tasks.columns if c in fora.columns]]], ignore_index=True)
    except Exception as e:
        print(f"  ⚠️ tasks anteriores não lidas (segue só com o arquivo): {e}")

    sobrescrever_aba("tasks", df_tasks)
    msg = f"{len(df_tasks)} tasks · setores no arquivo: {', '.join(setores_novos) or '—'}"
    if mantidos:
        msg += f" · ⚠️ arquivo parcial — mantidos da importação anterior: {', '.join(mantidos)}"
    atualizar_status_arquivo("Tasks (BI)", "⚠️ Parcial" if mantidos else "✅ OK", msg)
    print(f"  {msg}")
    print(f"  ✅ Tasks processadas: {len(df_tasks)}")
    return df_tasks


def processar_missoes(conteudo_bytes):
    """Relatório de MISSÕES do BI (campanha, ex.: "MANDA BALA NAS VENDAS") — xlsx com 2
    linhas de filtro antes do cabeçalho: Operação | UNB | GV | RN | PDV | Nome PDV |
    Data Próxima Visita | Campanha | Missão | Data Conclusão | Validacao (SIM/NÃO).
    Gera a aba `missoes`. Arquivo FILTRADO (só alguns RNs) atualiza só esses setores."""
    import re as _re
    print("📂 Processando missões...")
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), engine="openpyxl", dtype=str, skiprows=2)
    except Exception:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), dtype=str, skiprows=2)
    df.columns = [str(c).strip() for c in df.columns]

    def col(*nomes):
        norm = {c.upper().replace("Ã", "A").replace("Ç", "C"): c for c in df.columns}
        for n in nomes:
            c = norm.get(n.upper())
            if c: return c
        raise ValueError(f"Coluna não encontrada: {nomes[0]}. Disponíveis: {list(df.columns)}")
    c_rn, c_pdv, c_nome = col("RN"), col("PDV"), col("NOME PDV")
    c_prox, c_camp, c_miss = col("DATA PROXIMA VISITA", "DATA PRÓXIMA VISITA"), col("CAMPANHA"), col("MISSAO", "MISSÃO")
    c_conc, c_val, c_gv = col("DATA CONCLUSAO", "DATA CONCLUSÃO"), col("VALIDACAO", "VALIDAÇÃO"), col("GV")

    def data_br(v):
        t = _txt(v)
        if not t: return ""
        try:
            if _re.fullmatch(r"\d+(\.\d+)?", t):   # serial do Excel
                return (date(1899, 12, 30) + pd.Timedelta(days=int(float(t)))).strftime("%d/%m/%Y")
            return pd.to_datetime(t, dayfirst=not _re.match(r"\d{4}-", t)).strftime("%d/%m/%Y")
        except Exception:
            return t

    def limpa_missao(v):
        t = _txt(v)
        t = _re.sub(r"^[^0-9A-Za-zÀ-ÿ]+", "", t)                       # emojis do começo
        t = _re.sub(r"\s*Valendo mais pontos!?\s*$", "", t, flags=_re.I)  # sufixo igual em todas
        return t.strip()

    df["_setor"] = df[c_rn].apply(normalizar_setor)
    df = df[df["_setor"].isin(SETORES_VALIDOS)].copy()
    hoje = hoje_brasilia()
    out = pd.DataFrame({
        "setor":          df["_setor"],
        "gv":             df[c_gv].map(_txt),
        "cod_pdv":        df[c_pdv].map(_txt).str.replace(r"\.0$", "", regex=True).str.lstrip("0"),
        "nome_pdv":       df[c_nome].map(_txt).str.replace(r"^\d+\s*-\s*", "", regex=True),
        "proxima_visita": df[c_prox].map(data_br),
        "campanha":       df[c_camp].map(_txt),
        "missao":         df[c_miss].map(limpa_missao),
        "concluida_em":   df[c_conc].map(data_br),
        "validada":       df[c_val].map(_txt).str.upper().map(lambda s: "SIM" if s.startswith("S") else "NÃO"),
        "mes_referencia": f"{hoje.year}-{hoje.month:02d}",
    })
    out = out[out["missao"] != ""]

    # Mesmo cuidado das Tasks: arquivo parcial não apaga os outros setores
    setores_novos = sorted(set(out["setor"])) if not out.empty else []
    mantidos = []
    try:
        antigo = ler_aba("missoes")
        if not antigo.empty and "setor" in antigo.columns and setores_novos:
            antigo["setor"] = antigo["setor"].astype(str).str.strip().map(setor_hop)
            fora = antigo[~antigo["setor"].isin(setores_novos)]
            if not fora.empty:
                mantidos = sorted(set(fora["setor"]))
                out = pd.concat([out, fora[[c for c in out.columns if c in fora.columns]]], ignore_index=True)
    except Exception as e:
        print(f"  ⚠️ missões anteriores não lidas (segue só com o arquivo): {e}")

    sobrescrever_aba("missoes", out)
    ok = int((out["validada"] == "SIM").sum())
    msg = f"{len(out)} missões · {ok} validadas · {out['cod_pdv'].nunique()} PDVs · setores: {', '.join(setores_novos) or '—'}"
    if mantidos:
        msg += f" · ⚠️ arquivo parcial — mantidos da importação anterior: {', '.join(mantidos)}"
    atualizar_status_arquivo("Missões (BI)", "⚠️ Parcial" if mantidos else "✅ OK", msg)
    print(f"  {msg}")
    return out


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

    # Gera a aba produtos_full (TODOS os produtos + HL/caixa) a partir do MESMO
    # 0111 — base de nomes e HL para a Grade de Estoque e o Shelf.
    try:
        _gerar_produtos_full(df)
    except Exception as e:
        print(f"  ⚠️ Não consegui gerar produtos_full: {e}")

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



def _gerar_produtos_full(df):
    """A partir do DataFrame do 0111 (já lido), gera a aba produtos_full com
    TODOS os produtos + HL por caixa (coluna 'Fator Hecto Comercial', col P).
    Base de nomes/HL para a Grade de Estoque e o Shelf.
    """
    cols = list(df.columns)
    # Acha 'Fator Hecto Comercial' pelo nome (tolerante a encoding); senão usa pos 15
    col_hl = next((c for c in cols if "hecto" in c.lower() and "comerc" in c.lower()), None)
    if col_hl is None and len(cols) > 15:
        col_hl = cols[15]

    out = pd.DataFrame({
        "cod":       df["Código"].astype(str).str.strip().str.lstrip("0"),
        "nome":      df["Descrição"].astype(str).str.strip(),
        "embalagem": df.get("Embalagem", pd.Series("", index=df.index)).astype(str).str.strip(),
        "hl_caixa":  df[col_hl].astype(str).str.strip() if col_hl else "0",
    })
    out["hl_caixa"] = pd.to_numeric(
        out["hl_caixa"].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0.0).round(6)
    out = out[(out["nome"] != "") & (~out["nome"].str.match(r"^0000\.0"))]
    out = out.drop_duplicates(subset=["cod"])
    sobrescrever_aba("produtos_full", out)
    print(f"  ✅ produtos_full: {len(out)} produtos (do 0111, col HL='{col_hl}')")
    return out



def processar_grade_estoque(conteudo_bytes):
    """Grade de estoque (saldo Disp por produto) — sistema CORA. CSV `;` com colunas
    nomeadas: CodigoProduto; DescricaoProduto; UnidadeMedida; GradeEstoque;
    GradeCadastrada; Reserva; Saida; SaldoDisponivel. Fallback p/ o layout posicional
    antigo. Junta produtos_full p/ nome + HL/caixa. Gera grade_estoque (saldo > 0).
    """
    print("📂 Processando grade de estoque...")
    df = ler_csv_inf(conteudo_bytes)
    df.columns = [str(c).strip() for c in df.columns]

    def _acha(*nomes):
        for nm in nomes:
            for c in df.columns:
                if c.strip().lower() == nm.lower():
                    return c
        return None

    def to_int(s):
        return pd.to_numeric(s.astype(str).str.replace(r"[^\d-]", "", regex=True), errors="coerce").fillna(0).astype(int)

    c_cod  = _acha("CodigoProduto", "Codigo", "Cod")
    c_disp = _acha("SaldoDisponivel", "SaldoDisponível", "Saldo Disponivel", "Disp")

    if c_cod and c_disp:
        # ── Novo formato CORA (colunas nomeadas) ──
        c_desc  = _acha("DescricaoProduto", "Descricao", "Descrição")
        c_un    = _acha("UnidadeMedida", "Unidade", "UN")
        c_said  = _acha("Saida", "Saidas", "Saída")
        c_grade = _acha("GradeEstoque", "Grade")
        g = pd.DataFrame({
            "grade":     (df[c_grade].astype(str).str.strip() if c_grade else "1"),
            "cod":       df[c_cod].astype(str).str.strip().str.lstrip("0"),
            "descricao": (df[c_desc].astype(str).str.strip() if c_desc else ""),
            "un":        (df[c_un].astype(str).str.strip() if c_un else ""),
            "saidas":    (to_int(df[c_said]) if c_said else 0),
            "saldo":     to_int(df[c_disp]),   # SaldoDisponivel = Disp
        })
    else:
        # ── Fallback: layout posicional antigo (0 Grade · 1 Cod · 2 Desc · 3 UN · 9 Saidas · 11 Disp) ──
        cols = list(df.columns)
        if len(cols) < 12:
            raise ValueError(f"Grade com estrutura inesperada: {len(cols)} colunas / cabeçalhos {cols[:8]}.")
        col = lambda i: df[cols[i]].astype(str).str.strip()
        g = pd.DataFrame({
            "grade": col(0), "cod": col(1).str.lstrip("0"), "descricao": col(2),
            "un": col(3), "saidas": to_int(col(9)), "saldo": to_int(col(11)),
        })
    g = g[g["saldo"] > 0].copy()

    # Junta nome completo + HL/caixa da base
    try:
        pf = ler_aba("produtos_full")
    except Exception:
        pf = pd.DataFrame()
    mapa_nome, mapa_hl = {}, {}
    if not pf.empty and "cod" in pf.columns:
        for _, r in pf.iterrows():
            c = str(r.get("cod", "")).strip()
            if not c:
                continue
            mapa_nome[c] = str(r.get("nome", "")).strip()
            try:
                mapa_hl[c] = float(str(r.get("hl_caixa", 0)).replace(",", ".") or 0)
            except Exception:
                mapa_hl[c] = 0.0

    g["nome"] = g["cod"].map(mapa_nome).fillna(g["descricao"])
    g["hl_caixa"] = g["cod"].map(mapa_hl).fillna(0.0)
    g["hl_estoque"] = (g["saldo"] * g["hl_caixa"]).round(3)
    g = g.sort_values("saldo", ascending=False)

    sobrescrever_aba("grade_estoque", g[["grade", "cod", "nome", "descricao", "un", "saldo", "saidas", "hl_caixa", "hl_estoque"]])
    sem_nome = int((g["nome"] == g["descricao"]).sum())
    atualizar_status_arquivo("Grade de Estoque", "✅ OK", f"{len(g)} itens · {round(g['hl_estoque'].sum(),1)} HL")
    print(f"  ✅ grade_estoque: {len(g)} itens · {sem_nome} sem nome na base · {round(g['hl_estoque'].sum(),1)} HL")
    return g


__all__ = [n for n in dir() if not n.startswith("__")]
