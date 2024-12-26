# Ethereum Transaction Conflict Analysis

This project analyzes Ethereum blockchain transactions to determine the conflict rate between transactions within blocks over the last 24 hours. The conflict rate helps us understand what percentage of transactions could potentially be parallelized.

## Overview

The analysis identifies four types of conflicts:
1. Multiple ETH transfers from the same source address
2. Multiple ERC20 token transfers affecting the same address
3. Multiple value transfers to the same EOA (Externally Owned Account)
4. Multiple calls to the same function on the same contract

## Requirements

- Python 3.7+
- Web3.py
- An Alchemy API key

## Setup

1. Create a `.env` file in the project root with your Alchemy API key:
```
ALCHEMY_API_KEY=your_api_key_here
```

2. Install dependencies:
```bash
pip install web3 python-dotenv
```

## Usage

Run the analysis:
```bash
python main.py
```

The script will:
- Analyze all blocks from the last 24 hours
- Process transactions in parallel using multiple CPU cores
- Output progress updates and final statistics including:
  - Total transactions analyzed
  - Number of dependent transactions
  - Overall dependency ratio

## Note

The analysis assumes a block time of ~12 seconds to calculate the number of blocks in 24 hours. This is approximate and may vary based on network conditions.