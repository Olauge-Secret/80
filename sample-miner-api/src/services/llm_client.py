"""LLM API client wrapper with support for OpenAI, vLLM, and Chute.

This module provides a unified interface for interacting with Large Language Models
through different providers:
- OpenAI: Cloud-based API (GPT-4o, GPT-4-turbo, etc.)
- vLLM: Self-hosted models (Llama, Qwen, Mistral, etc.)
- Chute: Chute AI API (DeepSeek-R1, etc.)

The client automatically detects the provider from settings and provides
an OpenAI-compatible interface for all.
"""

import logging
import re
import time
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI, OpenAIError
import httpx
from src.core.config import settings

logger = logging.getLogger(__name__)

# Set up inference logging to file
_inference_logger = None
_inference_log_path = None

def _setup_inference_logger():
    """Set up a dedicated logger for inference times that writes to a file."""
    global _inference_logger, _inference_log_path
    
    if _inference_logger is not None:
        return _inference_logger
    
    # Create logs directory if it doesn't exist
    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)
    
    # Create inference log file path
    _inference_log_path = log_dir / "inference.log"
    
    # Create logger
    _inference_logger = logging.getLogger("inference")
    _inference_logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    _inference_logger.handlers.clear()
    
    # Create file handler
    file_handler = logging.FileHandler(_inference_log_path, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter('%(message)s')
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    _inference_logger.addHandler(file_handler)
    
    # Prevent propagation to root logger
    _inference_logger.propagate = False
    
    logger.info(f"Inference logging enabled. Log file: {_inference_log_path.absolute()}")
    
    return _inference_logger

def _log_inference(
    provider: str,
    model: str,
    tokens_used: int,
    inference_time: float,
    method: str = "generate_response",
    finish_reason: Optional[str] = None
):
    """Log inference details to file in JSON format."""
    global _inference_logger
    
    if _inference_logger is None:
        _inference_logger = _setup_inference_logger()
    
    # Create log entry
    log_entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "provider": provider,
        "model": model,
        "method": method,
        "tokens_used": tokens_used,
        "inference_time_seconds": round(inference_time, 3),
        "tokens_per_second": round(tokens_used / inference_time, 2) if inference_time > 0 else 0,
        "finish_reason": finish_reason
    }
    
    # Log as JSON (one line per inference)
    _inference_logger.info(json.dumps(log_entry))


def strip_reasoning_tags(text: str) -> str:
    """
    Remove reasoning/thinking tags from LLM responses.
    
    Removes content between tags like:
    - <think>...</think>
    - <think>...</think>
    - <reasoning>...</reasoning>
    - <thought>...</thought>
    
    Args:
        text: Raw text that may contain reasoning tags
        
    Returns:
        Text with reasoning tags and their content removed
    """
    if not text:
        return text
    
    # List of common reasoning tag names to remove
    reasoning_tags = [
        'redacted_reasoning',
        'think',
        'reasoning',
        'thought',
        'thinking'
    ]
    
    cleaned = text
    
    # Remove each type of reasoning tag (case-insensitive)
    for tag in reasoning_tags:
        # Pattern: <tag>...</tag> or <tag_name>...</tag_name>
        # Using non-greedy match (.*?) to match shortest possible content
        pattern = rf'<{tag}[^>]*>.*?</{tag}>'
        cleaned = re.sub(pattern, '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    
    # Also remove any self-closing reasoning tags
    for tag in reasoning_tags:
        pattern = rf'<{tag}[^>]*/>'
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Clean up extra whitespace (multiple newlines/spaces)
    cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)  # Multiple newlines -> double newline
    cleaned = cleaned.strip()
    
    return cleaned


