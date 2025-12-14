"""Task hashing utility for generating consistent task identifiers."""

import hashlib
import json
from typing import Any, Dict


def generate_task_hash(task: str, inputs: list) -> str:
    """
    Generate a consistent hash for a task based on its content.
    
    This creates a deterministic hash so that all miners receive the same
    hash for identical tasks, allowing them to share solutions via Redis.
    
    Args:
        task: The task description
        inputs: List of input items
        
    Returns:
        A hex string hash (64 characters)
    """
    # Create a canonical representation of the task
    task_data = {
        "task": task.strip(),
        "inputs": [
            {
                "user_query": item.user_query.strip() if hasattr(item, 'user_query') else str(item).strip(),
                "notebook": item.notebook.strip() if hasattr(item, 'notebook') and item.notebook else ""
            }
            for item in inputs
        ]
    }
    
    # Convert to JSON with sorted keys for consistency
    task_json = json.dumps(task_data, sort_keys=True, ensure_ascii=True)
    
    # Generate SHA256 hash
    task_hash = hashlib.sha256(task_json.encode('utf-8')).hexdigest()
    
    return task_hash


def generate_simple_hash(text: str) -> str:
    """
    Generate a simple hash from text.
    
    Args:
        text: Text to hash
        
    Returns:
        A hex string hash (64 characters)
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

