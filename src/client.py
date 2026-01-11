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
from typing import List, Dict, Optional
from datetime import timedelta
from pathlib import Path

from utils import get_current_time, get_host_ip
from blockchain import BlockChain, Transaction
from fastapi import Body
from fastapi.responses import HTMLResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import threading, time, os
import requests

CONFIG_PATH = Path(__file__).with_name('config.json')
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=logging.INFO, filename='.\\result\\activity.log',
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

    def __init__(self, id, ipv4, port, server_addr, other_clients, public_addr: str) -> None:
        self.id = id
        self.ipv4 = ipv4
        self.port = port
        self.public_addr = public_addr
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
        return 'Client(id={}, bind_host={}, port={}, public_addr={}, logic_clock={}, create_time={})'.format(
            self.id, self.ipv4, self.port, self.public_addr, self.logic_clock, self.create_time
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
        print('Or, Interact With CLI')
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
        reqs = [
            grequests.get(addr + f"/exit/{self.id}", timeout=5)
            for addr in self.other_clients.values()
        ]

        res_list = grequests.map(reqs, exception_handler=lambda req, exc: None)

        failed = []
        for addr, res in zip(self.other_clients.values(), res_list):
            if res is None or res.status_code != 200:
                failed.append(addr)

        if failed:
            print("Warning: could not notify these peers on exit:", failed)

        print(f"Client {self.id} exited.")


def register_client(server_addr) -> Client:
    """Register a new client with the server."""
    print('Registering client to server {}...'.format(server_addr))

    res = requests.get(server_addr + '/register', timeout=5)
    assert res.status_code == 200

    info = res.json()
    client_id = int(info['client_id'])

    # Determine how this client should be reachable by OTHER machines.
    public_ip = CONFIG.get('CLIENT_PUBLIC_IPv4', 'auto')
    if public_ip == 'auto':
        public_ip = get_host_ip()

    client_port = int(CONFIG.get('CLIENT_BASE_PORT', 8000)) + client_id
    client_addr = 'http://{}:{}'.format(public_ip, client_port)

    other_clients = dict((int(k), v) for k, v in info['other_clients'].items())
    server_addr = info.get('server_addr', server_addr)

    # Tell the server where it can reach this client (so future clients can discover it).
    try:
        r = requests.post(
            server_addr + '/register-confirm',
            json={'client_id': client_id, 'client_addr': client_addr},
            timeout=5,
        )
        if r.status_code != 200:
            print('Warning: server did not accept register-confirm:', r.text)
    except Exception as e:
        print('Warning: could not call /register-confirm:', e)

    print('Client {} registered'.format(client_id))
    print('  Public address:', client_addr)
    print('  Web UI:', client_addr + '/ui')
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
        CONFIG.get('CLIENT_BIND_HOST', '0.0.0.0'),
        port=client_port,
        server_addr=server_addr,
        other_clients=other_clients,
        public_addr=client_addr,
    )


app = fastapi.FastAPI()
_server_ip = CONFIG.get('SERVER_IPv4') or CONFIG.get('HOST_IPv4', '127.0.0.1')
_server_port = int(CONFIG.get('SERVER_PORT', CONFIG.get('HOST_PORT', 8000)))
server_address = f'http://{_server_ip}:{_server_port}'
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

@app.post("/ui/quit")
async def ui_quit():
    failed = []
    for cid, addr in client.other_clients.items():
        try:
            r = requests.get(f"{addr}/exit/{client.id}", timeout=2)
            if r.status_code != 200:
                failed.append(addr)
        except Exception:
            failed.append(addr)
    try:
        requests.get(f"{client.server_addr}/exit/{client.id}", timeout=2)
    except Exception:
        pass
    def die():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=die, daemon=True).start()
    return {"ok": True, "failed_peers": failed}

# -----------------------------
# Web UI (runs inside FastAPI)
# -----------------------------

