import json
import logging
from pathlib import Path
from typing import Dict

import fastapi
import uvicorn
from fastapi import Body

from blockchain import Account, Transaction


# Load config relative to this file so running from any working directory works.
CONFIG_PATH = Path(__file__).with_name("config.json")
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lamportLedger-server")


class lamportLedgerServer:
    """lamportLedger/registry server.

    Responsibilities:
    - Assign client IDs
    - Track each client's reachable address for peer-to-peer messaging
    - Maintain account balances
    """

    def __init__(self) -> None:
        self.accounts: Dict[int, Account] = {}
        self.clients: Dict[int, str] = {}  # client_id -> http://ip:port

        # Basic settings
        self.bind_host = "0.0.0.0"
        self.port = int(CONFIG.get("SERVER_PORT", 8000))

        self.router = fastapi.APIRouter()
        self.router.add_api_route("/", self.root, methods=["GET"])
        self.router.add_api_route("/register", self.register, methods=["GET"])
        self.router.add_api_route(
            "/register-confirm", self.register_confirm, methods=["POST"]
        )
        self.router.add_api_route("/balance/{client_id}", self.balance, methods=["GET"])
        self.router.add_api_route("/transfer", self.transfer, methods=["POST"])
        self.router.add_api_route("/exit/{client_id}", self.exit, methods=["GET"])

    async def root(self):
        return {
            "ok": True,
            "num_clients": len(self.clients),
            "clients": self.clients,
        }

    async def register(self):
        """Allocate an ID and return currently-known peers.

        Note: the client must call /register-confirm afterwards to provide its address.
        """

        new_id = 1 if not self.accounts else (max(self.accounts.keys()) + 1)
        self.accounts[new_id] = Account(id=new_id)  # default balance is set in Account

        # Server address is what clients should use to call server endpoints.
        server_ipv4 = CONFIG.get("SERVER_IPv4", "127.0.0.1")
        server_addr = f"http://{server_ipv4}:{self.port}"

        logger.info("Allocated client_id=%s", new_id)
        return {
            "client_id": new_id,
            "other_clients": self.clients,
            "server_addr": server_addr,
        }

    async def register_confirm(
        self,
        client_id: int = Body(..., embed=True),
        client_addr: str = Body(..., embed=True),
    ):
        self.clients[int(client_id)] = str(client_addr)
        logger.info("Registered client %s at %s", client_id, client_addr)
        return {"result": "success"}

    async def balance(self, client_id: int):
        client_id = int(client_id)
        if client_id not in self.accounts:
            return fastapi.responses.JSONResponse(
                status_code=404, content={"error": "unknown client"}
            )
        return {"balance": float(self.accounts[client_id].balance)}

    async def transfer(self, payload: dict = Body(...)):
        tx = Transaction(
            sender_id=payload["sender_id"],
            recipient_id=payload["recipient_id"],
            amount=payload["amount"],
            sender_logic_clock=payload["sender_logic_clock"],
            timestamp=payload.get("timestamp"),
            status=payload.get("status", "PENDING"),
            num_replies=payload.get("num_replies", 0),
        )

        s = int(tx.sender_id)
        r = int(tx.recipient_id)
        if s not in self.accounts or r not in self.accounts:
            return fastapi.responses.JSONResponse(
                status_code=404, content={"result": "fail", "error": "unknown account"}
            )

        # IMPORTANT: Client already checks insufficient balance before calling /transfer.
        # Here we just apply the change.
        self.accounts[s].balance -= float(tx.amount)
        self.accounts[r].balance += float(tx.amount)

        logger.info("Transfer: %s -> %s amount=%s", s, r, tx.amount)
        return {"result": "success"}

    async def exit(self, client_id: int):
        client_id = int(client_id)
        self.clients.pop(client_id, None)
        logger.info("Client %s removed", client_id)
        return {"result": "success"}


def main():
    lamportLedger = lamportLedgerServer()
    app = fastapi.FastAPI(title="LamportLedger Server")
    app.include_router(lamportLedger.router)
    uvicorn.run(app, host=lamportLedger.bind_host, port=lamportLedger.port, log_level="info")


if __name__ == "__main__":
    main()
