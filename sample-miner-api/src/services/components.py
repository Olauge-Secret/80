"""Component implementations for the unified API interface.

All components follow the same pattern:
- Input: ComponentInput (task, input list, previous_outputs)
- Output: ComponentOutput (task, output, component)
"""

import json
import logging
import httpx
import asyncio
from typing import List, Optional

from src.models.models import (
    ComponentInput, 
    ComponentOutput, 
    ComponentOutputData,
    InputItem, 
    PreviousOutput
)
from src.services.llm_client import generate_response, get_llm_client
from src.core.conversation import ConversationContext
from src.services.playbook_service import PlaybookService

logger = logging.getLogger(__name__)

# Initialize playbook service (will be set up when first used)
_playbook_service = None


def get_playbook_service() -> PlaybookService:
    """Get or create playbook service instance."""
    global _playbook_service
    if _playbook_service is None:
        llm_client = get_llm_client()
        _playbook_service = PlaybookService(llm_client)
    return _playbook_service


async def get_context_additions(
    component_input: ComponentInput,
    context: ConversationContext,
    component_name: str
) -> tuple[list, str]:
    """
    Get conversation history and playbook context based on component input settings.
    
    Args:
        component_input: Component input with settings
        context: Conversation context
        component_name: Name of the component (for logging)
        
    Returns:
        Tuple of (conversation_history, playbook_context_string)
    """
    # Get conversation history if enabled
    conversation_history = []
    if component_input.use_conversation_history:
        conversation_history = await context.get_recent_messages(count=5)
        logger.info(f"[{component_name}] Using conversation history: {len(conversation_history)} messages")
    else:
        logger.info(f"[{component_name}] Conversation history disabled")
    
    # Get playbook context if enabled
    playbook_context = ""
    if component_input.use_playbook:
        try:
            playbook_service = get_playbook_service()
            playbook_entries = await playbook_service.get_playbook(component_input.cid)
            if playbook_entries:
                playbook_context = "\n\n" + playbook_service.format_playbook_context(playbook_entries)
                logger.info(f"[{component_name}] Using playbook: {len(playbook_entries)} entries")
        except Exception as e:
            logger.warning(f"[{component_name}] Failed to load playbook: {e}")
            playbook_context = ""  # Ensure empty string on failure
    else:
        logger.info(f"[{component_name}] Playbook disabled")
    
    return conversation_history, playbook_context


def _extract_notebook_content(notebook_value, component_name: str) -> str:
    """
    Extract notebook content from various formats (dict, JSON string, etc.).
    
    Args:
        notebook_value: The notebook value (can be dict, str, list, etc.)
        component_name: Name of component (for logging)
        
    Returns:
        Notebook content as a string
    """
    if isinstance(notebook_value, dict):
        # If it's a dict with 'content' field, extract that
        if "content" in notebook_value:
            content = notebook_value["content"]
            # If content is a list, join it
            if isinstance(content, list):
                return "\n\n".join(str(item) for item in content)
            return str(content)
        # If it's a dict with 'title' and 'content', prefer content
        elif "title" in notebook_value and "content" in notebook_value:
            content = notebook_value["content"]
            if isinstance(content, list):
                return "\n\n".join(str(item) for item in content)
            return str(content)
        # Otherwise, convert dict to formatted JSON string
        logger.info(f"[{component_name}] Notebook is dict, converting to JSON string")
        return json.dumps(notebook_value, indent=2)
    elif isinstance(notebook_value, str):
        # Check if notebook is a JSON-encoded string (double-encoded)
        notebook_stripped = notebook_value.strip()
        if notebook_stripped.startswith(("{", "[")) and notebook_stripped.endswith(("}", "]")):
            try:
                # Try to parse the JSON string
                parsed_notebook = json.loads(notebook_value)
                # Recursively extract content
                return _extract_notebook_content(parsed_notebook, component_name)
            except json.JSONDecodeError:
                # Not valid JSON, keep as-is
                logger.debug(f"[{component_name}] Notebook string looks like JSON but failed to parse, keeping as-is")
                return notebook_value
        return notebook_value
    elif isinstance(notebook_value, list):
        # If it's a list, join items
        return "\n\n".join(str(item) for item in notebook_value)
    else:
        # Convert to string
        return str(notebook_value)


