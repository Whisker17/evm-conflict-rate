from web3 import Web3
from web3.types import HexBytes
import json
from datetime import datetime, timedelta
import time
from itertools import combinations
import asyncio
import aiohttp
from multiprocessing import Pool, Manager, Queue
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from typing import List, Dict, Set, Any
from collections import defaultdict


class AsyncWeb3Client:
    def __init__(self, api_url: str):
        self.api_url = api_url
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers={
            "Content-Type": "application/json",
        })
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def make_request(self, method: str, params: List) -> Any:
        """Make a single RPC request"""
        data = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }

        async with self.session.post(self.api_url, json=data) as response:
            result = await response.json()
            if 'error' in result:
                print(f"RPC Error for {method}: {result['error']}")
                return None
            return result.get('result')


class BlockFetcher:
    def __init__(self, web3_client: AsyncWeb3Client, web3: Web3, target_time: int):
        self.web3_client = web3_client
        self.web3 = web3
        self.target_time = target_time
        self.batch_size = 50

    async def get_latest_block(self) -> int:
        result = await self.web3_client.make_request("eth_blockNumber", [])
        return int(result, 16)

    async def fetch_blocks(self, start_block: int) -> List[Dict]:
        blocks = []
        current_block = start_block

        while True:
            block_tasks = []
            for _ in range(self.batch_size):
                task = self.web3_client.make_request(
                    "eth_getBlockByNumber",
                    [hex(current_block), True]
                )
                block_tasks.append(task)
                current_block -= 1

            # Get all blocks in parallel
            results = await asyncio.gather(*block_tasks)

            for block in results:
                if block is None or int(block['timestamp'], 16) < self.target_time:
                    return blocks
                block_number = int(block['number'], 16)
                web3_block = self.web3.eth.get_block(block_number, full_transactions=True)
                blocks.append(web3_block)

            if len(block_tasks) < self.batch_size:
                break

        return blocks


class TransactionAnalyzer:
    def __init__(self, web3_client: AsyncWeb3Client, web3: Web3):
        self.web3_client = web3_client
        self.web3 = web3

    async def get_transaction_data(self, tx_hash: str) -> Dict:
        # Ensure proper hex string format
        if isinstance(tx_hash, HexBytes):
            tx_hash = tx_hash.hex()
        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash

        # Make all requests in parallel
        trace_task = self.web3_client.make_request(
            "debug_traceTransaction",
            [
                tx_hash,
                {
                    "tracer": "prestateTracer",
                    "tracerConfig": {
                        "diffMode": False,
                        "includeStorage": True,
                        "includeStack": True,
                        "includeLogs": True
                    }
                }
            ]
        )

        tx_task = self.web3_client.make_request(
            "eth_getTransactionByHash", 
            [tx_hash]
        )

        receipt_task = self.web3_client.make_request(
            "eth_getTransactionReceipt", 
            [tx_hash]
        )

        # Wait for all requests to complete
        trace, tx, receipt = await asyncio.gather(trace_task, tx_task, receipt_task)

        if trace is None or tx is None or receipt is None:
            print(f"Failed to get complete data for transaction {tx_hash}")
            return None

        return {
            'trace': trace,
            'transaction': tx,
            'receipt': receipt
        }

    async def analyze_block_dependencies(self, block) -> Dict:
        tx_hashes = []
        for tx in block.transactions:
            if isinstance(tx, dict):
                tx_hash = tx['hash']
            else:
                tx_hash = tx.hash

            if isinstance(tx_hash, HexBytes):
                tx_hash = tx_hash.hex()
            if not tx_hash.startswith('0x'):
                tx_hash = '0x' + tx_hash

            tx_hashes.append(tx_hash)

        total_txs = len(tx_hashes)
        dependent_pairs = []

        # Process all transactions in parallel
        tx_data_tasks = [self.get_transaction_data(tx_hash) for tx_hash in tx_hashes]
        tx_data_results = await asyncio.gather(*tx_data_tasks)

        valid_tx_data = {}
        for tx_hash, tx_data in zip(tx_hashes, tx_data_results):
            if tx_data is not None:
                try:
                    trace = tx_data['trace']
                    tx = tx_data['transaction']
                    tx_receipt = tx_data['receipt']

                    reads = set()
                    writes = set()

                    # Track prestate reads
                    for address, state in trace.items():
                        address = address.lower()
                        if 'balance' in state:
                            reads.add((address, 'balance'))
                        if 'nonce' in state:
                            reads.add((address, 'nonce'))
                        if 'storage' in state:
                            for slot in state['storage'].keys():
                                reads.add((address, f"storage:{slot}"))
                        if 'code' in state:
                            reads.add((address, 'code'))

                    # Track writes
                    sender = tx['from'].lower()
                    writes.add((sender, 'nonce'))
                    writes.add((sender, 'balance'))

                    if tx['to']:
                        recipient = tx['to'].lower()
                        writes.add((recipient, 'balance'))

                    # Track contract creations
                    if tx_receipt['contractAddress']:
                        contract_addr = tx_receipt['contractAddress'].lower()
                        writes.add((contract_addr, 'code'))
                        writes.add((contract_addr, 'nonce'))
                        writes.add((contract_addr, 'balance'))

                    # Track storage writes from logs
                    for log in tx_receipt['logs']:
                        contract_addr = log['address'].lower()
                        for topic in log['topics']:
                            if isinstance(topic, bytes):
                                topic = topic.hex()
                            writes.add((contract_addr, f"storage:{topic}"))

                    valid_tx_data[tx_hash] = {
                        'reads': reads,
                        'writes': writes
                    }
                except Exception as e:
                    print(f"Error processing transaction {tx_hash}: {str(e)}")
                    continue

        valid_tx_hashes = list(valid_tx_data.keys())
        for tx1_hash, tx2_hash in combinations(valid_tx_hashes, 2):
            trace1 = valid_tx_data[tx1_hash]
            trace2 = valid_tx_data[tx2_hash]

            write_conflicts = trace1['writes'].intersection(trace2['writes'])
            read_write_conflicts = trace1['reads'].intersection(trace2['writes'])
            write_read_conflicts = trace1['writes'].intersection(trace2['reads'])

            if write_conflicts or read_write_conflicts or write_read_conflicts:
                dependent_pairs.append({
                    "tx1": tx1_hash,
                    "tx2": tx2_hash,
                    "conflicts": {
                        "write_conflicts": list(write_conflicts),
                        "read_write_conflicts": list(read_write_conflicts),
                        "write_read_conflicts": list(write_read_conflicts)
                    }
                })

        return {
            "block_number": block.number,
            "timestamp": block.timestamp,
            "total_transactions": total_txs,
            "successful_transactions": len(valid_tx_data),
            "failed_transactions": total_txs - len(valid_tx_data),
            "dependent_pairs": dependent_pairs,
            "total_dependent_pairs": len(dependent_pairs),
            "dependency_ratio": len(dependent_pairs) / (len(valid_tx_data) * (len(valid_tx_data) - 1) / 2) if len(valid_tx_data) > 1 else 0
        }


