import os
from typing import List, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# -----------------------------
# Config Pluggy
# -----------------------------

PLUGGY_BASE_URL = "https://api.pluggy.ai"

# Esses 2 valores vêm das variáveis do Railway
PLUGGY_CLIENT_ID = os.getenv("PLUGGY_CLIENT_ID")
PLUGGY_CLIENT_SECRET = os.getenv("PLUGGY_CLIENT_SECRET")


def get_pluggy_access_token() -> str:
    """
    Autentica na Pluggy usando clientId + clientSecret e devolve um accessToken.
    Esse token é usado como Bearer nas chamadas de /accounts e /transactions.
    """
    if not PLUGGY_CLIENT_ID or not PLUGGY_CLIENT_SECRET:
        raise RuntimeError("PLUGGY_CLIENT_ID ou PLUGGY_CLIENT_SECRET não configurados")

    url = f"{PLUGGY_BASE_URL}/auth"
    payload = {
        "clientId": PLUGGY_CLIENT_ID,
        "clientSecret": PLUGGY_CLIENT_SECRET,
    }

    resp = requests.post(url, json=payload, timeout=30)

    if resp.status_code >= 400:
        raise RuntimeError(f"Erro ao autenticar na Pluggy: {resp.status_code} {resp.text}")

    data = resp.json()
    token = data.get("accessToken")
    if not token:
        raise RuntimeError("Resposta de auth da Pluggy não trouxe accessToken")

    return token


def get_pluggy_headers() -> Dict[str, str]:
    """
    Headers padrão para chamar a API de dados da Pluggy (accounts, transactions, etc.).
    Usa Bearer token gerado por /auth.
    """
    token = get_pluggy_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# -----------------------------
# FastAPI App
# -----------------------------

app = FastAPI(title="CFO Pluggy API", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------------
# Models
# -----------------------------

class ConnectTokenRequest(BaseModel):
    user_id: str


class ConnectTokenResponse(BaseModel):
    connectToken: str
    raw: Dict[str, Any]


# -----------------------------
# Helpers Pluggy
# -----------------------------

def create_connect_token(user_id: str) -> Dict[str, Any]:
    """
    Chama a Pluggy para criar um Connect Token
    que será usado no widget de conexão do banco.
    """
    if not PLUGGY_CLIENT_ID or not PLUGGY_CLIENT_SECRET:
        raise RuntimeError("PLUGGY_CLIENT_ID ou PLUGGY_CLIENT_SECRET não configurados")

    url = f"{PLUGGY_BASE_URL}/connect_token"

    payload = {
        "clientId": PLUGGY_CLIENT_ID,
        "clientSecret": PLUGGY_CLIENT_SECRET,
        "userId": user_id,
    }

    resp = requests.post(url, json=payload, timeout=30)

    if resp.status_code >= 400:
        raise RuntimeError(
            f"Erro ao criar connect token: {resp.status_code} {resp.text}"
        )

    return resp.json()


def fetch_accounts_by_item(item_id: str) -> List[Dict[str, Any]]:
    """
    Busca TODAS as contas de um item usando apenas itemId.
    """
    url = f"{PLUGGY_BASE_URL}/accounts"
    headers = get_pluggy_headers()

    params = {
        "itemId": item_id,
        "pageSize": 100,
    }

    all_accounts: List[Dict[str, Any]] = []
    cursor = None

    while True:
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Erro ao listar contas: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        all_accounts.extend(data.get("results", []))

        cursor = data.get("nextCursor")
        if not cursor:
            break

    return all_accounts


def fetch_transactions_by_item(item_id: str) -> List[Dict[str, Any]]:
    """
    Busca TODAS as transações de um item usando apenas itemId.
    Isso evita o erro 'accountid should not be null or undefined'.
    """
    url = f"{PLUGGY_BASE_URL}/transactions"
    headers = get_pluggy_headers()

    params = {
        "itemId": item_id,
        "pageSize": 500,
    }

    all_txs: List[Dict[str, Any]] = []
    cursor = None

    while True:
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(url, headers=headers, params=params, timeout=60)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Erro ao listar transações: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        all_txs.extend(data.get("results", []))

        cursor = data.get("nextCursor")
        if not cursor:
            break

    return all_txs


# -----------------------------
# Endpoints
# -----------------------------

@app.post(
    "/pluggy/connect-token",
    response_model=ConnectTokenResponse,
    summary="Cria um connectToken da Pluggy para abrir o widget",
)
def api_create_connect_token(body: ConnectTokenRequest):
    try:
        data = create_connect_token(body.user_id)
        # A resposta padrão da Pluggy costuma ter 'accessToken',
        # aqui empacotamos num formato mais amigável pro front.
        return {
            "connectToken": data.get("accessToken") or data.get("connectToken"),
            "raw": data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/users/{user_id}/snapshot",
    summary="Snapshot financeiro do usuário para 1 item (banco) na Pluggy",
)
def get_snapshot(user_id: str, item_id: str):
    """
    Monta um snapshot simples a partir das contas + transações do item.
    - item_id vem da Pluggy (ex: 438973f7-4d7d-4d21-8a1c-958e1482cf82)
    """
    try:
        # 1) Contas
        accounts = fetch_accounts_by_item(item_id)

        # saldo total das contas (campo 'balance' se existir)
        saldo_total = 0.0
        for acc in accounts:
            balance = acc.get("balance") or {}
            if isinstance(balance, dict):
                valor = balance.get("current") or balance.get("available") or 0
            else:
                valor = balance or 0
            saldo_total += float(valor)

        # 2) Transações
        transactions = fetch_transactions_by_item(item_id)

        total_entradas = 0.0
        total_saidas = 0.0

        for tx in transactions:
            amount = float(tx.get("amount") or 0)
            if amount > 0:
                total_entradas += amount
            elif amount < 0:
                total_saidas += amount

        snapshot = {
            "user_id": user_id,
            "item_id": item_id,
            "saldo_total_contas": saldo_total,
            "fluxo_geral": {
                "total_entradas": total_entradas,
                "total_saidas": total_saidas,
                "saldo_movimento": total_entradas + total_saidas,
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