def parse_json_response(response: str, component_name: str) -> tuple[str, str]:
    """
    Parse JSON response from LLM, handling various formats.
    
    Args:
        response: Raw response string from LLM
        component_name: Name of component (for logging)
        
    Returns:
        Tuple of (immediate_response, notebook_output)
    """
    # Helper function to try parsing a JSON string recursively
    def try_parse_json_string(value: str) -> tuple[Optional[str], Optional[str]]:
        """Try to parse a JSON string that might contain the actual response."""
        if not isinstance(value, str):
            return None, None
        
        value_stripped = value.strip()
        if not (value_stripped.startswith("{") and value_stripped.endswith("}")):
            return None, None
        
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                inner_immediate = parsed.get("immediate_response")
                inner_notebook = parsed.get("notebook")
                if inner_immediate is not None or inner_notebook is not None:
                    return inner_immediate, inner_notebook
        except json.JSONDecodeError:
            pass
        
        return None, None
    
    try:
        # Try to extract JSON from response (handle markdown code blocks and extra text)
        response_text = response.strip()
        
        # Strategy 1: Try to find JSON in markdown code blocks
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            # Try to find any code block that might contain JSON
            parts = response_text.split("```")
            for i in range(1, len(parts), 2):  # Check every other part (code blocks)
                try:
                    test_text = parts[i].strip()
                    # Remove language identifier if present
                    if test_text.startswith("json"):
                        test_text = test_text[4:].strip()
                    # Try to parse
                    json.loads(test_text)
                    response_text = test_text
                    break
                except (json.JSONDecodeError, IndexError):
                    continue
        
        # Strategy 2: Try to find JSON object boundaries in the text
        if not response_text.startswith("{"):
            # Look for first { and last }
            first_brace = response_text.find("{")
            last_brace = response_text.rfind("}")
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                response_text = response_text[first_brace:last_brace + 1]
        
        # Strategy 3: Try parsing as-is
        result = json.loads(response_text)
        immediate_response = result.get("immediate_response", response)
        notebook_output = result.get("notebook", "no update")
        
        # Check if immediate_response is itself a JSON-encoded string (double-encoded)
        inner_immediate, inner_notebook = try_parse_json_string(immediate_response)
        if inner_immediate is not None:
            logger.info(f"[{component_name}] Found JSON-encoded immediate_response, extracting inner content")
            immediate_response = inner_immediate
            if inner_notebook is not None:
                notebook_output = inner_notebook
        
        # Extract notebook content using helper function
        notebook_output = _extract_notebook_content(notebook_output, component_name)
        
        logger.debug(f"[{component_name}] Successfully parsed JSON response")
        return immediate_response, notebook_output
            
    except (json.JSONDecodeError, IndexError, ValueError) as e:
        logger.warning(f"[{component_name}] Failed to parse JSON response: {e}")
        logger.debug(f"[{component_name}] Response text (first 500 chars): {response[:500]}")
        
        # Fallback: Try multiple strategies
        fallback_strategies = [
            # Strategy 1: Find JSON object boundaries
            lambda: (response[response.find("{"):response.rfind("}") + 1] if response.find("{") != -1 and response.rfind("}") != -1 else None),
            # Strategy 2: Try to find last complete JSON object
            lambda: _find_last_json_object(response),
            # Strategy 3: Try to find first complete JSON object
            lambda: _find_first_json_object(response),
        ]
        
        for strategy_idx, strategy in enumerate(fallback_strategies, 1):
            try:
                json_text = strategy()
                if json_text:
                    result = json.loads(json_text)
                    immediate_response = result.get("immediate_response", response)
                    notebook_output = result.get("notebook", "no update")
                    
                    # Check if immediate_response is itself a JSON-encoded string
                    inner_immediate, inner_notebook = try_parse_json_string(immediate_response)
                    if inner_immediate is not None:
                        logger.info(f"[{component_name}] Fallback {strategy_idx}: Found JSON-encoded immediate_response")
                        immediate_response = inner_immediate
                        if inner_notebook is not None:
                            notebook_output = inner_notebook
                    
                    # Extract notebook content
                    notebook_output = _extract_notebook_content(notebook_output, component_name)
                    
                    logger.info(f"[{component_name}] Successfully parsed JSON using fallback strategy {strategy_idx}")
                    return immediate_response, notebook_output
            except Exception as fallback_error:
                logger.debug(f"[{component_name}] Fallback strategy {strategy_idx} failed: {fallback_error}")
                continue
        
        # All strategies failed
        logger.warning(f"[{component_name}] All JSON parsing attempts failed. Using raw response.")
        return response, "no update"


def _find_last_json_object(text: str) -> Optional[str]:
    """Find the last complete JSON object in text by matching braces."""
    if "}" not in text:
        return None
    
    last_brace = text.rfind("}")
    depth = 0
    start_pos = last_brace
    
    # Find matching opening brace
    for i in range(last_brace, -1, -1):
        if text[i] == "}":
            depth += 1
        elif text[i] == "{":
            depth -= 1
            if depth == 0:
                return text[i:last_brace + 1]
    
    return None


