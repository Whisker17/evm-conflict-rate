# worker.py
from dataclasses import dataclass
from typing import Dict, Set, List, Optional, Tuple
from web3 import Web3
import time
from itertools import combinations
import config

@dataclass
class Modification:
    type: str
    from_address: str
    to_address: str
    input_data: str
    value: str
    token_from: Optional[str] = None
    token_to: Optional[str] = None
    function_selector: Optional[str] = None

@dataclass
class Conflict:
    contract_address: str
    type: str
    details: str

class RateLimiter:
    def __init__(self, calls_per_second=5):
        self.calls_per_second = calls_per_second
        self.last_call_time = time.time()
        
    def acquire(self):
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        
        if time_since_last_call < (1.0 / self.calls_per_second):
            sleep_time = (1.0 / self.calls_per_second) - time_since_last_call
            time.sleep(sleep_time)
        
        self.last_call_time = time.time()

def get_function_selector(input_data: str) -> Optional[str]:
    return input_data[:10] if len(input_data) >= 10 else None

def decode_erc20_transfer(input_data: str) -> Tuple[Optional[str], Optional[str]]:
    if len(input_data) < 138:
        return None, None
    
    selector = input_data[:10]
    if selector != '0xa9059cbb':
        return None, None
    
    to_address = '0x' + input_data[34:74]
    return None, to_address.lower()

def is_contract(result: dict, address: str) -> bool:
    return (result.get('input', '0x') != '0x' or 
            result.get('calls', []) or 
            address.startswith('0x000000000000000000000000'))

def analyze_trace(trace: dict) -> List[Modification]:
    result = trace.get('result', {})
    modifications = []

    if result.get('to'):
        from_addr = result['from'].lower()
        to_addr = result['to'].lower()
        input_data = result.get('input', '0x')
        value = result.get('value', '0x0')

        token_from, token_to = decode_erc20_transfer(input_data)
        
        if token_from is not None or token_to is not None:
            mod_type = 'erc20-transfer'
        elif not is_contract(result, to_addr) and value != '0x0':
            mod_type = 'eoa-transfer'
        else:
            mod_type = 'contract-call' if input_data != '0x' else 'eth-transfer'

        modifications.append(Modification(
            type=mod_type,
            from_address=from_addr,
            to_address=to_addr,
            input_data=input_data,
            value=value,
            token_from=token_from or from_addr if mod_type == 'erc20-transfer' else None,
            token_to=token_to if mod_type == 'erc20-transfer' else None,
            function_selector=get_function_selector(input_data)
        ))

    calls = result.get('calls', [])
    for call in calls:
        call_type = call.get('type', '').lower()
        from_addr = call['from'].lower()
        to_addr = call['to'].lower()
        input_data = call.get('input', '0x')
        value = call.get('value', '0x0')

        token_from, token_to = decode_erc20_transfer(input_data)
        
        if token_from is not None or token_to is not None:
            call_type = 'erc20-transfer'
        elif not is_contract(call, to_addr) and value != '0x0':
            call_type = 'eoa-transfer'

        modifications.append(Modification(
            type=call_type,
            from_address=from_addr,
            to_address=to_addr,
            input_data=input_data,
            value=value,
            token_from=token_from or from_addr if call_type == 'erc20-transfer' else None,
            token_to=token_to if call_type == 'erc20-transfer' else None,
            function_selector=get_function_selector(input_data)
        ))

    return modifications

def check_modifications_conflict(mods1: List[Modification], mods2: List[Modification]) -> Tuple[bool, List[Conflict]]:
    conflicts = []
    is_dependent = False

    sources1 = {mod.from_address for mod in mods1 if mod.value != '0x0'}
    sources2 = {mod.from_address for mod in mods2 if mod.value != '0x0'}
    
    common_sources = sources1 & sources2
    for source in common_sources:
        conflicts.append(Conflict(
            contract_address=source,
            type="same-source",
            details=f"Multiple ETH transfers from same source address {source}"
        ))
        is_dependent = True

    for mod1 in mods1:
        if mod1.type == 'erc20-transfer':
            for mod2 in mods2:
                if mod2.type == 'erc20-transfer' and mod1.to_address == mod2.to_address:
                    if (mod1.token_from == mod2.token_from or 
                        mod1.token_to == mod2.token_to):
                        conflicts.append(Conflict(
                            contract_address=mod1.to_address,
                            type="erc20-balance-conflict",
                            details=f"ERC20 transfers affecting same address: {mod1.token_from if mod1.token_from == mod2.token_from else mod1.token_to}"
                        ))
                        is_dependent = True
                        break

    for mod1 in mods1:
        if mod1.type == 'eoa-transfer':
            for mod2 in mods2:
                if mod2.type == 'eoa-transfer':
                    if mod1.to_address == mod2.to_address:
                        conflicts.append(Conflict(
                            contract_address=mod1.to_address,
                            type="eoa-transfer-conflict",
                            details=f"Multiple transfers to same EOA"
                        ))
                        is_dependent = True
                        break

    for mod1 in mods1:
        if mod1.type == 'contract-call':
            for mod2 in mods2:
                if mod2.type == 'contract-call' and mod1.to_address == mod2.to_address:
                    if mod1.function_selector == mod2.function_selector:
                        conflicts.append(Conflict(
                            contract_address=mod1.to_address,
                            type="contract-call-conflict",
                            details=f"Multiple calls to same function {mod1.function_selector}"
                        ))
                        is_dependent = True
                        break

    return is_dependent, conflicts

