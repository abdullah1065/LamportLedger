import json
import logging
import threading
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
import uvicorn

from blockchain import Account, Transaction

CONFIG_PATH = Path(__file__).with_name("config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="CSE707 Bank Server")

_lock = threading.Lock()
_accounts: Dict[int, Account] = {}
_clients_ip: Dict[int, str] = {}  # client_id -> ip


def _server_addr() -> str:
    return f"http://{CONFIG['SERVER_IPv4']}:{CONFIG['SERVER_PORT']}"


def _client_addr(client_id: int) -> str:
    ip = _clients_ip[client_id]
    port = CONFIG["CLIENT_BASE_PORT"] + client_id
    return f"http://{ip}:{port}"


@app.get("/")
async def health():
    return {"ok": True, "server_addr": _server_addr(), "num_clients": len(_clients_ip)}


@app.get("/register")
async def register(request: Request):
    # Identify the client machine's IP as seen by the server
    client_ip = request.client.host

    with _lock:
        # allocate next id (1,2,3,...)
        client_id = 1
        if _accounts:
            client_id = max(_accounts.keys()) + 1

        _accounts[client_id] = Account(id=client_id, balance=10.0)
        _clients_ip[client_id] = client_ip

        other_clients = {cid: _client_addr(cid) for cid in _clients_ip.keys() if cid != client_id}

    logging.info("Registered client %s from %s", client_id, client_ip)
    return {"client_id": client_id, "other_clients": other_clients, "server_addr": _server_addr()}


@app.get("/balance/{client_id}")
async def balance(client_id: int):
    with _lock:
        if client_id not in _accounts:
            return {"error": "unknown client_id"}
        return {"balance": _accounts[client_id].balance}


@app.post("/transfer")
async def transfer(payload: dict):
    tx = Transaction(**payload)

    with _lock:
        if tx.sender_id not in _accounts or tx.recipient_id not in _accounts:
            return {"result": "failure", "error": "unknown client id"}

        sender = _accounts[tx.sender_id]
        recipient = _accounts[tx.recipient_id]

        # server trusts clients to have already used Lamport mutual exclusion
        if sender.balance < tx.amount:
            return {"result": "failure", "error": "insufficient balance"}

        sender.balance -= tx.amount
        recipient.balance += tx.amount

    logging.info("Transfer %s -> %s amount=%s", tx.sender_id, tx.recipient_id, tx.amount)
    return {"result": "success"}


@app.get("/exit/{client_id}")
async def exit_client(client_id: int):
    with _lock:
        _clients_ip.pop(client_id, None)
        # Keep account for grading/demo or remove; here we keep it.
    logging.info("Client %s exited", client_id)
    return {"result": "success"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(CONFIG["SERVER_PORT"]), log_level="info")