def _find_first_json_object(text: str) -> Optional[str]:
    """Find the first complete JSON object in text by matching braces."""
    if "{" not in text:
        return None
    
    first_brace = text.find("{")
    depth = 0
    start_pos = first_brace
    
    # Find matching closing brace
    for i in range(first_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[first_brace:i + 1]
    
    return None




async def component_complete(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Complete component: Process tasks with optional conversation history and playbook.
    
    Supports three miner types:
    - parent: Solves with LLM and stores solution in Redis
    - child: Waits for and fetches solution from Redis (no LLM call)
    - normal: Solves with LLM independently (no Redis)
    
    Args:
        component_input: Unified component input
        context: Conversation context with history
        
    Returns:
        ComponentOutput with the completed task
    """
    from src.core.config import settings
    from src.utils.task_hash import generate_task_hash
    
    miner_type = settings.miner_type
    logger.info(f"[complete] Processing task as {miner_type} miner: {component_input.task}")
    
    # Generate task hash for Redis key
    task_hash = generate_task_hash(component_input.task, component_input.input)
    logger.info(f"[complete] Task hash: {task_hash[:16]}...")
    
    # CHILD MINER: Wait for parent's solution in Redis
    if miner_type == "child":
        logger.info(f"[complete] Child miner waiting for parent solution...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            # Wait for solution from parent
            solution = await redis_service.wait_for_solution(
                task_hash=task_hash,
                timeout=settings.redis_wait_timeout
            )
            
            if solution:
                logger.info(f"[complete] ✅ Child received solution from parent")
                # Return the parent's solution
                return ComponentOutput(
                    cid=component_input.cid,
                    task=component_input.task,
                    input=component_input.input,
                    output=ComponentOutputData(
                        immediate_response=solution.get("immediate_response", ""),
                        notebook=solution.get("notebook", "no update")
                    ),
                    component="complete"
                )
            else:
                logger.warning(f"[complete] ⏰ Timeout waiting for parent solution, falling back to LLM")
                # Fall through to normal processing
        else:
            logger.error(f"[complete] ❌ Redis not available for child miner, falling back to LLM")
            # Fall through to normal processing
    
    # Build input text from all input items
    input_text_parts = []
    for idx, item in enumerate(component_input.input, 1):
        input_text_parts.append(f"Query {idx}: {item.user_query}")
    
    input_text = "\n\n".join(input_text_parts)
    
    # Build previous outputs context - LLM will read everything and decide intelligently
    previous_context = ""
    if component_input.previous_outputs:
        previous_context = "\n\nPrevious component outputs:\n"
        for prev in component_input.previous_outputs:
            # Show the complete output with immediate_response and notebook
            previous_context += f"\n[{prev.component}] {prev.task}:\n"
            previous_context += f"  Response: {prev.output.immediate_response}\n"
            if prev.output.notebook and prev.output.notebook != "no update":
                previous_context += f"  Notebook: {prev.output.notebook}\n"
    
    # Get conversation history and playbook context
    conversation_history, playbook_context = await get_context_additions(
        component_input, context, "complete"
    )
    
    # Build system prompt with Canvas-style instructions
    system_prompt = """You are an intelligent AI assistant that helps users complete tasks.

CRITICAL: You MUST respond with ONLY valid JSON. No markdown code blocks, no explanations outside JSON, no extra text.

Required JSON format:
{
  "immediate_response": "Your natural language explanation of what you did or your answer",
  "notebook": "Updated notebook content OR 'no update'"
}

Guidelines for notebook field:
- If task is conversational only: Return "no update"
- If there's ONE notebook and no changes needed: Return "no update"
- If there's ONE notebook and changes needed: Return the updated version
- If there are MULTIPLE notebooks: You MUST create new content (combine/choose/merge) - NEVER "no update"
- If creating new notebook: Return the full content

Your response must be ONLY the JSON object, nothing else."""
    
    if playbook_context:
        system_prompt += f"\n\nUser preferences and context:\n{playbook_context}"
    
    # Build task prompt
    task_prompt = f"""Task: {component_input.task}

Input:
{input_text}
{previous_context}

Complete this task and respond in JSON format."""
    
    # Generate response with optional conversation history
    response = await generate_response(
        prompt=task_prompt,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        temperature=0.7,
        response_format={"type": "json_object"}
    )
    
    # Parse JSON response
    immediate_response, notebook_output = parse_json_response(response, "complete")
    
    # Resolve "no update" for notebook - return previous notebook if exists
    if notebook_output == "no update" and component_input.previous_outputs:
        resolved = False
        for prev in component_input.previous_outputs:
            if prev.output.notebook and prev.output.notebook != "no update":
                notebook_output = prev.output.notebook
                logger.info(f"[complete] Resolved 'no update' to previous notebook from [{prev.component}]")
                resolved = True
                break
        
        if not resolved:
            logger.info(f"[complete] No previous notebook found to resolve - keeping 'no update'")
    
    # Store in conversation history
    await context.add_user_message(f"Task: {component_input.task}\n{input_text}")
    await context.add_assistant_message(immediate_response)
    
    # PARENT MINER: Store solution in Redis for children
    if miner_type == "parent":
        logger.info(f"[complete] Parent miner storing solution in Redis...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            solution_data = {
                "immediate_response": immediate_response,
                "notebook": notebook_output
            }
            
            success = await redis_service.store_solution(
                task_hash=task_hash,
                solution=solution_data,
                ttl=settings.redis_solution_ttl
            )
            
            if success:
                logger.info(f"[complete] ✅ Parent stored solution in Redis")
            else:
                logger.warning(f"[complete] ⚠️ Failed to store solution in Redis")
        else:
            logger.warning(f"[complete] ⚠️ Redis not available for parent miner")
    
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=immediate_response,
            notebook=notebook_output  # Resolved: new content, previous notebook, or "no update"
        ),
        component="complete"
    )


async def component_refine(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Refine component: Improve outputs with optional conversation history and playbook.
    
    Supports three miner types:
    - parent: Refines with LLM and stores result in Redis
    - child: Waits for and fetches result from Redis (no LLM call)
    - normal: Refines with LLM independently (no Redis)
    
    Args:
        component_input: Unified component input with previous outputs to refine
        context: Conversation context
        
    Returns:
        ComponentOutput with refined output
    """
    from src.core.config import settings
    from src.utils.task_hash import generate_task_hash
    
    miner_type = settings.miner_type
    logger.info(f"[refine] Processing task as {miner_type} miner: {component_input.task}")
    
    # Generate task hash for Redis key
    task_hash = generate_task_hash(component_input.task, component_input.input)
    logger.info(f"[refine] Task hash: {task_hash[:16]}...")
    
    # CHILD MINER: Wait for parent's result in Redis
    if miner_type == "child":
        logger.info(f"[refine] Child miner waiting for parent result...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            # Wait for result from parent
            result = await redis_service.wait_for_solution(
                task_hash=task_hash,
                timeout=settings.redis_wait_timeout
            )
            
            if result:
                logger.info(f"[refine] ✅ Child received result from parent")
                # Return the parent's result
                return ComponentOutput(
                    cid=component_input.cid,
                    task=component_input.task,
                    input=component_input.input,
                    output=ComponentOutputData(
                        immediate_response=result.get("immediate_response", ""),
                        notebook=result.get("notebook", "no update")
                    ),
                    component="refine"
                )
            else:
                logger.warning(f"[refine] ⏰ Timeout waiting for parent result, falling back to LLM")
                # Fall through to normal processing
        else:
            logger.error(f"[refine] ❌ Redis not available for child miner, falling back to LLM")
            # Fall through to normal processing
    
    # Build input text
    input_text_parts = []
    for idx, item in enumerate(component_input.input, 1):
        input_text_parts.append(f"Query {idx}: {item.user_query}")
    
    input_text = "\n\n".join(input_text_parts)
    
    # Build previous outputs context - LLM will read everything and decide intelligently
    previous_outputs_text = ""
    if component_input.previous_outputs:
        previous_outputs_text = "\n\nPrevious outputs to refine:\n"
        for prev in component_input.previous_outputs:
            previous_outputs_text += f"\n[{prev.component}] {prev.task}:\n"
            previous_outputs_text += f"  Response: {prev.output.immediate_response}\n"
            if prev.output.notebook and prev.output.notebook != "no update":
                previous_outputs_text += f"  Notebook: {prev.output.notebook}\n"
    
    # Get conversation history and playbook context
    conversation_history, playbook_context = await get_context_additions(
        component_input, context, "refine"
    )
    
    # Build system prompt with Canvas-style instructions
    system_prompt = """You are an AI assistant that refines and improves outputs.

CRITICAL: You MUST respond with ONLY valid JSON. No markdown code blocks, no explanations outside JSON, no extra text.

Required JSON format:
{
  "immediate_response": "Explanation of what you refined and why",
  "notebook": "The refined/improved content OR 'no update'"
}

Guidelines for notebook field:
- If providing feedback only: Set notebook to "no update"
- If there's ONE notebook and no improvements needed: Set to "no update"
- If there's ONE notebook and improvements needed: Write the improved version
- If there are MULTIPLE notebooks: You MUST create new content (refine one, combine, or merge) - NEVER "no update"

Your response must be ONLY the JSON object, nothing else."""
    
    if playbook_context:
        system_prompt += f"\n\nUser preferences:\n{playbook_context}"
    
    # Build refine prompt
    refine_prompt = f"""Task: {component_input.task}

Original Input:
{input_text}
{previous_outputs_text}

Refine and improve the outputs. Respond in JSON format."""
    
    # Generate response
    response = await generate_response(
        prompt=refine_prompt,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        temperature=0.7,
        response_format={"type": "json_object"}
    )
    
    # Parse JSON response
    immediate_response, notebook_output = parse_json_response(response, "refine")
    
    # Resolve "no update" for notebook - return previous notebook if exists
    if notebook_output == "no update" and component_input.previous_outputs:
        resolved = False
        for prev in component_input.previous_outputs:
            if prev.output.notebook and prev.output.notebook != "no update":
                notebook_output = prev.output.notebook
                logger.info(f"[refine] Resolved 'no update' to previous notebook from [{prev.component}]")
                resolved = True
                break
        
        if not resolved:
            logger.info(f"[refine] No previous notebook found to resolve - keeping 'no update'")
    
    # Store in conversation history
    await context.add_user_message(f"Refine task: {component_input.task}")
    await context.add_assistant_message(immediate_response)
    
    # PARENT MINER: Store result in Redis for children
    if miner_type == "parent":
        logger.info(f"[refine] Parent miner storing result in Redis...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            result_data = {
                "immediate_response": immediate_response,
                "notebook": notebook_output
            }
            
            success = await redis_service.store_solution(
                task_hash=task_hash,
                solution=result_data,
                ttl=settings.redis_solution_ttl
            )
            
            if success:
                logger.info(f"[refine] ✅ Parent stored result in Redis")
            else:
                logger.warning(f"[refine] ⚠️ Failed to store result in Redis")
        else:
            logger.warning(f"[refine] ⚠️ Redis not available for parent miner")
    
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=immediate_response,
            notebook=notebook_output  # Resolved: refined content, previous notebook, or "no update"
        ),
        component="refine"
    )


async def component_feedback(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Feedback component: Analyze outputs and provide structured feedback.
    
    Supports three miner types:
    - parent: Analyzes with LLM and stores result in Redis
    - child: Waits for and fetches result from Redis (no LLM call)
    - normal: Analyzes with LLM independently (no Redis)
    
    Args:
        component_input: Unified component input with outputs to analyze
        context: Conversation context
        
    Returns:
        ComponentOutput with structured feedback
    """
    from src.core.config import settings
    from src.utils.task_hash import generate_task_hash
    
    miner_type = settings.miner_type
    logger.info(f"[feedback] Processing task as {miner_type} miner: {component_input.task}")
    
    # Generate task hash for Redis key
    task_hash = generate_task_hash(component_input.task, component_input.input)
    logger.info(f"[feedback] Task hash: {task_hash[:16]}...")
    
    # CHILD MINER: Wait for parent's result in Redis
    if miner_type == "child":
        logger.info(f"[feedback] Child miner waiting for parent result...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            # Wait for result from parent
            result = await redis_service.wait_for_solution(
                task_hash=task_hash,
                timeout=settings.redis_wait_timeout
            )
            
            if result:
                logger.info(f"[feedback] ✅ Child received result from parent")
                # Return the parent's result
                return ComponentOutput(
                    cid=component_input.cid,
                    task=component_input.task,
                    input=component_input.input,
                    output=ComponentOutputData(
                        immediate_response=result.get("immediate_response", ""),
                        notebook=result.get("notebook", "no update")
                    ),
                    component="feedback"
                )
            else:
                logger.warning(f"[feedback] ⏰ Timeout waiting for parent result, falling back to LLM")
                # Fall through to normal processing
        else:
            logger.error(f"[feedback] ❌ Redis not available for child miner, falling back to LLM")
            # Fall through to normal processing
    
    # Build previous outputs to analyze
    outputs_to_analyze = ""
    if component_input.previous_outputs:
        outputs_to_analyze = "\n\nOutputs to analyze:\n"
        for prev in component_input.previous_outputs:
            # Access Pydantic object attributes
            outputs_to_analyze += f"\n[{prev.component}] {prev.task}:\n"
            outputs_to_analyze += f"  Response: {prev.output.immediate_response}\n"
            if prev.output.notebook and prev.output.notebook != "no update":
                outputs_to_analyze += f"  Notebook: {prev.output.notebook}\n"
    
    # Get conversation history and playbook context
    conversation_history, playbook_context = await get_context_additions(
        component_input, context, "feedback"
    )
    
    # Build system prompt
    system_prompt = "You are an AI assistant that provides constructive feedback."
    if playbook_context:
        system_prompt += f"\n{playbook_context}"
    
    # Build feedback prompt
    feedback_prompt = f"""Task: {component_input.task}
{outputs_to_analyze}

Analyze the outputs and provide structured feedback:

For each output, identify:
1. Strengths (what works well)
2. Weaknesses (what could be improved)
3. Specific suggestions for improvement

Format your feedback clearly with sections."""
    
    # Generate feedback
    response = await generate_response(
        prompt=feedback_prompt,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        temperature=0.7
    )
    
    # Store in conversation history
    await context.add_user_message(f"Feedback request: {component_input.task}")
    await context.add_assistant_message(response)
    
    # PARENT MINER: Store result in Redis for children
    if miner_type == "parent":
        logger.info(f"[feedback] Parent miner storing result in Redis...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            result_data = {
                "immediate_response": response,
                "notebook": "no update"
            }
            
            success = await redis_service.store_solution(
                task_hash=task_hash,
                solution=result_data,
                ttl=settings.redis_solution_ttl
            )
            
            if success:
                logger.info(f"[feedback] ✅ Parent stored result in Redis")
            else:
                logger.warning(f"[feedback] ⚠️ Failed to store result in Redis")
        else:
            logger.warning(f"[feedback] ⚠️ Redis not available for parent miner")
    
    # Feedback is conversational - no notebook editing
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=response,
            notebook="no update"
        ),
        component="feedback"
    )


async def component_human_feedback(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Human feedback component: Process and extract structured insights to playbook.
    
    Uses LLM to extract actionable insights from human feedback and stores them
    in a structured playbook (knowledge base) with operations (insert/update/delete).
    
    Inspired by: https://github.com/kayba-ai/agentic-context-engine
    
    Args:
        component_input: Unified component input with human feedback
        context: Conversation context
        
    Returns:
        ComponentOutput with summary of extracted insights
    """
    logger.info(f"[human_feedback] Processing task: {component_input.task}")
    
    # Extract human feedback from input
    feedback_text_parts = []
    for item in component_input.input:
        if item.user_query:
            feedback_text_parts.append(item.user_query)
    
    feedback_text = "\n".join(feedback_text_parts)
    

    
    if not feedback_text.strip():
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response="No feedback text provided.",
                notebook="no update"
            ),
            component="human_feedback"
        )
    
    logger.info(f"[human_feedback] Received feedback: {feedback_text[:100]}...")
    
    try:
        # Get playbook service
        playbook_service = get_playbook_service()
        
        # Get conversation context for better extraction
        messages = await context.get_messages()
        conversation_context = "\n".join([
            f"{msg['role']}: {msg['content'][:100]}..."
            for msg in messages[-5:]  # Last 5 messages
        ])
        
        # Extract insights using LLM
        insights = await playbook_service.extract_insights(
            feedback=feedback_text,
            cid=component_input.cid,
            context=conversation_context
        )
        
        # Apply operations to playbook
        entries = await playbook_service.apply_operations(
            insights=insights,
            cid=component_input.cid,
            source_feedback=feedback_text
        )
        
        # Format response
        if insights:
            response_parts = [
                "✅ Thank you for your feedback! I've analyzed it and extracted the following insights:\n"
            ]
            
            for idx, insight in enumerate(insights, 1):
                operation_emoji = {
                    "insert": "➕",
                    "update": "🔄",
                    "delete": "❌"
                }.get(insight["operation"], "•")
                
                response_parts.append(
                    f"{operation_emoji} **{insight['insight_type'].title()}** ({insight['operation']})\n"
                    f"   Key: `{insight['key']}`\n"
                    f"   Value: {insight['value']}\n"
                    f"   Confidence: {insight.get('confidence_score', 0.8):.0%}"
                )
                if insight.get('tags'):
                    response_parts.append(f"   Tags: {', '.join(insight['tags'])}")
                response_parts.append("")
            
            response_parts.append(
                f"\n📚 Your playbook now has {len(entries)} active entries. "
                "I'll use this knowledge in our future conversations!"
            )
            
            message = "\n".join(response_parts)
        else:
            message = (
                "Thank you for your feedback. However, I couldn't extract any "
                "actionable insights to add to your playbook. Your feedback has "
                "been stored in the conversation history for context."
            )
        
        logger.info(f"[human_feedback] Extracted {len(insights)} insights, created/updated {len(entries)} entries")
        
        # Store in conversation history
        await context.add_user_message(f"User feedback: {feedback_text}")
        await context.add_assistant_message(message)
        
        # Create JSON summary of insights for notebook
        notebook_data = {
            "feedback": feedback_text,
            "insights_extracted": len(insights),
            "entries_modified": len(entries),
            "insights": insights
        }
        
        notebook_json = json.dumps(notebook_data, indent=2)
        
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response=message,
                notebook=notebook_json  # Structured insights data
            ),
            component="human_feedback"
        )
        
    except Exception as e:
        logger.error(f"[human_feedback] Error processing feedback: {e}", exc_info=True)
        
        # Fallback to simple storage
        message = (
            f"Thank you for your feedback. I've noted it for future reference:\n\n"
            f"{feedback_text}\n\n"
            f"(Note: Advanced insight extraction encountered an error, but your "
            f"feedback is stored in conversation history)"
        )
        
        await context.add_user_message(f"User feedback: {feedback_text}")
        await context.add_assistant_message(message)
        
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response=message,
                notebook="no update"  # Error case
            ),
            component="human_feedback"
        )


