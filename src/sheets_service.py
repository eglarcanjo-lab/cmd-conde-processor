import os
import time
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# Cache do spreadsheet para evitar chamadas repetidas de fetch_sheet_metadata
_sheet_cache = None
_sheet_cache_time = 0
CACHE_TTL = 300  # 5 minutos


def get_client():
    creds = Credentials.from_service_account_info(
        {
            "type": "service_account",
            "project_id": os.environ.get("GOOGLE_PROJECT_ID", "cmd-conde"),
            "private_key_id": os.environ.get("GOOGLE_PRIVATE_KEY_ID", ""),
            "private_key": os.environ.get("GOOGLE_PRIVATE_KEY", "").replace("\\n", "\n"),
            "client_email": os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL"),
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def get_sheet():
    """Retorna o spreadsheet com cache para evitar fetch_sheet_metadata repetido."""
    global _sheet_cache, _sheet_cache_time
    agora = time.time()
    if _sheet_cache is not None and (agora - _sheet_cache_time) < CACHE_TTL:
        return _sheet_cache
    client = get_client()
    _sheet_cache = client.open_by_key(SHEET_ID)
    _sheet_cache_time = agora
    return _sheet_cache


def _com_retry(fn, max_tentativas=5, espera_inicial=10):
    """
    Executa fn com retry automático em caso de quota 429.
    Espera exponencial: 10s, 20s, 40s, 80s, 160s.
    """
    for tentativa in range(max_tentativas):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            if e.response.status_code == 429 and tentativa < max_tentativas - 1:
                espera = espera_inicial * (2 ** tentativa)
                print(f"  ⚠️ Quota 429 — aguardando {espera}s (tentativa {tentativa + 1}/{max_tentativas})")
                time.sleep(espera)
                # Invalida cache após quota exceeded
                global _sheet_cache
                _sheet_cache = None
            else:
                raise


def ler_aba(nome_aba):
    """Lê uma aba e retorna DataFrame, com retry em caso de quota exceeded.
    Usa UNFORMATTED_VALUE para receber números como floats Python (sem formatação
    de locale), evitando que "73,235" (locale BR) seja retornado como string
    em vez do número 73.235.
    """
    def _ler():
        sh = get_sheet()
        try:
            ws = sh.worksheet(nome_aba)
            data = ws.get_all_records(value_render_option="UNFORMATTED_VALUE")
            return pd.DataFrame(data)
        except gspread.exceptions.WorksheetNotFound:
            return pd.DataFrame()

    return _com_retry(_ler)


def sobrescrever_aba(nome_aba, df):
    """Limpa a aba e escreve o DataFrame do zero, com retry em caso de quota."""
    def _sobrescrever():
        sh = get_sheet()
        try:
            ws = sh.worksheet(nome_aba)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=nome_aba, rows=1, cols=1)

        ws.clear()

        if df.empty:
            ws.update([df.columns.tolist()])
            return

        df_out = df.copy().fillna("")

        # Converte colunas numéricas para número
        for col in df_out.columns:
            try:
                converted = pd.to_numeric(df_out[col], errors='coerce')
                if converted.notna().sum() / max(len(df_out), 1) > 0.5:
                    df_out[col] = converted.where(converted.notna(), df_out[col])
            except:
                pass

        def cell_value(v):
            if pd.isna(v):
                return ""
            if isinstance(v, (int, float)):
                return v
            return str(v)

        rows = [[cell_value(v) for v in row] for _, row in df_out.iterrows()]
        data = [df_out.columns.tolist()] + rows
        ws.update(data, value_input_option="RAW")
        print(f"  ✅ Aba '{nome_aba}' atualizada: {len(df_out)} linhas")

    _com_retry(_sobrescrever)


def atualizar_status_arquivo(nome_arquivo, status, detalhes=""):
    """Registra o status de processamento, com retry em caso de quota."""
    def _atualizar():
        sh = get_sheet()
        from datetime import datetime
        try:
            import pytz
            agora = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
        except Exception:
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")

        try:
            ws = sh.worksheet("status_arquivos")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title="status_arquivos", rows=1, cols=5)
            ws.update([["arquivo", "status", "detalhes", "atualizado_em", "periodicidade"]])

        records = ws.get_all_records()
        linha_idx = None
        for i, r in enumerate(records):
            if r.get("arquivo") == nome_arquivo:
                linha_idx = i + 2
                break

        nova_linha = [nome_arquivo, status, detalhes, agora, "Diária"]

        if linha_idx:
            ws.update(f"A{linha_idx}:E{linha_idx}", [nova_linha])
        else:
            ws.append_row(nova_linha)

    _com_retry(_atualizar)
