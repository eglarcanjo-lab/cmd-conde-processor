import os
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")


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
    client = get_client()
    return client.open_by_key(SHEET_ID)


def ler_aba(nome_aba):
    """Lê uma aba e retorna DataFrame."""
    sh = get_sheet()
    try:
        ws = sh.worksheet(nome_aba)
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()


def sobrescrever_aba(nome_aba, df):
    """
    Limpa a aba e escreve o DataFrame do zero.
    Mantém o cabeçalho como primeira linha.
    """
    sh = get_sheet()
    try:
        ws = sh.worksheet(nome_aba)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=nome_aba, rows=1, cols=1)

    ws.clear()

    if df.empty:
        ws.update([df.columns.tolist()])
        return

    df = df.fillna("").astype(str)
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    print(f"  ✅ Aba '{nome_aba}' atualizada: {len(df)} linhas")


def atualizar_status_arquivo(nome_arquivo, status, detalhes=""):
    """Registra o status de processamento de cada arquivo."""
    sh = get_sheet()
    from datetime import datetime
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
            linha_idx = i + 2  # +2 por causa do cabeçalho (1-based)
            break

    nova_linha = [nome_arquivo, status, detalhes, agora, "Diária"]

    if linha_idx:
        ws.update(f"A{linha_idx}:E{linha_idx}", [nova_linha])
    else:
        ws.append_row(nova_linha)