class GoogleSearchClient:
    """Google Custom Search API client with async support."""
    
    def __init__(self):
        from src.core.config import settings
        self.base_url = "https://www.googleapis.com/customsearch/v1"
        self.params = {
            "key": settings.google_api_key,
            "cx": settings.google_cx_key,
        }
        logger.info("[internet_search] GoogleSearchClient initialized")
    
    async def asearch(self, query: str, num_results: int = 5) -> List[dict]:
        """
        Perform async Google search.
        
        Args:
            query: Search query string
            num_results: Number of results to return
            
        Returns:
            List of search result dictionaries with 'title', 'url', and 'snippet' keys
        """
        logger.info(f"[internet_search] Performing search for query: {query}, num_results: {num_results}")
        print("params", self.params)
        params = {**self.params, "q": query, "num": num_results}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
                search_results = response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[internet_search] HTTP error occurred: {e}")
                return []
            except Exception as e:
                logger.error(f"[internet_search] An error occurred during Google search: {e}")
                return []
        
        items = search_results.get('items', [])
        results = [
            {
                "title": item.get('title', 'No title'),
                "url": item.get('link', ''),
                "snippet": item.get('snippet', '')
            }
            for item in items
        ]
        
        logger.info(f"[internet_search] Search completed. Found {len(results)} results.")
        return results
    
    async def search_many(self, queries: List[str], num_results: int = 5) -> List[dict]:
        """
        Execute multiple search queries in parallel.
        
        Args:
            queries: List of search queries to execute
            num_results: Number of results to fetch per query
            
        Returns:
            Combined list of unique search results from all queries
        """
        logger.info(f"[internet_search] Starting search for {len(queries)} queries, {num_results} results each")
        
        # Create tasks for all searches
        search_tasks = [self.asearch(query, num_results) for query in queries]
        search_results = await asyncio.gather(*search_tasks)
        
        # Flatten the list of results and remove duplicates by URL
        seen_urls = set()
        unique_results = []
        
        for results in search_results:
            for result in results:
                url = result.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_results.append(result)
        
        logger.info(f"[internet_search] Completed search with {len(unique_results)} unique results")
        return unique_results


