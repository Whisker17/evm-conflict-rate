# main.py
from web3 import Web3
import asyncio
import dotenv
import os
import time
from multiprocessing import cpu_count
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import math
from worker import analyze_block
from typing import List

dotenv.load_dotenv()
alchemy_api_key = os.getenv("ALCHEMY_API_KEY")
alchemy_url = f"https://eth-mainnet.g.alchemy.com/v2/{alchemy_api_key}"

async def get_24h_blocks(w3: Web3) -> List[int]:
    current_block = w3.eth.block_number
    blocks_per_day = math.ceil(24 * 60 * 60 / 12)
    return list(range(current_block - blocks_per_day + 1, current_block + 1))

async def analyze_24h_transactions(w3: Web3):
    blocks = await get_24h_blocks(w3)
    total_blocks = len(blocks)
    print(f"Analyzing {total_blocks} blocks from the last 24 hours...")
    
    num_processes = max(1, cpu_count() - 1)
    print(f"Using {num_processes} processes for parallel block analysis")
    
    all_dependent_txs = []
    total_txs = 0
    all_conflicts = []
    
    worker_func = partial(analyze_block, alchemy_url=alchemy_url)
    
    with ProcessPoolExecutor(max_workers=num_processes) as executor:
        future_to_block = {
            executor.submit(worker_func, block): block 
            for block in blocks
        }
        
        completed = 0
        for future in asyncio.as_completed([asyncio.wrap_future(f) for f in future_to_block]):
            dependent_txs, block_total_txs, conflicts = await future
            all_dependent_txs.extend(dependent_txs)
            total_txs += block_total_txs
            all_conflicts.extend(conflicts)
            
            completed += 1
            progress = (completed / total_blocks) * 100
            print(f"Progress: {progress:.1f}% ({completed}/{total_blocks} blocks)")
            print(f"Current stats - Dependent: {len(all_dependent_txs)}, Total: {total_txs}")
    
    unique_dependent_txs = set(all_dependent_txs)
    dependency_ratio = len(unique_dependent_txs) / total_txs if total_txs > 0 else 0
    return len(unique_dependent_txs), total_txs, dependency_ratio

async def main():
    w3 = Web3(Web3.HTTPProvider(alchemy_url))
    
    start_time = time.time()
    dependent_count, total_count, ratio = await analyze_24h_transactions(w3)
    end_time = time.time()
    
    print("\nAnalysis Complete!")
    print(f"Time taken: {end_time - start_time:.2f} seconds")
    print(f"Total transactions analyzed: {total_count}")
    print(f"Dependent transactions found: {dependent_count}")
    print(f"Dependency ratio: {ratio:.2%}")

if __name__ == "__main__":
    asyncio.run(main())