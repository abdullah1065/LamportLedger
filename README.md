# CSE707 Project: Lamport Ledger (Blockchain-based Lamport Logical Clocks)

Each **client node** runs a FastAPI app that:
- participates in Lamport mutual exclusion for transfers
- maintains a local blockchain-like ordered log (sorted by Lamport time)

A separate **bank server**:
- assigns client IDs
- stores each client's reachable address (peer discovery)
- maintains account balances

## Quick start (same machine)

1) Install dependencies
```bash
pip install -r requirements.txt
```

2) Start the server
```bash
python src/server.py
```

3) Start 2–3 clients (in separate terminals)
```bash
python src/client.py
```

4) Open the Web UI for each client

After a client registers, it listens on `CLIENT_BASE_PORT + client_id`.

Example (Client 1):
```
http://127.0.0.1:8001/ui
```

## Multi-PC / distributed run (LAN)

### A) Choose the server PC
Find the server PC LAN IP (example: `192.168.0.10`).

### B) Update `src/config.json` on **ALL** machines
Set `SERVER_IPv4` to the server PC IP.

```json
{
  "SERVER_IPv4": "192.168.0.10",
  "SERVER_PORT": 8000,

  "CLIENT_BIND_HOST": "0.0.0.0",
  "CLIENT_PUBLIC_IPv4": "auto",
  "CLIENT_BASE_PORT": 8000
}
```

### C) Run

On the server PC:
```bash
python src/server.py
```

On each client PC:
```bash
python src/client.py
```

Then open each client's UI:
- `http://<client-ip>:8001/ui`
- `http://<client-ip>:8002/ui`
- ...

### Firewall notes
Allow inbound TCP:
- server: `8000`
- clients: `8001..8000+N`

## Running over the internet (different networks)
Because clients must reach each other, use a VPN like **Tailscale** or **ZeroTier** and set `SERVER_IPv4` to the VPN IP.