UI_HTML = """<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Lamport Ledger (Blockchain-based Lamport Logical Clocks)</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 20px; }
      .row { display: flex; flex-wrap: wrap; gap: 16px; }
      .card { border: 1px solid #ddd; border-radius: 12px; padding: 14px; min-width: 320px; flex: 1; }
      .muted { color: #666; }
      code { background: #f6f6f6; padding: 2px 6px; border-radius: 6px; }
      input { padding: 8px 10px; border: 1px solid #ccc; border-radius: 10px; width: 140px; }
      button { padding: 8px 12px; border: 1px solid #ccc; border-radius: 10px; cursor: pointer; }
      table { width: 100%; border-collapse: collapse; }
      th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; font-size: 14px; }
      pre { white-space: pre-wrap; word-break: break-word; background: #f6f6f6; padding: 10px; border-radius: 12px; }
      .ok { color: #0a7; }
      .warn { color: #c60; }
      .err { color: #c00; }
    </style>
  </head>
  <body>
    <h2>CSE707 Project: Lamport Ledger (Blockchain-based Lamport Logical Clocks)</h2>
    <p class=\"muted\">Developed by Rafiad Sadat Shahir [20101580], Zayed Humayun [20141030], Abdullah Khondoker [20301065]</p>
    <p class=\"muted\" style=\"font-style: italic\">Open this page on any device that can reach this client. (Example: <code>http://&lt;client-ip&gt;:&lt;port&gt;/ui</code>)</p>

    <div class=\"row\">
      <div class=\"card\">
        <h3>Actions</h3>
        <div style=\"display:flex; gap:10px; align-items:center; flex-wrap:wrap\">
          <div>
            <div class=\"muted\">Recipient ID</div>
            <input id=\"recipient\" type=\"number\" min=\"1\" placeholder=\"e.g. 2\" />
          </div>
          <div>
            <div class=\"muted\">Amount</div>
            <input id=\"amount\" type=\"number\" min=\"0\" step=\"0.01\" placeholder=\"e.g. 5\" />
          </div>
          <div style=\"margin-top:18px\">
            <button onclick=\"sendTransfer()\">Send Transfer</button>
            <button onclick=\"refresh()\">Refresh</button>
            <button onclick=\"quit()\">Quit</button>
          </div>
        </div>
        <p id=\"status\" class=\"muted\" style=\"margin-top:12px\">&nbsp;</p>
      </div>

      <div class=\"card\">
        <h3>Client State</h3>
        <table>
          <tbody>
            <tr><th>Client ID</th><td id=\"client_id\">-</td></tr>
            <tr><th>Public Address</th><td id=\"public_addr\">-</td></tr>
            <tr><th>Server Address</th><td id=\"server_addr\">-</td></tr>
            <tr><th>Lamport Clock</th><td id=\"logic_clock\">-</td></tr>
            <tr><th>Balance</th><td id=\"balance\">-</td></tr>
            <tr><th>Peers</th><td id=\"peers\">-</td></tr>
            <tr><th>Replies Needed</th><td id=\"replies_needed\">-</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class=\"row\" style=\"margin-top:16px\">
      <div class=\"card\">
        <h3>Lamport Queues</h3>
        <pre id=\"queues\">Loading...</pre>
      </div>
      <div class=\"card\">
        <h3>Blockchain (latest)</h3>
        <pre id=\"chain\">Loading...</pre>
      </div>
    </div>

    <script>
        async function quit() {
            if (!confirm("Quit this client node?")) return;

            document.getElementById('status').textContent = 'Quitting...';
            document.getElementById('status').className = 'muted';

            try {
                await fetch('/ui/quit', { method: 'POST' });
                document.getElementById('status').textContent = 'Quit requested. You can close this tab.';
                document.getElementById('status').className = 'ok';
            } catch (e) {
                document.getElementById('status').textContent = 'Quit failed: ' + e;
                document.getElementById('status').className = 'err';
            }
            }
      async function refresh() {
        try {
          const res = await fetch('/ui/state');
          const data = await res.json();

          document.getElementById('client_id').textContent = data.client_id;
          document.getElementById('public_addr').textContent = data.public_addr;
          document.getElementById('server_addr').textContent = data.server_addr;
          document.getElementById('logic_clock').textContent = data.logic_clock;
          document.getElementById('balance').textContent = (data.balance === null) ? 'N/A' : data.balance;
          document.getElementById('peers').textContent = `${data.num_peers} (${Object.keys(data.other_clients).join(', ') || 'none'})`;
          document.getElementById('replies_needed').textContent = data.replies_needed;

          document.getElementById('queues').textContent = JSON.stringify({
            sending_queue: data.sending_queue,
            message_queue: data.message_queue
          }, null, 2);

          document.getElementById('chain').textContent = JSON.stringify(data.blockchain, null, 2);
        } catch (e) {
          document.getElementById('status').textContent = 'Failed to refresh: ' + e;
          document.getElementById('status').className = 'err';
        }
      }

      async function sendTransfer() {
        const recipient = parseInt(document.getElementById('recipient').value, 10);
        const amount = parseFloat(document.getElementById('amount').value);

        if (!recipient || !amount || amount <= 0) {
          document.getElementById('status').textContent = 'Please enter a valid recipient and positive amount.';
          document.getElementById('status').className = 'warn';
          return;
        }

        document.getElementById('status').textContent = 'Transfer queued...';
        document.getElementById('status').className = 'muted';

        const res = await fetch('/ui/transfer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ recipient_id: recipient, amount })
        });
        const data = await res.json();

        if (data.result === 'success') {
          document.getElementById('status').textContent = 'Transfer request broadcasted. (Processing will complete when you reach the head of the queue.)';
          document.getElementById('status').className = 'ok';
        } else {
          document.getElementById('status').textContent = 'Transfer failed to start: ' + (data.error || 'unknown');
          document.getElementById('status').className = 'err';
        }
        setTimeout(refresh, 300);
      }

      refresh();
      setInterval(refresh, 1000);
    </script>
  </body>
</html>"""