async def component_internet_search(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Internet search component: Search the internet using Google Custom Search API.
    
    Args:
        component_input: Unified component input with search queries
        context: Conversation context
        
    Returns:
        ComponentOutput with search results from Google Custom Search
    """
    logger.info(f"[internet_search] Processing task: {component_input.task}")
    
    # Extract search queries
    search_queries = []
    for item in component_input.input:
        search_queries.append(item.user_query)
    
    if not search_queries:
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response="No search queries provided.",
                notebook="no update"
            ),
            component="internet_search"
        )
    
    try:
        from src.core.config import settings
        
        # Check if API keys are configured
        if not settings.google_api_key or not settings.google_cx_key:
            logger.error("[internet_search] Google API keys not configured")
            response = "Google Custom Search API is not configured. Please set GOOGLE_API_KEY and GOOGLE_CX_KEY in .env file."
        else:
            # Initialize Google Search Client
            search_client = GoogleSearchClient()
            
            # Perform search (use search_many for multiple queries, asearch for single)
            if len(search_queries) == 1:
                results = await search_client.asearch(search_queries[0], num_results=7)
            else:
                results = await search_client.search_many(search_queries, num_results=5)
            
            if not results:
                response = f"No search results found for: {', '.join(search_queries)}"
            else:
                # Format search results
                result_lines = [f"Search results for: {', '.join(search_queries)}\n"]
                
                for idx, result in enumerate(results[:7], 1):  # Limit to 7 results
                    # Skip YouTube results
                    if "youtube.com" in result.get("url", ""):
                        continue
                    
                    title = result.get("title", "No title")
                    url = result.get("url", "")
                    snippet = result.get("snippet", "No description")
                    
                    result_lines.append(f"{idx}. {title}")
                    result_lines.append(f"   URL: {url}")
                    result_lines.append(f"   {snippet}")
                    result_lines.append("")
                
                response = "\n".join(result_lines)
                logger.info(f"[internet_search] Found {len(results)} results")
                    
    except httpx.HTTPStatusError as e:
        logger.error(f"[internet_search] HTTP error: {e}")
        response = f"HTTP error while searching: {str(e)}. Please check your API keys and internet connection."
    except httpx.RequestError as e:
        logger.error(f"[internet_search] Network error: {e}")
        response = f"Network error while searching: {str(e)}. Please check your internet connection."
    except Exception as e:
        logger.error(f"[internet_search] Unexpected error: {e}", exc_info=True)
        response = f"Unexpected error during search: {str(e)}"
    
    # Store in conversation history
    await context.add_user_message(f"Search: {', '.join(search_queries)}")
    await context.add_assistant_message(response)
    
    # Internet search is conversational - no notebook editing
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=response,
            notebook="no update"
        ),
        component="internet_search"
    )


async def component_summary(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Summary component: Use LLM to summarize previous outputs.
    
    Supports three miner types:
    - parent: Summarizes with LLM and stores result in Redis
    - child: Waits for and fetches result from Redis (no LLM call)
    - normal: Summarizes with LLM independently (no Redis)
    
    Args:
        component_input: Unified component input with outputs to summarize
        context: Conversation context
        
    Returns:
        ComponentOutput with summarized content
    """
    from src.core.config import settings
    from src.utils.task_hash import generate_task_hash
    
    miner_type = settings.miner_type
    logger.info(f"[summary] Processing task as {miner_type} miner: {component_input.task}")
    
    # Generate task hash for Redis key
    task_hash = generate_task_hash(component_input.task, component_input.input)
    logger.info(f"[summary] Task hash: {task_hash[:16]}...")
    
    # CHILD MINER: Wait for parent's result in Redis
    if miner_type == "child":
        logger.info(f"[summary] Child miner waiting for parent result...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            # Wait for result from parent
            result = await redis_service.wait_for_solution(
                task_hash=task_hash,
                timeout=settings.redis_wait_timeout
            )
            
            if result:
                logger.info(f"[summary] ✅ Child received result from parent")
                # Return the parent's result
                return ComponentOutput(
                    cid=component_input.cid,
                    task=component_input.task,
                    input=component_input.input,
                    output=ComponentOutputData(
                        immediate_response=result.get("immediate_response", ""),
                        notebook=result.get("notebook", "no update")
                    ),
                    component="summary"
                )
            else:
                logger.warning(f"[summary] ⏰ Timeout waiting for parent result, falling back to LLM")
                # Fall through to normal processing
        else:
            logger.error(f"[summary] ❌ Redis not available for child miner, falling back to LLM")
            # Fall through to normal processing
    
    # Build content to summarize from previous outputs
    content_to_summarize = []
    if component_input.previous_outputs:
        for prev in component_input.previous_outputs:
            # Access Pydantic object attributes
            output_text = f"[{prev.component}] {prev.task}:\n"
            output_text += f"Response: {prev.output.immediate_response}\n"
            if prev.output.notebook and prev.output.notebook != "no update":
                output_text += f"Notebook: {prev.output.notebook}\n"
            content_to_summarize.append(output_text)
    

    
    if not content_to_summarize:
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response="No previous outputs to summarize.",
                notebook="no update"
            ),
            component="summary"
        )
    
    combined_content = "\n\n---\n\n".join(content_to_summarize)
    
    # Get conversation history and playbook context
    conversation_history, playbook_context = await get_context_additions(
        component_input, context, "summary"
    )
    
    # Build system prompt with Canvas-style instructions
    system_prompt = """You are an AI assistant that creates concise, comprehensive summaries.

CRITICAL: You MUST respond with ONLY valid JSON. No markdown code blocks, no explanations outside JSON, no extra text.

Required JSON format:
{
  "immediate_response": "Your summary explanation",
  "notebook": "Summarized notebook content OR 'no update'"
}

Guidelines for notebook field:
- If there's NO notebook content in inputs: Return "no update"
- If there's ONE notebook to summarize: Return the summarized version
- If there are MULTIPLE notebooks: Create a combined summary

Your response must be ONLY the JSON object, nothing else."""
    
    if playbook_context:
        system_prompt += f"\n\nUser preferences:\n{playbook_context}"
    
    # Build summary prompt
    summary_prompt = f"""Task: {component_input.task}

Content to summarize:
{combined_content}

Create a comprehensive summary that:
1. Captures the main points and key insights
2. Maintains important details
3. Removes redundancy
4. Organizes information logically

Respond in JSON format."""
    
    # Generate summary
    response = await generate_response(
        prompt=summary_prompt,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        temperature=0.5,
        response_format={"type": "json_object"}
    )
    
    # Parse JSON response
    immediate_response, notebook_output = parse_json_response(response, "summary")
    
    # Resolve "no update" for notebook - return previous notebook if exists
    if notebook_output == "no update" and component_input.previous_outputs:
        resolved = False
        for prev in component_input.previous_outputs:
            if prev.output.notebook and prev.output.notebook != "no update":
                notebook_output = prev.output.notebook
                logger.info(f"[summary] Resolved 'no update' to previous notebook from [{prev.component}]")
                resolved = True
                break
        
        if not resolved:
            logger.info(f"[summary] No previous notebook found to resolve - keeping 'no update'")
    
    # Store in conversation history
    await context.add_user_message(f"Summarize: {component_input.task}")
    await context.add_assistant_message(immediate_response)
    
    # PARENT MINER: Store result in Redis for children
    if miner_type == "parent":
        logger.info(f"[summary] Parent miner storing result in Redis...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            result_data = {
                "immediate_response": immediate_response,
                "notebook": notebook_output
            }
            
            success = await redis_service.store_solution(
                task_hash=task_hash,
                solution=result_data,
                ttl=settings.redis_solution_ttl
            )
            
            if success:
                logger.info(f"[summary] ✅ Parent stored result in Redis")
            else:
                logger.warning(f"[summary] ⚠️ Failed to store result in Redis")
        else:
            logger.warning(f"[summary] ⚠️ Redis not available for parent miner")
    
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=immediate_response,
            notebook=notebook_output  # Resolved: summarized content, previous notebook, or "no update"
        ),
        component="summary"
    )