class LLMClient:
    """Unified wrapper for LLM APIs (OpenAI and vLLM-compatible endpoints)."""
    
    def __init__(self):
        """Initialize the LLM client based on configured provider."""
        self.provider = settings.llm_provider.lower()
        self.model = settings.get_model_name
        
        # Configure HTTP client with connection pooling for better performance
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=settings.connection_pool_keepalive,
                max_connections=settings.connection_pool_max,
                keepalive_expiry=float(settings.connection_pool_keepalive_expiry)
            ),
            timeout=httpx.Timeout(
                connect=10.0,   # 10s to establish connection
                read=float(settings.request_timeout),
                write=10.0,     # 10s to write request
                pool=5.0        # 5s to get connection from pool
            )
        )
        
        if self.provider == "openai":
            # Standard OpenAI configuration with connection pooling
            self.client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                http_client=http_client
            )
            logger.info(f"Initialized OpenAI client with model: {self.model} (with connection pooling)")
        
        elif self.provider == "vllm":
            # vLLM uses OpenAI-compatible API with connection pooling
            self.client = AsyncOpenAI(
                api_key=settings.vllm_api_key,
                base_url=settings.get_vllm_base_url,
                http_client=http_client
            )
            logger.info(f"Initialized vLLM client at {settings.get_vllm_base_url} with model: {self.model} (with connection pooling)")
        
        elif self.provider == "chute":
            # Chute uses OpenAI-compatible API with connection pooling
            chute_api_key = settings.get_chute_api_key()
            self.client = AsyncOpenAI(
                api_key=chute_api_key,
                base_url=settings.chutes_base_url,
                http_client=http_client
            )
            logger.info(f"Initialized Chute client at {settings.chutes_base_url} with model: {self.model} (with connection pooling)")
        
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}. Use 'openai', 'vllm', or 'chute'.")
    
    async def generate_response(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Generate a response using GPT-4o.
        
        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            conversation_history: Previous conversation messages
            system_prompt: Optional system prompt to guide behavior
            response_format: Optional response format (e.g., {"type": "json_object"})
            
        Returns:
            Dictionary containing response and metadata
            
        Raises:
            OpenAIError: If the API call fails
        """
        try:
            # Prepare messages
            messages = []
            
            # Add system prompt if provided (must be first)
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            # Add conversation history if provided (filter out null/empty messages)
            if conversation_history:
                for i, msg in enumerate(conversation_history):
                    content = msg.get("content")
                    # Skip messages with null, empty, or non-string content
                    if content is None:
                        logger.warning(f"Skipping message {i} with null content")
                        continue
                    if not isinstance(content, str):
                        logger.warning(f"Skipping message {i} with non-string content: {type(content)}")
                        continue
                    if not content.strip():
                        logger.warning(f"Skipping message {i} with empty content")
                        continue
                    
                    # Add valid message
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": content
                    })
            
            # Add current prompt (skip if empty)
            if prompt and prompt.strip():
                messages.append({"role": "user", "content": prompt})
            
            logger.info(f"Prepared {len(messages)} messages for {self.provider.upper()} API")
            
            # Prepare API parameters
            # GPT-5 and newer models have different API requirements
            is_gpt5 = "gpt-5" in self.model.lower() or "o1" in self.model.lower()
            
            params = {
                "model": self.model,
                "messages": messages
            }
            
            # GPT-5 uses max_completion_tokens and only supports temperature=1 (default)
            if is_gpt5:
                params["max_completion_tokens"] = max_tokens or settings.max_tokens
                # GPT-5 only supports temperature=1 (default), don't set it
            else:
                params["max_tokens"] = max_tokens or settings.max_tokens
                params["temperature"] = temperature if temperature is not None else settings.temperature
            
            # Add response format if provided (for JSON mode)
            if response_format:
                params["response_format"] = response_format
            
            # Make API call with timing
            logger.info(f"Calling {self.provider.upper()} API with model: {self.model}")
            start_time = time.perf_counter()
            response = await self.client.chat.completions.create(**params)
            inference_time = time.perf_counter() - start_time
            
            # Extract response data
            message = response.choices[0].message
            raw_content = message.content or ""
            
            # Strip reasoning tags (like <think>...</think>)
            cleaned_content = strip_reasoning_tags(raw_content)
            
            result = {
                "response": cleaned_content,
                "model": response.model,
                "tokens_used": response.usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason
            }
            
            # Log inference to file
            _log_inference(
                provider=self.provider,
                model=response.model,
                tokens_used=result['tokens_used'],
                inference_time=inference_time,
                method="generate_response",
                finish_reason=result['finish_reason']
            )
            
            logger.info(f"Successfully generated response. Tokens used: {result['tokens_used']}, Inference time: {inference_time:.3f}s")
            logger.info(f"Response: {result['response']}")
            return result
            
        except OpenAIError as e:
            logger.error(f"{self.provider.upper()} API error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in generate_response: {str(e)}")
            raise
    
    async def complete_text(
        self,
        text_to_complete: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Complete/continue text using chat completion API (simulates text completion).
        
        This simulates the old completion API by using the chat API with the text
        as an assistant message prefix, prompting the model to continue naturally.
        
        Args:
            text_to_complete: The text prefix to continue from
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            system_prompt: Optional system prompt
            
        Returns:
            Dictionary containing completion and metadata
            
        Raises:
            OpenAIError: If the API call fails
        """
        try:
            # Prepare messages for text continuation
            messages = []
            
            # Add system prompt if provided
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            # Add the text as an assistant message (to continue from)
            # This tricks the model into continuing the text naturally
            messages.append({"role": "assistant", "content": text_to_complete})
            
            # Add a user message prompting continuation
            messages.append({"role": "user", "content": "Continue."})
            
            logger.info(f"Completing text (length: {len(text_to_complete)} chars)")
            
            # Prepare API parameters
            # GPT-5 and newer models have different API requirements
            is_gpt5 = "gpt-5" in self.model.lower() or "o1" in self.model.lower()
            
            params = {
                "model": self.model,
                "messages": messages
            }
            
            # GPT-5 uses max_completion_tokens and only supports temperature=1 (default)
            if is_gpt5:
                params["max_completion_tokens"] = max_tokens or settings.max_tokens
                # GPT-5 only supports temperature=1 (default), don't set it
            else:
                params["max_tokens"] = max_tokens or settings.max_tokens
                params["temperature"] = temperature if temperature is not None else settings.temperature
            
            # Make API call with timing
            start_time = time.perf_counter()
            response = await self.client.chat.completions.create(**params)
            inference_time = time.perf_counter() - start_time
            
            # Extract response data
            message = response.choices[0].message
            raw_content = message.content or ""
            
            # Strip reasoning tags (like <think>...</think>)
            cleaned_content = strip_reasoning_tags(raw_content)
            
            result = {
                "completion": cleaned_content,
                "model": response.model,
                "tokens_used": response.usage.total_tokens,
                "finish_reason": response.choices[0].finish_reason
            }
            
            # Log inference to file
            _log_inference(
                provider=self.provider,
                model=response.model,
                tokens_used=result['tokens_used'],
                inference_time=inference_time,
                method="complete_text",
                finish_reason=result['finish_reason']
            )
            
            logger.info(f"Successfully completed text. Tokens used: {result['tokens_used']}, Inference time: {inference_time:.3f}s")
            return result
            
        except OpenAIError as e:
            logger.error(f"{self.provider.upper()} API error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in complete_text: {str(e)}")
            raise
    
    async def generate_streaming_response(
        self,
        prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None
    ):
        """
        Generate a streaming response using GPT-4o.
        
        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature
            
        Yields:
            Response chunks as they arrive
        """
        try:
            # GPT-5 and newer models have different API requirements
            is_gpt5 = "gpt-5" in self.model.lower() or "o1" in self.model.lower()
            
            params = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True
            }
            
            # GPT-5 uses max_completion_tokens and only supports temperature=1
            if is_gpt5:
                params["max_completion_tokens"] = max_tokens or settings.max_tokens
                # GPT-5 only supports temperature=1 (default), don't set it
            else:
                params["max_tokens"] = max_tokens or settings.max_tokens
                params["temperature"] = temperature if temperature is not None else settings.temperature
            
            logger.info(f"Starting streaming response with model: {self.model}")
            start_time = time.perf_counter()
            tokens_used = 0
            finish_reason = None
            
            async for chunk in await self.client.chat.completions.create(**params):
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                # Try to get token usage from chunk if available
                if hasattr(chunk, 'usage') and chunk.usage:
                    tokens_used = chunk.usage.total_tokens
                # Try to get finish reason from chunk if available
                if hasattr(chunk.choices[0], 'finish_reason') and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
            
            inference_time = time.perf_counter() - start_time
            
            # Log inference to file (for streaming, tokens might be 0 if not available in chunks)
            _log_inference(
                provider=self.provider,
                model=self.model,
                tokens_used=tokens_used if tokens_used > 0 else 0,
                inference_time=inference_time,
                method="generate_streaming_response",
                finish_reason=finish_reason
            )
            
            logger.info(f"Streaming completed. Total inference time: {inference_time:.3f}s")
                    
        except OpenAIError as e:
            logger.error(f"{self.provider.upper()} streaming error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in streaming: {str(e)}")
            raise
    
    async def check_health(self) -> bool:
        """
        Check if the LLM API is accessible.
        
        Returns:
            True if API is accessible, False otherwise
        """
        try:
            # Simple test call
            # GPT-5 uses max_completion_tokens
            is_gpt5 = "gpt-5" in self.model.lower() or "o1" in self.model.lower()
            test_params = {
                "model": self.model,
                "messages": [{"role": "user", "content": "test"}]
            }
            if is_gpt5:
                test_params["max_completion_tokens"] = 5
            else:
                test_params["max_tokens"] = 5
            
            await self.client.chat.completions.create(**test_params)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return False


