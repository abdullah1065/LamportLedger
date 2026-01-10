import os
import re
import json
import time
import fastapi
import uvicorn
import logging
import requests
import grequests
import warnings
import threading
import timeloop
from typing import List, Dict
from datetime import timedelta
from utils import get_current_time
from blockchain import BlockChain, Transaction
from fastapi import Body

with open('config.json') as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='../result/activity.log',
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)

TRANSFER_DELAY = 3  # seconds


def tx_from_payload(payload: dict) -> Transaction:
    """Convert incoming JSON dict to local Transaction object (no pydantic)."""
    return Transaction(
        sender_id=payload["sender_id"],
        recipient_id=payload["recipient_id"],
        amount=payload["amount"],
        sender_logic_clock=payload["sender_logic_clock"],
        timestamp=payload.get("timestamp"),
        status=payload.get("status", "PENDING"),
        num_replies=payload.get("num_replies", 0),
    )


class Client:
    """Each user can be regarded as a Client instance"""

    def __init__(self, id, ipv4, port, server_addr, other_clients) -> None:
        self.id = id
        self.ipv4 = ipv4
        self.port = port
        self.logic_clock = 0
        self.create_time = get_current_time()
        self.server_addr = server_addr
        self.other_clients: Dict[int, str] = other_clients
        self.chain = BlockChain()

        self.sending_queue: List[Transaction] = []
        self.message_queue: List[Transaction] = []

    def start(self):
        """Start the client (FastAPI Web application)"""
        threading.Thread(target=uvicorn.run, kwargs={
            'app': app,
            'host': self.ipv4,
            'port': self.port,
            'log_level': 'warning'
        }).start()

    def sending_queue_str(self):
        return ['Transaction({})'.format(trans.to_tuple()) for trans in self.sending_queue]

    def message_queue_str(self):
        return ['Transaction({})'.format(trans.to_tuple()) for trans in self.message_queue]

    def all_info(self):
        return """
        {}
            other_clients={},
            sending_queue={},
            message_queue={},
        """.format(self, self.other_clients,
                   self.sending_queue_str(),
                   self.message_queue_str())

    def __repr__(self) -> str:
        return 'Client(id={}, ipv4={}, port={}, logic_clock={}, create_time={})'.format(
            self.id, self.ipv4, self.port, self.logic_clock, self.create_time
        )

    def interact(self):
        """Begin user interacting"""
        self.prompt()
        while True:
            cmd = input('>>> ').strip().lower()
            if re.match(r'(exit|quit|q)$', cmd):
                print('Exiting...')
                self.shutdown()
                os._exit(0)

            elif re.match(r'^(transfer|t)\s+\d+\s+\d+(\.\d+)?$', cmd):
                try:
                    recipient, amount = cmd.split()[1:]
                except ValueError:
                    print('Invalid transfer command')
                    continue
                self.transfer(int(recipient), float(amount))

            elif re.match(r'(balance|bal|b)$', cmd):
                print('Client {}, Balance {}'.format(self.id, self.balance()))

            elif re.match(r'(print\s+chain|print_chain|print|p)$', cmd):
                self.chain.display()

            elif re.match(r'(all|printall|print_all|print\s+all)$', cmd):
                print(self.all_info())

            elif re.match(r'(m|msg|message)$', cmd):
                print('Sending queue: ', self.sending_queue_str())
                print('Processed message queue: ', self.message_queue_str())

            else:
                print('Invalid command')
            print()

    def prompt(self):
        time.sleep(1)
        print('Welcome to the blockchain client!')
        print('Commands:')
        print('  1. transfer <recipient> <amount> (e.g., transfer 2 100, or, t 2 100)')
        print('  2. balance (or bal, b)')
        print('  3. print_chain (or print, p)')
        print('  4. exit (or quit, q)')
        print('  5. all (print detailed info of the client)')
        print('  6. msg (or m; print message queue)')
        print('Enter a command:')

    def request_transaction(self):
        """Send 'request' and expect to receive 'reply' from each other client."""
        print('sending requests to other clients...:', self.other_clients)

        tx = self.sending_queue[-1]

        # Put own tx into local message queue (Lamport ordering)
        self.message_queue.append(tx)
        self.message_queue.sort(key=lambda t: (t.sender_logic_clock, t.sender_id))

        # Send to others
        reqs = [
            grequests.post(
                client_addr + '/transfer-request',
                json=tx.to_dict(),
                timeout=5
            )
            for client_addr in self.other_clients.values()
        ]
        res_list = grequests.map(reqs)

        # Require all replies to be 200
        assert all([res is not None and res.status_code == 200 for res in res_list])

        for res in res_list:
            if res.json().get('result') == 'success':
                tx.num_replies += 1

    def finish_transaction(self):
        """Send 'release' so others can remove the tx from their queues."""
        tx = self.sending_queue[0]
        reqs = [
            grequests.post(
                client_addr + '/transfer-finish',
                json=tx.to_dict(),
                timeout=5
            )
            for client_addr in self.other_clients.values()
        ]
        res_list = grequests.map(reqs)
        assert all([res is not None and res.status_code == 200 for res in res_list])

        print('Client {} finished transaction {}'.format(self.id, tx.to_tuple()))

    def transfer(self, recipient_id, amount):
        """Transfer transaction via Lamport mutual exclusion style messaging."""
        if recipient_id not in self.other_clients:
            warnings.warn('Recipient {} not in other clients'.format(recipient_id))
            return
        if amount < 0:
            warnings.warn('Amount must be positive')
            return

        print('Attempting transferring ${} from {} to {}'.format(amount, self.id, recipient_id))

        # Send event
        self.logic_clock += 1
        self.sending_queue.append(Transaction(
            sender_id=self.id,
            recipient_id=recipient_id,
            amount=amount,
            sender_logic_clock=self.logic_clock
        ))

        print('Client {} broadcasts transfer requests to {}; logic clock +1, now {}'.format(
            self.id, list(self.other_clients.keys()), self.logic_clock
        ))

        self.request_transaction()

    def balance(self, for_transfer=False):
        res = requests.get(self.server_addr + '/balance/{}'.format(self.id))
        assert res.status_code == 200
        if not for_transfer:
            self.logic_clock += 1
        print('Client {} logic clock +1, now {}'.format(self.id, self.logic_clock))
        return res.json()['balance']

    def shutdown(self):
        res = requests.get(self.server_addr + '/exit/{}'.format(self.id))
        assert res.status_code == 200

        reqs = [
            grequests.get(other_client_addr + '/exit/{}'.format(self.id), timeout=5)
            for other_client_addr in self.other_clients.values()
        ]
        res_list = grequests.map(reqs)
        assert all([res is not None and res.status_code == 200 for res in res_list])
        logging.info('Client {} exit the system'.format(self.id))


