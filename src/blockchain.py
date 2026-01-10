import json
import hashlib
from typing import List, Optional
from utils import get_current_time


class Transaction:
    def __init__(
        self,
        sender_id: int,
        recipient_id: int,
        amount: float,
        sender_logic_clock: int,
        timestamp: Optional[str] = None,
        status: str = "PENDING",
        num_replies: int = 0,
    ) -> None:
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        self.amount = amount
        self.sender_logic_clock = sender_logic_clock
        self.timestamp = timestamp or get_current_time()
        self.status = status
        self.num_replies = num_replies

    def __eq__(self, other) -> bool:
        return (
            self.sender_id == other.sender_id
            and self.recipient_id == other.recipient_id
            and self.amount == other.amount
            and self.sender_logic_clock == other.sender_logic_clock
            and self.timestamp == other.timestamp
        )

    def to_tuple(self):
        return (self.sender_id, self.recipient_id, self.amount)

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "amount": self.amount,
            "sender_logic_clock": self.sender_logic_clock,
            "timestamp": self.timestamp,
            "status": self.status,
            "num_replies": self.num_replies,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class Account:
    def __init__(self, id: int, balance: float = 10.0, recent_access_time: Optional[str] = None) -> None:
        self.id = id
        self.balance = balance
        self.recent_access_time = recent_access_time or get_current_time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "balance": self.balance,
            "recent_access_time": self.recent_access_time,
        }


class Block:
    def __init__(self, transaction: Transaction, previous_hash: Optional[str] = None) -> None:
        self.transaction: Transaction = transaction
        self.previous_hash = previous_hash if previous_hash else hashlib.sha256(b"").hexdigest()
        self.next_block: Optional["Block"] = None

    def __repr__(self):
        return f"Block(transaction={self.transaction.to_tuple()}, timestamp={self.transaction.timestamp})"

    def hash(self) -> str:
        payload = {
            "transaction": self.transaction.to_dict(),
            "previous_hash": self.previous_hash,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class BlockChain:
    def __init__(self) -> None:
        self.chain: List[Block] = []

    def __repr__(self):
        blocks_str = ""
        for block in self.chain:
            blocks_str += str(block) + ",\n\t"
        return "BlockChain([\n\t{}\n])".format(blocks_str.strip())

    def display(self):
        print("-" * 20)
        print("Total blocks: {}".format(len(self.chain)))
        print("-" * 10)
        for i, block in enumerate(self.chain):
            print("Block {}:".format(i))
            print("  transaction: {}".format(block.transaction.to_dict()))
            print("  previous_hash: {}".format(block.previous_hash))
            print("  hash: {}".format(block.hash()))
            print("  next_block: {}".format(block.next_block))
            print()

    def add_transaction(self, transaction: Transaction):
        self.chain.append(Block(transaction))
        self.resort_blocks()

    def resort_blocks(self):
        self.chain.sort(key=lambda b: (b.transaction.sender_logic_clock, b.transaction.sender_id))

        if self.chain:
            self.chain[0].previous_hash = hashlib.sha256(b"").hexdigest()

        for i, block in enumerate(self.chain[:-1]):
            block.next_block = self.chain[i + 1]
            block.next_block.previous_hash = block.hash()

        if self.chain:
            self.chain[-1].next_block = None