async def main():
    api_key = "d9knunrAA3rrtjnNBkWloA0Z-tprIY9R"
    alchemy_url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
    web3 = Web3(Web3.HTTPProvider(alchemy_url))

    target_time = int(time.time()) - (24 * 60 * 60)

    async with AsyncWeb3Client(alchemy_url) as web3_client:
        block_fetcher = BlockFetcher(web3_client, web3, target_time)
        tx_analyzer = TransactionAnalyzer(web3_client, web3)

        latest_block = await block_fetcher.get_latest_block()
        print("Fetching blocks...")
        blocks = await block_fetcher.fetch_blocks(latest_block)

        print(f"\nAnalyzing {len(blocks)} blocks...")
        analysis_tasks = [tx_analyzer.analyze_block_dependencies(block) for block in blocks]
        results = await asyncio.gather(*analysis_tasks)

        # Calculate total possible pairs across all blocks
        total_possible_pairs = sum(
            r['total_transactions'] * (r['total_transactions'] - 1) // 2 
            for r in results
        )
        total_dependent_pairs = sum(r['total_dependent_pairs'] for r in results)

        summary = {
            "time_period": "24 hours",
            "total_blocks_analyzed": len(results),
            "total_transactions": sum(r['total_transactions'] for r in results),
            "total_possible_pairs": total_possible_pairs,
            "total_dependent_pairs": total_dependent_pairs,
            "average_dependency_ratio": total_dependent_pairs / total_possible_pairs if total_possible_pairs > 0 else 0,
            "block_details": results
        }

        with open('dependency_analysis.json', 'w') as f:
            json.dump(summary, f, indent=2)

        print("\nAnalysis Summary:")
        print(f"Total blocks analyzed: {summary['total_blocks_analyzed']}")
        print(f"Total transactions: {summary['total_transactions']}")
        print(f"Total possible transaction pairs: {summary['total_possible_pairs']}")
        print(f"Total dependent pairs: {summary['total_dependent_pairs']}")
        print(f"Dependency ratio: {summary['total_dependent_pairs']} / {summary['total_possible_pairs']} = {summary['average_dependency_ratio']:.2%}")
        print("\nDetailed results saved to dependency_analysis.json")


if __name__ == "__main__":
    asyncio.run(main())