def register_client(server_addr) -> Client:
    """Register a new client with the server."""
    print('Registering client to server {}...'.format(server_addr))
    print('Registering client to server {}...'.format(server_addr))
    print('Registering client to server {}...'.format(server_addr))

    res = requests.get(server_addr + '/register')
    assert res.status_code == 200

    info = res.json()
    client_id = info['client_id']
    client_addr = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'] + client_id)

    other_clients = dict((int(k), v) for k, v in info['other_clients'].items())
    server_addr = info['server_addr']

    print('Client {} registered'.format(client_id))
    logging.info('Client {} registered'.format(client_id))

    # Notify existing clients. Don't crash if some are offline.
    def exception_handler(req, exc):
        print("Notify failed:", req.url, "error:", exc)
        return None

    reqs = [
        grequests.post(
            other_client_addr + '/register',
            json={'client_id': client_id, 'client_addr': client_addr},
            timeout=3
        )
        for other_client_addr in other_clients.values()
    ]
    res_list = grequests.map(reqs, exception_handler=exception_handler)

    failed = []
    for addr, r in zip(other_clients.values(), res_list):
        if r is None or r.status_code != 200:
            failed.append(addr)

    if failed:
        print("Warning: could not notify these clients:", failed)

    return Client(
        client_id,
        CONFIG['HOST_IPv4'],
        port=CONFIG['HOST_PORT'] + client_id,
        server_addr=server_addr,
        other_clients=other_clients
    )


app = fastapi.FastAPI()
server_address = 'http://{}:{}'.format(CONFIG['HOST_IPv4'], CONFIG['HOST_PORT'])
client = register_client(server_address)
msg_process_loop = timeloop.Timeloop()


@msg_process_loop.job(interval=timedelta(seconds=0.1))
def process_message():
    """Process transaction from the client itself."""
    if not client.message_queue or not client.sending_queue:
        return

    head_send = client.sending_queue[0]
    head_msg = client.message_queue[0]

    if head_send == head_msg and head_send.num_replies == len(client.other_clients):
        cur_balance = client.balance(for_transfer=True)
        print('Now processing transaction: {}'.format(head_send.to_tuple()))
        print('Before transfer: Client {}, Balance {}'.format(client.id, cur_balance))

        if cur_balance < head_send.amount:
            print('Transfer FAILURE (Insufficient balance)')
            head_send.status = 'ABORT'
            client.chain.add_transaction(head_send)
            client.finish_transaction()
        else:
            res = requests.post(client.server_addr + '/transfer', json=head_send.to_dict())
            assert res.status_code == 200
            assert res.json().get('result') == 'success'

            head_send.status = 'SUCCESS'
            client.chain.add_transaction(head_send)
            client.finish_transaction()

            print('After transfer: Client {}, Balance {}'.format(client.id, client.balance()))
            print('Transfer SUCCESS')

        # Remove from both queues
        client.message_queue.remove(client.sending_queue.pop(0))


@app.post('/transfer-request')
async def receive_transfer_request(payload: dict = Body(...)):
    """Receive a transfer request from another client."""
    time.sleep(TRANSFER_DELAY)
    tx = tx_from_payload(payload)

    client.message_queue.append(tx)
    client.message_queue.sort(key=lambda t: (t.sender_logic_clock, t.sender_id))

    client.logic_clock = max(client.logic_clock, tx.sender_logic_clock) + 1
    return {'result': 'success'}


@app.post('/transfer-finish')
async def receive_transfer_finish(payload: dict = Body(...)):
    """Receive a transfer finish from another client."""
    time.sleep(TRANSFER_DELAY)
    tx = tx_from_payload(payload)

    # Ensure it matches the stored instance by equality
    if tx in client.message_queue:
        client.message_queue.remove(tx)

    client.chain.add_transaction(tx)
    return {'result': 'success'}


@app.post('/register')
async def add_new_registered_client(client_id: int = Body(..., embed=True),
                                    client_addr: str = Body(..., embed=True)):
    """Add a new registered client."""
    client.other_clients.update({client_id: client_addr})
    print('New registered client {} at {} added this system'.format(client_id, client_addr))
    print('Now other clients: {}'.format(client.other_clients))
    return {'result': 'success'}


@app.get('/exit/{client_id}')
async def remove_shutdown_client(client_id: int):
    """Remove a shutdown client."""
    client.other_clients.pop(client_id, None)
    print('Client {} exit the system'.format(client_id))
    print('Now other clients: {}'.format(client.other_clients))
    return {'result': 'success'}


if __name__ == '__main__':
    print('Registered client with server:')
    print(client)
    client.start()
    msg_process_loop.start()
    client.interact()
