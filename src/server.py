import fastapi
from fastapi import Body
import json
import uvicorn
import time
import os
import threading
import logging
from pprint import pprint
from typing import List, Dict

from blockchain import Account, Transaction
from utils import get_current_time

with open('config.json') as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)


def tx_from_payload(payload: dict) -> Transaction:
    return Transaction(
        sender_id=payload["sender_id"],
        recipient_id=payload["recipient_id"],
        amount=payload["amount"],
        sender_logic_clock=payload.get("sender_logic_clock", 0),
        timestamp=payload.get("timestamp"),
        status=payload.get("status", "PENDING"),
        num_replies=payload.get("num_replies", 0),
    )


class BankServer:
    """Bank server"""
    __instance = None  # Singleton pattern

    def __new__(cls, *args, **kwargs):
        if not cls.__instance:
            cls.__instance = super().__new__(cls, *args, **kwargs)
        return cls.__instance

    def __init__(self) -> None:
        self.accounts: List[Account] = []
        self.transactions: List[Transaction] = []
        self.clients: Dict[int, str] = {}

        self.ipv4 = CONFIG['HOST_IPv4']
        self.port = CONFIG['HOST_PORT']

        self.router = fastapi.APIRouter()
        self.router.add_api_route('/', self.root, methods=['GET'])
        self.router.add_api_route('/register', self.register, methods=['GET'])
        self.router.add_api_route('/balance/{client_id}', self.balance, methods=['GET'])
        self.router.add_api_route('/transfer', self.transfer, methods=['POST'])
        self.router.add_api_route('/exit/{client_id}', self.shutdown_client, methods=['GET'])

    # ---------------- ordinary functions ----------------
    def prompt(self):
        time.sleep(1)
        print('Welcome to the blockchain bank server!')
        print('Commands:')
        print('  1. print_table (or print, p)')
        print('  2. exit (or quit, q)')
        print('Enter a command:')

    def interact(self):
        while True:
            cmd = input('>>> ').strip()
            if cmd in ['exit', 'quit', 'q']:
                print('Exiting...')
                os._exit(0)
            elif cmd in ['print_table', 'print', 'p']:
                self.print_balance_table()
            elif cmd == 'clients':
                print(self.clients)
            else:
                print('Invalid command')
            print()

    def print_balance_table(self):
        self._check_account_info()
        print('client_id\tbalance\trecent_access_time')
        for account in self.accounts:
            print('        {}\t{}\t{}'.format(account.id, account.balance, account.recent_access_time))

    def __repr__(self) -> str:
        return 'BankServer(num_accounts={}, num_transactions={})'.format(len(self.accounts), len(self.transactions))

    def _check_account_info(self):
        assert len(self.accounts) == len(self.clients)
        ids = [a.id for a in self.accounts]
        assert len(set(ids)) == len(ids)

    # ---------------- view functions ----------------
    async def root(self):
        return {'message': 'Welcome to the blockchain bank server!'}

    async def balance(self, client_id: int):
        self._check_account_info()
        account = [a for a in self.accounts if a.id == client_id][0]
        account.recent_access_time = get_current_time()
        return {'balance': account.balance}

    async def register(self):
        self._check_account_info()

        ids = [a.id for a in self.accounts]
        client_id = max(ids) + 1 if ids else 1

        # IMPORTANT: your Account is a normal class, so create it positionally
        self.accounts.append(Account(client_id))  # balance defaults to 10.0

        other_clients = self.clients.copy()
        self.clients[client_id] = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'] + client_id)

        print('New client: {}, now all clients:'.format(client_id))
        pprint(self.clients)

        return {
            'client_id': client_id,
            'other_clients': other_clients,
            'server_addr': f'http://{self.ipv4}:{self.port}'
        }

    async def transfer(self, payload: dict = Body(...)):
        """Transfer money from sender to recipient (payload is JSON dict)."""
        self._check_account_info()

        transaction = tx_from_payload(payload)

        sender = [a for a in self.accounts if a.id == transaction.sender_id][0]
        recipient = [a for a in self.accounts if a.id == transaction.recipient_id][0]

        sender.recent_access_time = get_current_time()
        recipient.recent_access_time = sender.recent_access_time

        # optional validation
        if transaction.amount < 0:
            return {'result': 'fail', 'reason': 'amount must be positive'}
        if sender.balance < transaction.amount:
            return {'result': 'fail', 'reason': 'insufficient balance'}

        sender.balance -= transaction.amount
        recipient.balance += transaction.amount

        self.transactions.append(transaction)

        logging.info(
            'Bank server record updated: Client %s transferring %s to client %s',
            transaction.sender_id, transaction.amount, transaction.recipient_id
        )
        return {'result': 'success'}

    async def shutdown_client(self, client_id: int):
        self.clients.pop(client_id, None)
        self.accounts = [a for a in self.accounts if a.id != client_id]
        print('Client {} shutdown, now all clients:'.format(client_id))
        pprint(self.clients)
        return {'result': 'success'}


if __name__ == '__main__':
    server = BankServer()
    app = fastapi.FastAPI()
    app.include_router(server.router)

    threading.Thread(target=uvicorn.run, kwargs={
        'app': app,
        'host': server.ipv4,
        'port': server.port,
        'log_level': 'warning'
    }).start()

    print('Bank server:', server)
    server.prompt()
    server.interact()
