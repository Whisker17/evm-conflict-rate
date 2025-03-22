# config.py
import os
from dotenv import load_dotenv

load_dotenv()  

# 重试配置
MAX_RETRIES = 10
RETRY_DELAY = 2  # 基础重试延迟（秒）

chains = [
    {
        "name": "Mantle",
        "alchemy_api_key": os.getenv("ALCHEMY_API_KEY"),  
        "alchemy_url": "https://mantle-mainnet.g.alchemy.com/v2/{}",
        "block_time": 2,
    },
    {
        "name": "Ethereum",
        "alchemy_api_key": os.getenv("ALCHEMY_API_KEY"),  
        "alchemy_url": "https://eth-mainnet.g.alchemy.com/v2/{}",
        "block_time": 12,
    },
    # {
    #     "name": "Base",
    #     "alchemy_api_key": os.getenv("ALCHEMY_API_KEY"),  
    #     "alchemy_url": "https://base-mainnet.g.alchemy.com/v2/{}",
    #     "block_time": 2,
    # },
    {
        "name": "Optimism",
        "alchemy_api_key": os.getenv("ALCHEMY_API_KEY"),  
        "alchemy_url": "https://opt-mainnet.g.alchemy.com/v2/{}",
        "block_time": 2,
    },
]