# Global client instance
llm_client = LLMClient()


def get_llm_client() -> LLMClient:
    """Get the global LLM client instance."""
    return llm_client


# Convenience function for easier imports
async def generate_response(
    prompt: str,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    system_prompt: Optional[str] = None,
    user_message: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None
) -> str:
    """
    Convenience function to generate a response using the global client.
    
    Args:
        prompt: The input prompt (used if user_message not provided)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        conversation_history: Previous conversation messages
        system_prompt: Optional system prompt to guide behavior
        user_message: Optional user message (overrides prompt if provided)
        response_format: Optional response format (e.g., {"type": "json_object"})
        
    Returns:
        The generated response text
    """
    result = await llm_client.generate_response(
        prompt=user_message if user_message else prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        conversation_history=conversation_history,
        system_prompt=system_prompt,
        response_format=response_format
    )
    return result["response"]


async def complete_text(
    text_to_complete: str,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    system_prompt: Optional[str] = None
) -> str:
    """
    Convenience function to complete/continue text using the global client.
    
    Args:
        text_to_complete: The text prefix to continue from
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature
        system_prompt: Optional system prompt
        
    Returns:
        The completed/continued text
    """
    result = await llm_client.complete_text(
        text_to_complete=text_to_complete,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=system_prompt
    )
    return result["completion"]
