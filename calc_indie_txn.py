from web3 import Web3
import requests
import json
from pprint import pprint

api_key = "YOUR_API_KEY"
alchemy_url = f"https://eth-mainnet.g.alchemy.com/v2/{api_key}"
block_time = 12


def total_blocks_for_time_period(hours, mins, seconds):
    return int(hours * mins * seconds / block_time)

total_blocks_last_24_hours = total_blocks_for_time_period(24, 60, 60)

# Connect to the Ethereum network
w3 = Web3(Web3.HTTPProvider(alchemy_url))
if not w3.is_connected():
    print("Failed to connect to Ethereum network")
    exit()

def get_block_txns(block_number):
    block_txns = []
    block = w3.eth.get_block(block_number)
    for txn in block["transactions"]:
        block_txns.append(txn.hex())
    return block_txns

def is_block_producer(txn_hash, address):
    block_number = w3.eth.get_transaction(txn_hash)['blockNumber']
    producer = w3.eth.get_block(block_number)['miner']
    if producer.lower() == address.lower():
        return True
    return False


def is_address_contract(address):
    try:
        checksum_address = Web3.to_checksum_address(address)
        if w3.eth.get_code(checksum_address):
            return True
        return False
    except ValueError:
        return False


def is_txn_value_transfer(txn_hash):
    if is_address_contract(w3.eth.get_transaction(txn_hash)['from']) or is_address_contract(w3.eth.get_transaction(txn_hash)['to']):
        return False
    return True


def get_txn_modified_addresses_and_state_vars(txn_hash):
    result = []
    txn_from = w3.eth.get_transaction(txn_hash)['from']
    # Could be None for contract creation
    txn_to = w3.eth.get_transaction(txn_hash)['to']

    # Case 1: Value transfer between EOAs
    if is_txn_value_transfer(txn_hash):
        result.append({"address": txn_from, "state_vars": []})
        if txn_to:
            result.append({"address": txn_to, "state_vars": []})
        return result

    # Case 2: Contract interaction
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": "debug_traceTransaction",
        "params": [
            txn_hash,
            {
                "tracer": "prestateTracer"
            }
        ]
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }
    response = requests.post(alchemy_url, json=payload, headers=headers).json()
    addresses = list(response['result'].items())

    for address, details in addresses:
        # Skip block producer
        if is_block_producer(txn_hash, address):
            continue

        # Check for modified storage slots
        state_vars = []
        if is_address_contract(address):
            state_vars.extend(list(details.get("storage").keys()))
            result.append({"address": address, "state_vars": state_vars})

        # Ensure txn_from is included
        if not any(entry["address"].lower() == txn_from.lower() for entry in result):
            result.append({"address": txn_from, "state_vars": []})

    print(result)
    return result

# def are_txns_dependent(txn1, txn2): TODO
