"""Processador HOP — base: imports, constantes, normalização de setor/dia e helpers comuns.
Dividido de processor.py (Auditoria out/26). processor.py reexporta tudo:
quem faz `from processor import X` continua funcionando."""
import pandas as pd

import chardet

import io

from datetime import datetime, date

from categorias import resolver_categoria, carregar_base_produtos, CATEGORIAS_VALIDAS

from sheets_service import ler_aba, sobrescrever_aba, sobrescrever_por_mes, atualizar_status_arquivo


# Setores válidos da CMD Conde
# Setores 301–305 foram RENOMEADOS para 107–111 (out/2026) e passaram para o GV1.
# Todo setor lido passa por normalizar_setor/setor_hop, que aplica o de-para — dado novo
# já entra como 107–111. 301–305 continuam "válidos" só para não descartar histórico.
DE_PARA_SETOR = {"301": "107", "302": "108", "303": "109", "304": "110", "305": "111"}

SETORES_VALIDOS = {
    "101", "102", "103", "104", "105", "106", "107", "108", "109", "110", "111",
    "301", "302", "303", "304", "305",
}


DIAS_SEMANA_MAP = {
    "SEG": 0, "TER": 1, "QUA": 2, "QUI": 3, "SEX": 4, "SAB": 5, "DOM": 6,
    "SEGUNDA": 0, "TERÇA": 1, "QUARTA": 2, "QUINTA": 3, "SEXTA": 4,
}



def detectar_encoding(conteudo_bytes):
    resultado = chardet.detect(conteudo_bytes[:10000])
    enc = resultado.get("encoding", "latin-1")
    return enc if enc else "latin-1"



def ler_csv_inf(conteudo_bytes, usecols=None):
    """Lê um CSV/INF separado por ; com encoding automático.
    usecols: opcional — callable ou lista para carregar só algumas colunas
    (reduz drasticamente a memória em arquivos largos, ex: pedidos 71 colunas)."""
    enc = detectar_encoding(conteudo_bytes)
    try:
        texto = conteudo_bytes.decode(enc, errors="replace")
    except Exception:
        texto = conteudo_bytes.decode("latin-1", errors="replace")
    # index_col=False: impede que pandas use a 1ª coluna de dados como índice
    # quando o CSV tem ponto-e-vírgula no final de cada linha (campo extra vazio).
    return pd.read_csv(io.StringIO(texto), sep=";", dtype=str, low_memory=False, index_col=False, usecols=usecols)



def normalizar_setor(setor_raw):
    """Remove zeros à esquerda do setor ('00101' → '101') e aplica o de-para dos setores
    renomeados (301–305 → 107–111)."""
    try:
        s = str(int(str(setor_raw).strip()))
    except:
        s = str(setor_raw).strip()
    return DE_PARA_SETOR.get(s, s)



def setor_hop(v):
    """Setor já 'limpo' (texto) → aplica só o de-para (preserva 'OPERACAO' etc.)."""
    s = str(v).strip()
    return DE_PARA_SETOR.get(s, s)



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



def hoje_brasilia():
    """Data de HOJE no fuso de Brasília. O Render roda em UTC, então date.today()
    'vira' o dia por volta das 21h (BRT), quebrando a regra 'dia atual usa Volume
    Marcação' do Volume Diário (a marcação de hoje virava dia-passado e sumia)."""
    from datetime import datetime
    import pytz
    return datetime.now(pytz.timezone("America/Sao_Paulo")).date()



def _txt(v):
    """Texto limpo: vazio p/ None/NaN e p/ os textos 'nan'/'none'/'null' (sobra de NaN salvo
    como texto — antes travava o nome como "nan" p/ sempre na produtos_base)."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "null", "nat") else t


# Exporta TUDO (inclusive _helpers) p/ os outros módulos e p/ o processor.py
__all__ = [n for n in dir() if not n.startswith("__")]
