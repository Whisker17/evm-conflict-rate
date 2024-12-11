from web3 import Web3
import json
from datetime import datetime, timedelta
import time
from itertools import combinations


def get_transaction_trace(web3, tx_hash):
    # If tx_hash is an AttributeDict (transaction object), get the hash
    if hasattr(tx_hash, 'hash'):
        tx_hash = tx_hash.hash.hex()
    elif isinstance(tx_hash, HexBytes):
        tx_hash = tx_hash.hex()

    # Ensure hash starts with 0x
    if not tx_hash.startswith('0x'):
        tx_hash = '0x' + tx_hash

    trace = web3.provider.make_request("debug_traceTransaction", [
        tx_hash,  # Now it's a properly formatted hex string
        {
            "tracer": "prestateTracer",
            "tracerConfig": {
                "diffMode": False,
                "includeStorage": True,
                "includeStack": True,
                "includeLogs": True
            }
        }
    ])

    # Get transaction details
    tx = web3.eth.get_transaction(tx_hash)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)

    reads = set()
    writes = set()

    # Track prestate reads from all addresses
    for address, state in trace['result'].items():
        address = address.lower()

        # Track balance reads
        if 'balance' in state:
            reads.add((address, 'balance'))

        # Track nonce reads
        if 'nonce' in state:
            reads.add((address, 'nonce'))

        # Track storage reads
        if 'storage' in state:
            for slot in state['storage'].keys():
                reads.add((address, f"storage:{slot}"))

        # Track code reads (for contract calls)
        if 'code' in state:
            reads.add((address, 'code'))

    # Track writes from transaction receipt
    sender = tx['from'].lower()
    writes.add((sender, 'nonce'))  # Sender nonce always incremented
    # Sender balance always reduced (gas + value)
    writes.add((sender, 'balance'))

    # Track direct recipient
    if tx['to']:  # Not None (contract creation)
        recipient = tx['to'].lower()
        writes.add((recipient, 'balance'))  # Direct recipient balance change

    # Track all internal transactions (from trace)
    if 'calls' in trace['result']:
        for call in trace['result']['calls']:
            if 'value' in call and int(call['value'], 16) > 0:
                writes.add((call['from'].lower(), 'balance'))
                writes.add((call['to'].lower(), 'balance'))

    # Track contract creations
    if tx_receipt['contractAddress']:
        contract_addr = tx_receipt['contractAddress'].lower()
        writes.add((contract_addr, 'code'))
        writes.add((contract_addr, 'nonce'))
        writes.add((contract_addr, 'balance'))

    # Track storage writes from logs
    for log in tx_receipt['logs']:
        contract_addr = log['address'].lower()
        # Each log potentially indicates storage writes
        for topic in log['topics']:
            writes.add((contract_addr, f"storage:{topic.hex()}"))

    return {
        'reads': list(reads),
        'writes': list(writes),
        'sender': sender,
        'recipient': tx['to'].lower() if tx['to'] else None,
        'is_contract_creation': bool(tx_receipt['contractAddress']),
        'contract_address': tx_receipt['contractAddress'].lower() if tx_receipt['contractAddress'] else None,
        'logs': [log['address'].lower() for log in tx_receipt['logs']]
    }


def analyze_block_dependencies(web3, block):
    """Analyze all transaction dependencies within a block"""
    tx_hashes = block.transactions
    total_txs = len(tx_hashes)
    dependent_pairs = []

    # Get all possible pairs of transactions
    for tx1_hash, tx2_hash in combinations(tx_hashes, 2):
        try:
            result = are_transactions_dependent(web3, tx1_hash, tx2_hash)
            print(f"Trying transactions {tx1_hash['hash'].hex()} and {tx2_hash['hash'].hex()}")
            if result["is_dependent"]:
                dependent_pairs.append({
                    "tx1": tx1_hash,
                    "tx2": tx2_hash,
                    "conflicts": {
                        "write_conflicts": result["write_conflicts"],
                        "read_write_conflicts": result["read_write_conflicts"],
                        "write_read_conflicts": result["write_read_conflicts"]
                    }
                })
        except Exception as e:
            print(f"Error analyzing dependency between {tx1_hash} and {tx2_hash}: {str(e)}")

    return {
        "block_number": block.number,
        "timestamp": block.timestamp,
        "total_transactions": total_txs,
        "dependent_pairs": dependent_pairs,
        "total_dependent_pairs": len(dependent_pairs),
        "dependency_ratio": len(dependent_pairs) / (total_txs * (total_txs - 1) / 2) if total_txs > 1 else 0
    }