@app.get('/ui', response_class=HTMLResponse)
async def ui_page():
    return UI_HTML


def _balance_without_clock() -> Optional[float]:
    try:
        res = requests.get(f"{client.server_addr}/balance/{client.id}", timeout=2)
        if res.status_code != 200:
            return None
        return float(res.json().get('balance'))
    except Exception:
        return None


@app.get('/ui/state')
async def ui_state():
    # show last 5 blocks (if available)
    last_blocks = []
    for b in client.chain.chain[-5:]:
        last_blocks.append({
            'transaction': b.transaction.to_dict(),
            'previous_hash': b.previous_hash,
            'hash': b.hash(),
        })

    return {
        'client_id': client.id,
        'public_addr': client.public_addr,
        'server_addr': client.server_addr,
        'logic_clock': client.logic_clock,
        'balance': _balance_without_clock(),
        'num_peers': len(client.other_clients),
        'other_clients': client.other_clients,
        'replies_needed': len(client.other_clients),
        'sending_queue': [t.to_dict() for t in client.sending_queue],
        'message_queue': [t.to_dict() for t in client.message_queue],
        'blockchain': {
            'total_blocks': len(client.chain.chain),
            'latest_blocks': last_blocks,
        }
    }


@app.post('/ui/transfer')
async def ui_transfer(payload: dict = Body(...)):
    try:
        recipient_id = int(payload.get('recipient_id'))
        amount = float(payload.get('amount'))
    except Exception:
        return {'result': 'fail', 'error': 'invalid payload'}

    def _run():
        try:
            client.transfer(recipient_id, amount)
        except Exception as e:
            print('UI transfer failed:', e)

    threading.Thread(target=_run, daemon=True).start()
    return {'result': 'success'}


if __name__ == '__main__':
    title = "CSE707 Project: Lamport Ledger (Blockchain-based Lamport Logical Clocks)"
    welcome_msg = "Welcome LamportLedger client!"
    print("=" * len(title))
    print(title)
    print("=" * len(title))

    print('Registered client with server:')
    print(client)
    client.start()
    msg_process_loop.start()

    print("=" * len(welcome_msg))
    print(welcome_msg)
    print("=" * len(welcome_msg))
    print('  Public address:', client.public_addr)
    print('  Interact With Web UI (click this link):', client.public_addr + '/ui')
    print()

    client.interact()
