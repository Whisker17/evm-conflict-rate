# main.py
from web3 import Web3
import asyncio
import time
from multiprocessing import cpu_count
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import math
from worker import analyze_block, Conflict
from typing import List
from collections import defaultdict
import config
from rich.console import Console
from rich import print

console = Console()

async def get_24h_blocks(w3: Web3, block_time: int) -> List[int]:
    current_block = w3.eth.block_number
    blocks_per_day = math.ceil(1 * 60 * 60 / block_time)  
    # blocks_per_day = math.ceil(60 / block_time)
    return list(range(current_block - blocks_per_day + 1, current_block + 1))

async def analyze_chain(chain_config: dict):
    chain_name = chain_config["name"]
    alchemy_api_key = chain_config["alchemy_api_key"]
    alchemy_url = chain_config["alchemy_url"].format(alchemy_api_key)
    block_time = chain_config["block_time"]  

    console.print(f"\nAnalyzing {chain_name}...")
    console.print(f"Rate limit retry configuration: max_retries={config.MAX_RETRIES}, retry_delay={config.RETRY_DELAY}s")

    w3 = Web3(Web3.HTTPProvider(alchemy_url))

    blocks = await get_24h_blocks(w3, block_time)  
    total_blocks = len(blocks)
    console.print(f"Analyzing {chain_name} {total_blocks} blocks from the last 24 hours...")

    num_processes = max(1, cpu_count() - 1)
    console.print(f"Using {num_processes} processes for parallel block analysis")

    all_dependent_txs = []
    total_txs = 0
    all_conflicts = []
    failed_blocks = 0

    conflict_counts = defaultdict(int)

    worker_func = partial(analyze_block, alchemy_url=alchemy_url)

    start_time = time.time()  

    with ProcessPoolExecutor(max_workers=num_processes) as executor:
        future_to_block = {
            executor.submit(worker_func, block): block
            for block in blocks
        }

        completed = 0
        for future in asyncio.as_completed([asyncio.wrap_future(f) for f in future_to_block]):
            try:
                dependent_txs, block_total_txs, conflicts, is_failed = await future
                if is_failed:
                    failed_blocks += 1
                    block_num = future_to_block[future._future]
                    console.print(f"[red]Failed to analyze block {block_num}[/]")
                else:
                    all_dependent_txs.extend(dependent_txs)
                    total_txs += block_total_txs
                    all_conflicts.extend(conflicts)
            except Exception as e:
                failed_blocks += 1
                block_num = future_to_block[future._future]
                console.print(f"[red]Exception processing block {block_num}: {str(e)}[/]")

            completed += 1
            progress = (completed / total_blocks) * 100
            console.print(f"Progress: {progress:.1f}% ({completed}/{total_blocks} blocks, {failed_blocks} failed)")
            console.print(f"Current stats - Dependent: {len(all_dependent_txs)}, Total: {total_txs}")

            for conflict in conflicts:
                # console.print(f"Conflict Type: {conflict.type}, Details: {conflict.details}")
                conflict_counts[conflict.type] += 1

    unique_dependent_txs = set(all_dependent_txs)
    dependency_ratio = len(unique_dependent_txs) / total_txs if total_txs > 0 else 0
    block_success_rate = (total_blocks - failed_blocks) / total_blocks if total_blocks > 0 else 0

    end_time = time.time()  
    analysis_time = end_time - start_time  

    console.print("\nConflict Type Counts:")
    for conflict_type, count in conflict_counts.items():
        console.print(f"{conflict_type}: {count}")

    console.print(f"[green bold]\n{chain_name} Analysis Complete![/]")
    console.print(f"[white]Time taken: {analysis_time:.2f} seconds[/]")  
    console.print(f"Total blocks analyzed: {total_blocks}")
    console.print(f"Failed blocks: {failed_blocks} ({(failed_blocks/total_blocks)*100:.2f}%)")
    console.print(f"Block analysis success rate: {block_success_rate:.2%}")
    console.print(f"Total transactions analyzed: {total_txs}")
    console.print(f"Dependent transactions found: {len(unique_dependent_txs)}")
    console.print(f"Dependency ratio: {dependency_ratio:.2%}")

    return {
        "chain_name": chain_name,
        "total_blocks": total_blocks,
        "failed_blocks": failed_blocks,
        "block_success_rate": block_success_rate,
        "total_transactions": total_txs,
        "dependent_transactions": len(unique_dependent_txs),
        "dependency_ratio": dependency_ratio,
        "conflict_counts": conflict_counts,
        "analysis_time": analysis_time,  
    }


async def main():
    start_time = time.time()

    chain_results = []
    for chain_config in config.chains:
        result = await analyze_chain(chain_config)
        chain_results.append(result)

    end_time = time.time()

    console.print("[cyan bold]\nAll Chains Analysis Complete![/]")
    console.print(f"[white]Time taken: {end_time - start_time:.2f} seconds[/]")

    for result in chain_results:
        console.print(f"[magenta bold]\n{result['chain_name']}:[/]")
        console.print(f"[white]  Time taken: {result['analysis_time']:.2f} seconds[/]")  
        console.print(f"  Total blocks: {result['total_blocks']}")
        console.print(f"  Failed blocks: {result['failed_blocks']} ({(result['failed_blocks']/result['total_blocks'])*100:.2f}%)")
        console.print(f"  Block success rate: {result['block_success_rate']:.2%}")
        console.print(f"  Total transactions: {result['total_transactions']}")
        console.print(f"  Dependent transactions: {result['dependent_transactions']}")
        console.print(f"  Dependency ratio: {result['dependency_ratio']:.2%}")
        console.print("  Conflict counts:")
        for conflict_type, count in result["conflict_counts"].items():
            console.print(f"    {conflict_type}: {count}")

if __name__ == "__main__":
    asyncio.run(main())