def get_blocks_last_24h(web3):
    """Get all blocks from the last 24 hours"""
    current_block = web3.eth.block_number
    current_block_data = web3.eth.get_block(current_block)
    current_time = current_block_data.timestamp
    target_time = current_time - (6)  # 24 hours ago

    blocks = []
    block_number = current_block

    while True:
        try:
            block = web3.eth.get_block(block_number, full_transactions=True)
            if block.timestamp < target_time:
                break
            blocks.append(block)
            block_number -= 1

            # Optional: Print progress
            if len(blocks) % 10 == 0:
                print(f"Processed {len(blocks)} blocks...")

        except Exception as e:
            print(f"Error fetching block {block_number}: {str(e)}")
            break

    return blocks


def are_transactions_dependent(web3, tx_hash1, tx_hash2):
    trace1 = get_transaction_trace(web3, tx_hash1)
    trace2 = get_transaction_trace(web3, tx_hash2)

    reads1 = set(trace1['reads'])
    writes1 = set(trace1['writes'])
    reads2 = set(trace2['reads'])
    writes2 = set(trace2['writes'])

    write_conflicts = writes1.intersection(writes2)
    read_write_conflicts = writes1.intersection(reads2)
    write_reads_conflicts = writes2.intersection(reads1)

    return {
        "is_dependent": bool(write_conflicts or read_write_conflicts or write_reads_conflicts),
        "write_conflicts": list(write_conflicts),
        "read_write_conflicts": list(read_write_conflicts),
        "write_read_conflicts": list(write_reads_conflicts),
        "tx1_info": {
            "sender": trace1['sender'],
            "recipient": trace1['recipient'],
            "is_contract_creation": trace1['is_contract_creation'],
            "contract_address": trace1['contract_address'],
            "contracts_logging": trace1['logs']
        },
        "tx2_info": {
            "sender": trace2['sender'],
            "recipient": trace2['recipient'],
            "is_contract_creation": trace2['is_contract_creation'],
            "contract_address": trace2['contract_address'],
            "contracts_logging": trace2['logs']
        }
    }


def main():
    api_key = ""
    alchemy_url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
    web3 = Web3(Web3.HTTPProvider(alchemy_url))

    print("Fetching blocks from the last 24 hours...")
    blocks = get_blocks_last_24h(web3)

    print(f"\nAnalyzing {len(blocks)} blocks...")
    results = []

    for block in blocks:
        print(f"\nAnalyzing block {block.number}...")
        block_analysis = analyze_block_dependencies(web3, block)
        results.append(block_analysis)

        # Print summary for this block
        print(f"Block {block.number}: {block_analysis['total_dependent_pairs']} dependent pairs out of {block_analysis['total_transactions']} transactions")
        print(f"Dependency ratio: {block_analysis['dependency_ratio']:.2%}")

    # Calculate overall statistics
    total_blocks = len(results)
    total_transactions = sum(r['total_transactions'] for r in results)
    total_dependent_pairs = sum(r['total_dependent_pairs'] for r in results)
    avg_dependency_ratio = sum(r['dependency_ratio'] for r in results) / total_blocks

    summary = {
        "time_period": "24 hours",
        "total_blocks_analyzed": total_blocks,
        "total_transactions": total_transactions,
        "total_dependent_pairs": total_dependent_pairs,
        "average_dependency_ratio": avg_dependency_ratio,
        "block_details": results
    }

    # Save results to file
    with open('dependency_analysis.json', 'w') as f:
        json.dump(summary, f, indent=2)

    print("\nAnalysis Summary:")
    print(f"Total blocks analyzed: {total_blocks}")
    print(f"Total transactions: {total_transactions}")
    print(f"Total dependent pairs: {total_dependent_pairs}")
    print(f"Average dependency ratio: {avg_dependency_ratio:.2%}")
    print("\nDetailed results saved to dependency_analysis.json")


if __name__ == "__main__":
    main()