async def component_aggregate(
    component_input: ComponentInput,
    context: ConversationContext
) -> ComponentOutput:
    """
    Aggregate component: Perform majority voting on previous outputs.
    
    This component analyzes multiple previous outputs and identifies the most
    common or agreed-upon answer through majority voting logic.
    
    Supports three miner types:
    - parent: Aggregates with LLM and stores result in Redis
    - child: Waits for and fetches result from Redis (no LLM call)
    - normal: Aggregates with LLM independently (no Redis)
    
    Args:
        component_input: Unified component input with outputs to aggregate
        context: Conversation context
        
    Returns:
        ComponentOutput with aggregated result
    """
    from src.core.config import settings
    from src.utils.task_hash import generate_task_hash
    
    miner_type = settings.miner_type
    logger.info(f"[aggregate] Processing task as {miner_type} miner: {component_input.task}")
    
    # Generate task hash for Redis key
    task_hash = generate_task_hash(component_input.task, component_input.input)
    logger.info(f"[aggregate] Task hash: {task_hash[:16]}...")
    
    # CHILD MINER: Wait for parent's result in Redis
    if miner_type == "child":
        logger.info(f"[aggregate] Child miner waiting for parent result...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            # Wait for result from parent
            result = await redis_service.wait_for_solution(
                task_hash=task_hash,
                timeout=settings.redis_wait_timeout
            )
            
            if result:
                logger.info(f"[aggregate] ✅ Child received result from parent")
                # Return the parent's result
                return ComponentOutput(
                    cid=component_input.cid,
                    task=component_input.task,
                    input=component_input.input,
                    output=ComponentOutputData(
                        immediate_response=result.get("immediate_response", ""),
                        notebook=result.get("notebook", "no update")
                    ),
                    component="aggregate"
                )
            else:
                logger.warning(f"[aggregate] ⏰ Timeout waiting for parent result, falling back to LLM")
                # Fall through to normal processing
        else:
            logger.error(f"[aggregate] ❌ Redis not available for child miner, falling back to LLM")
            # Fall through to normal processing
    

    
    if not component_input.previous_outputs:
        return ComponentOutput(
            cid=component_input.cid,
            task=component_input.task,
            input=component_input.input,
            output=ComponentOutputData(
                immediate_response="No previous outputs to aggregate.",
                notebook="no update"
            ),
            component="aggregate"
        )
    
    # Build outputs for analysis
    outputs_text = []
    for idx, prev in enumerate(component_input.previous_outputs, 1):
        # Access Pydantic object attributes
        output_text = f"Output {idx} [{prev.component}]:\n"
        output_text += f"Response: {prev.output.immediate_response}\n"
        if prev.output.notebook and prev.output.notebook != "no update":
            output_text += f"Notebook: {prev.output.notebook}\n"
        outputs_text.append(output_text)
    
    combined_outputs = "\n\n---\n\n".join(outputs_text)
    
    # Get conversation history and playbook context
    conversation_history, playbook_context = await get_context_additions(
        component_input, context, "aggregate"
    )
    
    # Build system prompt with Canvas-style instructions
    system_prompt = """You are an AI assistant that aggregates multiple outputs using majority voting.

CRITICAL: You MUST respond with ONLY valid JSON. No markdown code blocks, no explanations outside JSON, no extra text.

Required JSON format:
{
  "immediate_response": "Your explanation of the consensus and voting results",
  "notebook": "The aggregated/consensus notebook content OR 'no update'"
}

Guidelines for notebook field:
- If there's NO notebook content in inputs: Return "no update"
- If there's ONE notebook: Return it as-is (or "no update" if no changes)
- If there are MULTIPLE notebooks: Create aggregated version using majority voting
- Use majority voting: Choose the most common content or merge agreements

Your response must be ONLY the JSON object, nothing else."""
    
    if playbook_context:
        system_prompt += f"\n\nUser preferences:\n{playbook_context}"
    
    # Build aggregate prompt
    aggregate_prompt = f"""Task: {component_input.task}

Multiple outputs to aggregate:
{combined_outputs}

Analyze these outputs and determine the consensus answer by:
1. Identifying common themes and agreements
2. Noting where outputs differ
3. Using majority voting logic to determine the most supported answer
4. Highlighting any important minority opinions

Respond in JSON format."""
    
    # Generate aggregate result
    response = await generate_response(
        prompt=aggregate_prompt,
        system_prompt=system_prompt,
        conversation_history=conversation_history,
        temperature=0.3,
        response_format={"type": "json_object"}
    )
    
    # Parse JSON response
    immediate_response, notebook_output = parse_json_response(response, "aggregate")
    
    # Resolve "no update" for notebook - return previous notebook if exists
    if notebook_output == "no update" and component_input.previous_outputs:
        resolved = False
        for prev in component_input.previous_outputs:
            if prev.output.notebook and prev.output.notebook != "no update":
                notebook_output = prev.output.notebook
                logger.info(f"[aggregate] Resolved 'no update' to previous notebook from [{prev.component}]")
                resolved = True
                break
        
        if not resolved:
            logger.info(f"[aggregate] No previous notebook found to resolve - keeping 'no update'")
    
    # Store in conversation history
    await context.add_user_message(f"Aggregate: {component_input.task}")
    await context.add_assistant_message(immediate_response)
    
    # PARENT MINER: Store result in Redis for children
    if miner_type == "parent":
        logger.info(f"[aggregate] Parent miner storing result in Redis...")
        from src.services.redis_service import get_redis_service
        
        redis_service = get_redis_service()
        if redis_service and redis_service.client:
            result_data = {
                "immediate_response": immediate_response,
                "notebook": notebook_output
            }
            
            success = await redis_service.store_solution(
                task_hash=task_hash,
                solution=result_data,
                ttl=settings.redis_solution_ttl
            )
            
            if success:
                logger.info(f"[aggregate] ✅ Parent stored result in Redis")
            else:
                logger.warning(f"[aggregate] ⚠️ Failed to store result in Redis")
        else:
            logger.warning(f"[aggregate] ⚠️ Redis not available for parent miner")
    
    return ComponentOutput(
        cid=component_input.cid,
        task=component_input.task,
        input=component_input.input,
        output=ComponentOutputData(
            immediate_response=immediate_response,
            notebook=notebook_output  # Resolved: aggregated content, previous notebook, or "no update"
        ),
        component="aggregate"
    )
