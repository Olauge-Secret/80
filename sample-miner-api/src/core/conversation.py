"""Conversation context management using SQLite database.

This module uses SQLite for persistent storage (file-based, zero configuration).
Database file location: ./data/miner_api.db (configured in .env)

All database operations are run in a thread pool to enable concurrent request handling.
"""

import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
from src.repositories.conversation_repository import ConversationRepository

logger = logging.getLogger(__name__)


class ConversationContext:
    """
    Manages conversation context for a single conversation ID.
    Now backed by SQLite database via SQLModel ORM.
    
    Stores up to 10 recent messages and automatically cleans up old messages.
    Messages older than 7 days are automatically deleted.
    """
    
    MAX_MESSAGES = 10  # Store up to 10 recent messages
    MAX_MESSAGE_AGE_DAYS = 7  # Auto-delete messages older than 7 days
    
    def __init__(self, cid: str):
        self.cid = cid
        self.repository = ConversationRepository()
        # Note: Conversation creation is deferred to first async operation
        # This prevents blocking during initialization
    
    async def _ensure_conversation_exists(self):
        """Ensure conversation exists in database (async wrapper)."""
        await asyncio.to_thread(self.repository.get_or_create_conversation, self.cid)
    
    async def add_message(self, role: str, content: str, extra_data: Optional[dict] = None):
        """
        Add a message to conversation history. Stores up to 10 recent messages.
        Automatically cleans up messages older than 7 days.
        """
        # Skip if content is None or empty
        if not content or not content.strip():
            logger.warning(f"Skipping empty message for conversation {self.cid}")
            return
        
        # Ensure conversation exists first
        await self._ensure_conversation_exists()
        
        # Add message to database (run in thread pool to avoid blocking)
        await asyncio.to_thread(
            self.repository.add_message,
            self.cid,
            role,
            content,
            extra_data
        )
    
    async def add_user_message(self, content: str, extra_data: Optional[dict] = None):
        """Add a user message to conversation history."""
        await self.add_message("user", content, extra_data)
    
    async def add_assistant_message(self, content: str, extra_data: Optional[dict] = None):
        """Add an assistant message to conversation history."""
        await self.add_message("assistant", content, extra_data)
    
    async def get_messages(self) -> List[Dict]:
        """
        Get conversation history as a list of message dictionaries.
        Returns up to 10 most recent messages, excluding messages older than 7 days.
        """
        # Ensure conversation exists first
        await self._ensure_conversation_exists()
        
        messages = await asyncio.to_thread(
            self.repository.get_recent_messages,
            self.cid,
            self.MAX_MESSAGES
        )
        return messages
    
    async def get_context_summary(self) -> str:
        """
        Get a summary of the conversation context from recent messages.
        """
        messages = await self.get_messages()
        if not messages:
            return "No conversation context yet."
        
        history_text = "Recent conversation:\n"
        for msg in messages[-5:]:  # Show last 5 messages
            role = msg['role'].capitalize()
            content = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
            history_text += f"{role}: {content}\n"
        
        return history_text
    
    async def get_context(self) -> str:
        """
        Get full conversation context as formatted string for LLM.
        Alias for get_context_summary.
        """
        return await self.get_context_summary()
    
    async def get_recent_messages(self, count: int = 5) -> List[Dict]:
        """
        Get the most recent N messages.
        
        Args:
            count: Number of recent messages to return
        
        Returns:
            List of message dictionaries
        """
        return await asyncio.to_thread(
            self.repository.get_recent_messages,
            self.cid,
            count
        )
    
    async def clear(self):
        """Clear conversation messages by deleting the conversation."""
        await asyncio.to_thread(self.repository.delete_conversation, self.cid)
        logger.info(f"Cleared messages for conversation {self.cid}.")
    
    async def get_created_at(self) -> Optional[datetime]:
        """Get conversation creation time."""
        conversation = await asyncio.to_thread(self.repository.get_conversation, self.cid)
        return conversation.created_at if conversation else None
    
    async def get_last_updated(self) -> Optional[datetime]:
        """Get last update time."""
        conversation = await asyncio.to_thread(self.repository.get_conversation, self.cid)
        return conversation.last_updated if conversation else None
    
    # Properties for backward compatibility (now async)
    @property
    def created_at(self) -> Optional[datetime]:
        """Get conversation creation time (synchronous, for backward compatibility)."""
        conversation = self.repository.get_conversation(self.cid)
        return conversation.created_at if conversation else None
    
    @property
    def last_activity(self) -> Optional[datetime]:
        """Get last update time (synchronous, for backward compatibility)."""
        conversation = self.repository.get_conversation(self.cid)
        return conversation.last_updated if conversation else None


class ConversationManager:
    """
    Manages all conversation contexts.
    Now uses SQLite database for persistent storage.
    """
    
    def __init__(self):
        self.repository = ConversationRepository()
    
    def get_or_create(self, cid: str) -> ConversationContext:
        """Get an existing conversation or create a new one."""
        return ConversationContext(cid)
    
    def get(self, cid: str) -> Optional[ConversationContext]:
        """Get an existing conversation context."""
        conversation = self.repository.get_conversation(cid)
        if conversation:
            return ConversationContext(cid)
        return None
    
    async def delete(self, cid: str):
        """Delete a conversation context."""
        await asyncio.to_thread(self.repository.delete_conversation, cid)
    
    def delete_sync(self, cid: str):
        """Delete a conversation context (synchronous version for backward compatibility)."""
        self.repository.delete_conversation(cid)
    
    async def get_stats(self) -> Dict:
        """Get statistics about conversations."""
        conversations = await asyncio.to_thread(
            self.repository.get_all_conversations,
            100
        )
        
        return {
            "total_conversations": len(conversations),
            "max_conversations": 100,  # Database limit for stats display
            "conversations": [
                {
                    "cid": conv.cid,
                    "messages": conv.message_count,
                    "created_at": conv.created_at.isoformat(),
                    "last_updated": conv.last_updated.isoformat()
                }
                for conv in conversations
            ]
        }
    
    def get_stats_sync(self) -> Dict:
        """Get statistics about conversations (synchronous version for backward compatibility)."""
        conversations = self.repository.get_all_conversations(limit=100)
        
        return {
            "total_conversations": len(conversations),
            "max_conversations": 100,  # Database limit for stats display
            "conversations": [
                {
                    "cid": conv.cid,
                    "messages": conv.message_count,
                    "created_at": conv.created_at.isoformat(),
                    "last_updated": conv.last_updated.isoformat()
                }
                for conv in conversations
            ]
        }


# Global conversation manager instance
conversation_manager = ConversationManager()
