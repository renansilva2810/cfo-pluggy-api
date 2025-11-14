import os
from datetime import datetime, timedelta, timezone

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

PLUGGY_CLIENT_ID = os.getenv("PLUGGY_CLIENT_ID")
PLUGGY_CLIENT_SECRET = os.getenv("PLUGGY_CLIENT_SECRET")

if not PLUGGY_CLIENT_ID or not PLUGGY_CLIENT_SECRET:
    raise RuntimeError("Configure PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET nas variáveis de ambiente.")

PLUGGY_BASE_URL = "https://api.pluggy.ai"

app = FastAPI(title="CFO Backend - Pluggy API")
@app.get("/")
def root():
    return {"message": "API CFO Pluggy rodando"}


class ConnectTokenRequest(BaseModel):
    user_id: str  # ex: "renan"


def get_pluggy_api_key() -> str:
    """
    Pede um API Key pra Pluggy usando CLIENT_ID e CLIENT_SECRET.
    Documentação: POST /auth
    """
    url = f"{PLUGGY_BASE_URL}/auth"
    resp = requests.post(
        url,
        json={
            "clientId": PLUGGY_CLIENT_ID,
            "clientSecret": PLUGGY_CLIENT_SECRET,
        },
        timeout=15,
    )
    if not resp.ok:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao autenticar na Pluggy: {resp.status_code} {resp.text}",
        )
    data = resp.json()
    api_key = data.get("apiKey")
    if not api_key:
        raise HTTPException(status_code=500, detail="Resposta de auth da Pluggy sem apiKey.")
    return api_key


def pluggy_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/pluggy/connect-token")
def create_connect_token(body: ConnectTokenRequest):
    """
    Gera um connectToken pra usar no Pluggy Connect Widget.
    Doc: POST /connect_token
    """
    api_key = get_pluggy_api_key()
    url = f"{PLUGGY_BASE_URL}/connect_token"

    payload = {
        "clientUserId": body.user_id,
        # Você pode incluir options aqui se quiser (webhookUrl, oauthRedirectUrl, etc)
    }

    resp = requests.post(url, json=payload, headers=pluggy_headers(api_key), timeout=15)
    if not resp.ok:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao criar connect token: {resp.status_code} {resp.text}",
        )

    data = resp.json()
    # Nas docs o campo vem como accessToken
    access_token = data.get("accessToken") or data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=500, detail="Resposta da Pluggy sem accessToken.")

    return {
        "connectToken": access_token,
        "raw": data,
    }


@app.get("/users/{user_id}/snapshot")
def get_user_snapshot(user_id: str, item_id: str):
    """
    Dado um itemId (conexão com banco na Pluggy), busca contas e transações
    e devolve um snapshot simplificado para o seu CFO GPT.
    """
    api_key = get_pluggy_api_key()

    # 1) Buscar contas desse item
    accounts_url = f"{PLUGGY_BASE_URL}/accounts"
    accounts_resp = requests.get(
        accounts_url,
        headers=pluggy_headers(api_key),
        params={"itemId": item_id, "pageSize": 500},
        timeout=20,
    )
    if not accounts_resp.ok:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao listar contas: {accounts_resp.status_code} {accounts_resp.text}",
        )
    accounts = accounts_resp.json().get("results", [])

    # 2) Buscar transações dos últimos 90 dias
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=90)

    transactions_url = f"{PLUGGY_BASE_URL}/transactions"
    tx_resp = requests.get(
        transactions_url,
        headers=pluggy_headers(api_key),
        params={
            "itemId": item_id,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "pageSize": 500,
        },
        timeout=20,
    )
    if not tx_resp.ok:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao listar transações: {tx_resp.status_code} {tx_resp.text}",
        )
    transactions = tx_resp.json().get("results", [])

    # 3) Cálculos básicos de snapshot
    saldo_total = 0.0
    for acc in accounts:
        bal = acc.get("balance")
        if isinstance(bal, (int, float)):
            saldo_total += bal

    entradas = [t for t in transactions if t.get("amount", 0) > 0]
    saidas = [t for t in transactions if t.get("amount", 0) < 0]

    total_entradas = sum(t.get("amount", 0) for t in entradas)
    total_saidas = sum(t.get("amount", 0) for t in saidas)

    snapshot = {
        "user_id": user_id,
        "item_id": item_id,
        "saldo_total_contas": saldo_total,
        "fluxo_90_dias": {
            "total_entradas": total_entradas,
            "total_saidas": total_saidas,
            "saldo": total_entradas + total_saidas,
        },
        "resumo": {
            "qtd_contas": len(accounts),
            "qtd_transacoes": len(transactions),
        },
        "raw": {
            "accounts": accounts,
            "transactions": transactions,
        },
    }

    return snapshot
