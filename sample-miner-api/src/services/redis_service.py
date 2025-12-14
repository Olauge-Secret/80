"""Redis service for sharing solutions between miner instances."""

import json
import logging
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

logger = logging.getLogger(__name__)


class RedisService:
    """Service for managing shared solutions in Redis."""
    
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        """
        Initialize Redis service.
        
        Args:
            host: Redis host address
            port: Redis port
            db: Redis database number
        """
        if not REDIS_AVAILABLE:
            logger.warning("Redis library not installed. Redis functionality disabled.")
            self.client = None
            return
        
        self.host = host
        self.port = port
        self.db = db
        self.client: Optional[redis.Redis] = None
        logger.info(f"Redis service configured: {host}:{port}/{db}")
    
    async def connect(self):
        """Establish connection to Redis."""
        if not REDIS_AVAILABLE:
            return False
        
        try:
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30
            )
            # Test connection
            await self.client.ping()
            logger.info("✅ Redis connection established successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            self.client = None
            return False
    
    async def disconnect(self):
        """Close Redis connection."""
        if self.client:
            try:
                await self.client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
    
    def _get_solution_key(self, task_hash: str) -> str:
        """Generate Redis key for a task solution."""
        return f"solution:{task_hash}"
    
    async def store_solution(
        self,
        task_hash: str,
        solution: Dict[str, Any],
        ttl: int = 120
    ) -> bool:
        """
        Store a solution in Redis.
        
        Args:
            task_hash: Unique identifier for the task
            solution: Solution data to store
            ttl: Time to live in seconds (default: 120s = 2 minutes)
            
        Returns:
            True if stored successfully, False otherwise
        """
        if not self.client:
            logger.warning("Redis not available, cannot store solution")
            return False
        
        try:
            key = self._get_solution_key(task_hash)
            solution_data = {
                **solution,
                "stored_at": datetime.utcnow().isoformat(),
                "task_hash": task_hash
            }
            
            # Store as JSON string with expiration
            await self.client.setex(
                key,
                ttl,
                json.dumps(solution_data)
            )
            
            logger.info(f"✅ Stored solution for task {task_hash[:8]}... (TTL: {ttl}s)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to store solution: {e}")
            return False
    
    async def get_solution(self, task_hash: str) -> Optional[Dict[str, Any]]:
        """
        Get a solution from Redis.
        
        Args:
            task_hash: Unique identifier for the task
            
        Returns:
            Solution data if found, None otherwise
        """
        if not self.client:
            logger.warning("Redis not available, cannot get solution")
            return None
        
        try:
            key = self._get_solution_key(task_hash)
            data = await self.client.get(key)
            
            if data:
                solution = json.loads(data)
                logger.info(f"✅ Retrieved solution for task {task_hash[:8]}...")
                return solution
            else:
                logger.debug(f"No solution found for task {task_hash[:8]}...")
                return None
                
        except Exception as e:
            logger.error(f"❌ Failed to get solution: {e}")
            return None
    
    async def wait_for_solution(
        self,
        task_hash: str,
        timeout: int = 55,
        poll_interval: float = 0.5
    ) -> Optional[Dict[str, Any]]:
        """
        Wait for a solution to appear in Redis.
        
        Args:
            task_hash: Unique identifier for the task
            timeout: Maximum time to wait in seconds (default: 55s)
            poll_interval: Time between checks in seconds (default: 0.5s)
            
        Returns:
            Solution data if found within timeout, None otherwise
        """
        if not self.client:
            logger.warning("Redis not available, cannot wait for solution")
            return None
        
        logger.info(f"⏳ Waiting for solution: {task_hash[:8]}... (timeout: {timeout}s)")
        start_time = asyncio.get_event_loop().time()
        
        while True:
            # Check if solution exists
            solution = await self.get_solution(task_hash)
            if solution:
                elapsed = asyncio.get_event_loop().time() - start_time
                logger.info(f"✅ Solution received after {elapsed:.2f}s")
                return solution
            
            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                logger.warning(f"⏰ Timeout waiting for solution: {task_hash[:8]}... ({elapsed:.2f}s)")
                return None
            
            # Wait before next check
            await asyncio.sleep(poll_interval)
    
    async def delete_solution(self, task_hash: str) -> bool:
        """
        Delete a solution from Redis.
        
        Args:
            task_hash: Unique identifier for the task
            
        Returns:
            True if deleted successfully, False otherwise
        """
        if not self.client:
            return False
        
        try:
            key = self._get_solution_key(task_hash)
            await self.client.delete(key)
            logger.info(f"🗑️ Deleted solution for task {task_hash[:8]}...")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to delete solution: {e}")
            return False
    
    async def health_check(self) -> bool:
        """Check if Redis is healthy."""
        if not self.client:
            return False
        
        try:
            await self.client.ping()
            return True
        except Exception:
            return False


# Global Redis service instance
_redis_service: Optional[RedisService] = None


def get_redis_service() -> Optional[RedisService]:
    """Get or create the global Redis service instance."""
    global _redis_service
    
    if _redis_service is None:
        from src.core.config import settings
        
        # Initialize Redis service
        _redis_service = RedisService(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db
        )
    
    return _redis_service


async def initialize_redis():
    """Initialize and connect to Redis."""
    service = get_redis_service()
    if service:
        await service.connect()


async def close_redis():
    """Close Redis connection."""
    service = get_redis_service()
    if service:
        await service.disconnect()

