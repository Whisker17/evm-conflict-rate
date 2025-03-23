# exponential_backoff.py
import time
import random
import logging
from typing import Callable, Any, TypeVar, Optional
from functools import wraps

T = TypeVar('T')

class ExponentialBackoff:
    def __init__(
        self, 
        max_retries: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 64.0,
        factor: float = 2.0,
        jitter: bool = True
    ):
        """初始化指数退避实例
        
        Args:
            max_retries: 最大重试次数
            initial_backoff: 初始等待时间(秒)
            max_backoff: 最大等待时间(秒)
            factor: 指数增长因子
            jitter: 是否添加随机抖动
        """
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.factor = factor
        self.jitter = jitter
        self.logger = logging.getLogger("ExponentialBackoff")
    
    def _get_backoff_time(self, retry_count: int) -> float:
        """计算下一次重试的等待时间"""
        backoff = min(self.initial_backoff * (self.factor ** retry_count), self.max_backoff)
        
        if self.jitter:
            # 添加最多1秒的随机抖动
            jitter_time = random.random()
            backoff += jitter_time
            
        return backoff
    
    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """执行函数，如果抛出指定异常则重试
        
        Args:
            func: 要执行的函数
            args, kwargs: 传递给函数的参数
            
        Returns:
            函数的返回值
            
        Raises:
            最后一次尝试的异常
        """
        last_exception = None
        
        for retry in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # 检查是否为429错误
                error_message = str(e).lower()
                is_rate_limit_error = (
                    "429" in error_message or 
                    "too many requests" in error_message or 
                    "rate limit" in error_message
                )
                
                if not is_rate_limit_error or retry >= self.max_retries:
                    # 如果不是速率限制错误，或者已达到最大重试次数，则重新抛出异常
                    raise
                
                # 计算等待时间
                backoff_time = self._get_backoff_time(retry)
                
                # self.logger.warning(
                #     f"Rate limit error ({e}). Retrying in {backoff_time:.2f}s "
                #     f"(attempt {retry + 1}/{self.max_retries + 1})..."
                # )
                
                # 等待指定时间后重试
                time.sleep(backoff_time)
        
        # 如果所有重试都失败了(不应该到达这里)
        if last_exception:
            raise last_exception
        
        # 默认返回(也不应该到达这里)
        return None
    
    def decorator(self, func: Callable[..., T]) -> Callable[..., T]:
        """装饰器版本，可以直接装饰函数"""
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.execute(func, *args, **kwargs)
        return wrapper


# 创建一个简便函数用于重试
def with_retry(
    max_retries: int = 5,
    initial_backoff: float = 1.0,
    max_backoff: float = 64.0,
    factor: float = 2.0,
    jitter: bool = True
):
    """装饰器函数，使用指数退避重试机制
    
    用法:
        @with_retry(max_retries=5)
        def my_function():
            # 可能会抛出异常的代码
    """
    backoff = ExponentialBackoff(
        max_retries=max_retries,
        initial_backoff=initial_backoff,
        max_backoff=max_backoff,
        factor=factor,
        jitter=jitter
    )
    return backoff.decorator