def trace_transaction(w3: Web3, tx_hash: str, rate_limiter: RateLimiter) -> dict:
    retries = 0
    last_error = None
    
    while retries <= config.MAX_RETRIES:
        rate_limiter.acquire()
        
        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash
        
        try:
            response = w3.provider.make_request(
                "debug_traceTransaction", 
                [tx_hash, {"tracer": "callTracer"}]
            )
            
            # 检查是否有错误信息表明这是一个 429 错误
            if 'error' in response and isinstance(response['error'], dict):
                error_info = response['error']
                error_code = error_info.get('code')
                error_msg = str(error_info).lower()
                
                if error_code == 429 or 'rate limit' in error_msg or 'too many requests' in error_msg:
                    if retries < config.MAX_RETRIES:
                        retries += 1
                        # 指数退避策略
                        sleep_time = config.RETRY_DELAY * (2 ** (retries - 1))
                        print(f"Rate limit hit for {tx_hash}, retrying in {sleep_time}s (retry {retries}/{config.MAX_RETRIES})")
                        time.sleep(sleep_time)
                        last_error = f"Rate limit error: {error_info}"
                        continue
                    else:
                        # 最大重试次数已用尽
                        raise Exception(f"Rate limit exceeded after {config.MAX_RETRIES} retries for tx {tx_hash}: {error_info}")
                else:
                    # 其他API错误
                    raise Exception(f"API error for tx {tx_hash}: {error_info}")
            
            # 确保有结果字段，否则可能是其他类型的错误
            if 'result' not in response:
                if retries < config.MAX_RETRIES:
                    retries += 1
                    sleep_time = config.RETRY_DELAY * (2 ** (retries - 1))
                    print(f"Invalid response for {tx_hash}, retrying in {sleep_time}s (retry {retries}/{config.MAX_RETRIES})")
                    time.sleep(sleep_time)
                    last_error = f"Invalid response: {response}"
                    continue
                else:
                    raise Exception(f"Invalid response after {config.MAX_RETRIES} retries for tx {tx_hash}: {response}")
            
            return response
            
        except Exception as e:
            last_error = str(e)
            if retries < config.MAX_RETRIES and ('429' in last_error or 'rate limit' in last_error.lower() or 'too many' in last_error.lower()):
                retries += 1
                sleep_time = config.RETRY_DELAY * (2 ** (retries - 1))
                print(f"Rate limit exception for {tx_hash}, retrying in {sleep_time}s (retry {retries}/{config.MAX_RETRIES})")
                time.sleep(sleep_time)
            else:
                raise Exception(f"Error tracing tx {tx_hash} after {retries} retries: {last_error}")
    
    # 如果所有重试都失败
    raise Exception(f"Max retries ({config.MAX_RETRIES}) exceeded for transaction {tx_hash}. Last error: {last_error}")

def analyze_block(block_number: int, alchemy_url: str) -> Tuple[List[str], int, List[Conflict], bool]:
    try:
        w3 = Web3(Web3.HTTPProvider(alchemy_url))
        rate_limiter = RateLimiter(calls_per_second=5)
        
        print(f"Analyzing block {block_number}...")
        
        block = w3.eth.get_block(block_number, full_transactions=True)
        txs = [tx['hash'].hex() for tx in block['transactions']]
        
        if not txs:
            return [], 0, [], False
            
        traces = {}
        failed_txs = []  # 记录失败的交易哈希
        
        for tx_hash in txs:
            try:
                trace = trace_transaction(w3, tx_hash, rate_limiter)
                traces[tx_hash] = trace
            except Exception as e:
                failed_txs.append(tx_hash)
                print(f"Error fetching trace for {tx_hash} in block {block_number}: {str(e)}")
                # 如果任何交易无法解析，立即标记整个区块失败
                return [], len(txs), [], True
        
        tx_modifications = {}
        for tx_hash, trace in traces.items():
            tx_modifications[tx_hash] = analyze_trace(trace)
        
        dependent_txs = set()
        all_conflicts = []
        
        for tx1, tx2 in combinations(tx_modifications.keys(), 2):
            dependent, conflicts = check_modifications_conflict(
                tx_modifications[tx1],
                tx_modifications[tx2]
            )
            if dependent:
                dependent_txs.add(tx1)
                dependent_txs.add(tx2)
                all_conflicts.extend(conflicts)
        
        return list(dependent_txs), len(txs), all_conflicts, False
    
    except Exception as e:
        print(f"Error processing block {block_number}: {str(e)}")
        return [], 0, [], True