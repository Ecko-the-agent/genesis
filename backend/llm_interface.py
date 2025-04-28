# backend/llm_interface.py
import vertexai
from vertexai.generative_models import (
    GenerativeModel, Part, Content, GenerationConfig,
    SafetySetting, HarmCategory, HarmBlockThreshold
)
import logging
import json
import re
from datetime import datetime
import config # Import configuration

logger = logging.getLogger(__name__)
_model = None

# Safety settings (adjust as needed, blocking dangerous content is wise)
SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

# --- Operation Constants (Imported from centralized config) ---
# Ensure these match definitions in config.py
from config import (
    OP_REPLACE_ENTIRE_FILE, OP_CREATE_FILE, OP_INSERT_LINES,
    OP_DELETE_LINES, OP_REPLACE_LINES, ALLOWED_OPS
)

def _get_model():
    """Initializes and returns the Vertex AI model instance."""
    global _model
    if _model is None: # Initialize only once per function invocation
        try:
            if not config.GCP_PROJECT_ID or not config.REGION or not config.MODEL_NAME:
                 raise ValueError("Missing GCP/Vertex AI configuration.")
            logger.info(f"Initializing Vertex AI model '{config.MODEL_NAME}'...")
            vertexai.init(project=config.GCP_PROJECT_ID, location=config.REGION)
            _model = GenerativeModel(config.MODEL_NAME, safety_settings=SAFETY_SETTINGS)
            logger.info("Vertex AI model initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI model: {e}", exc_info=True)
            _model = False # Mark as failed
    # Return the model instance if successful, otherwise None
    return None if _model is False else _model

def _prepare_history(history_messages):
    """Converts Firestore history to Vertex AI Content list, limiting size."""
    vertex_history = []; token_count = 0; MAX_TOKENS = 8000 # Increased token limit for Gemini
    for msg in reversed(history_messages): # Process newest first
        role = 'user' if msg.get('sender') == 'User' else 'model' # Map sender to LLM roles
        content = str(msg.get('message', '')); tokens = len(content)//4 # Rough token estimation
        if token_count + tokens < MAX_TOKENS:
            # ===> Change Applied Here: Check for "Error:" prefix explicitly <===
            if content and not content.startswith("Error:"): # Skip empty/error messages
                vertex_history.append(Content(role=role, parts=[Part.from_text(content)])); token_count += tokens
        else: logger.warning(f"Truncating history at ~{token_count} tokens."); break
    vertex_history.reverse() # Return chronological order
    logger.info(f"Prepared {len(vertex_history)} history messages (~{token_count} tokens).")
    return vertex_history

def generate_chat_response(history, user_message):
    """Generates a conversational response."""
    model_instance = _get_model();
    if not model_instance: return {"error": "AI model unavailable."}, 503
    vertex_history = _prepare_history(history)
    logger.info(f"Generating chat response for: '{user_message[:100]}...'")
    try:
        chat = model_instance.start_chat(history=vertex_history)
        # Use generation config from config.py
        response = chat.send_message(Part.from_text(user_message), generation_config=GenerationConfig(**config.GENERATION_CONFIG_CHAT))
        # Check for valid content in response
        if not response.candidates or not response.candidates[0].content.parts:
            reason = response.candidates[0].finish_reason.name if response.candidates else "UNKNOWN"
            safety = response.candidates[0].safety_ratings if response.candidates else "N/A"
            logger.error(f"LLM chat response generation stopped/empty. Reason: {reason}, Safety: {safety}")
            return {"error": f"AI response blocked/empty ({reason})."}, 500
        # Concatenate parts for full response
        ecko_response = "".join(p.text for p in response.candidates[0].content.parts).strip()
        logger.info("Received chat response.")
        return {"response": ecko_response}, 200
    except Exception as e: logger.error(f"LLM chat error: {e}"); return {"error": f"Error communicating with AI: {e}"}, 500

def generate_modification_plan(user_request, files_content):
    """
    Generates a JSON plan for precise code modifications using detailed operations.
    """
    model_instance = _get_model();
    if not model_instance: return None, "Error: AI model unavailable."

    # --- Prepare Context ---
    context_str = "Current project file contents (line numbers are 1-based):\n\n"
    total_chars = 0; MAX_CHARS = 100000 # Limit context size
    included_count = 0
    file_line_counts = {} # Store line counts for validation later if needed
    for path, content_str in sorted(files_content.items()):
        if content_str is None: content_str = "[UNREADABLE]" # Indicate unreadable files
        # Add line numbers to context for LLM reference
        lines = content_str.splitlines()
        file_line_counts[path] = len(lines) # Store actual line count
        # ===> Change Applied Here: Ensure 1-based numbering f"{i+1}: {line}" <===
        numbered_content = "\n".join(f"{i+1}: {line}" for i, line in enumerate(lines))
        file_entry = f"--- File: {path} ({len(lines)} lines) ---\n{numbered_content}\n---\n\n"
        if total_chars + len(file_entry) <= MAX_CHARS:
            context_str += file_entry; total_chars += len(file_entry); included_count += 1
        else: context_str += "[CONTEXT TRUNCATED]\n"; logger.warning("Truncated context for LLM plan."); break
    logger.info(f"LLM context: {included_count} files, {total_chars} chars.")

    # --- Define the NEW Prompt for Surgical Edits ---
    # ===> Change Applied Here: Ensure prompt details match instructions <===
    prompt = f"""Analyze the user request based on the provided file contents (with 1-based line numbers).
Generate a JSON list representing a precise plan to fulfill the request.

**Allowed Operations:**
1.  `"{OP_REPLACE_ENTIRE_FILE}"`: Replaces the entire file content. Use for major rewrites or replacing unreadable files if necessary.
    - Required keys: `"operation"`, `"file_path"`, `"new_content"` (string)
2.  `"{OP_CREATE_FILE}"`: Creates a new file.
    - Required keys: `"operation"`, `"file_path"`, `"new_content"` (string)
3.  `"{OP_INSERT_LINES}"`: Inserts new lines *after* a specific line number. Use `0` for inserting at the beginning.
    - Required keys: `"operation"`, `"file_path"`, `"after_line_number"` (integer, 0-based index equivalent), `"lines_to_insert"` (list of strings)
4.  `"{OP_DELETE_LINES}"`: Deletes a range of lines (inclusive). Line numbers are 1-based.
    - Required keys: `"operation"`, `"file_path"`, `"start_line_number"` (integer, 1-based), `"end_line_number"` (integer, 1-based)
5.  `"{OP_REPLACE_LINES}"`: Replaces a range of lines (inclusive) with new lines. Line numbers are 1-based.
    - Required keys: `"operation"`, `"file_path"`, `"start_line_number"` (integer, 1-based), `"end_line_number"` (integer, 1-based), `"replacement_lines"` (list of strings)

**Instructions:**
- Be precise with file paths and line numbers based *only* on the provided context.
- Use the **minimum** number of operations necessary. Prefer line-based ops over `replace_entire_file` unless absolutely required.
- Ensure line numbers are valid within the context of each file (use the 1-based numbers shown).
- Output **ONLY** the raw JSON list `[...]`. Do not include explanations or markdown formatting around the JSON.
- If no changes are needed, or the request is unsafe or unclear, output an empty list `[]`.

**User Request:** "{user_request}"

**File Context:**
{context_str}
```json
""" # Enforce JSON output format

    try:
        logger.info(f"Generating surgical modification plan...")
        # Use generation config from config.py
        response = model_instance.generate_content(prompt, generation_config=GenerationConfig(**config.GENERATION_CONFIG_PLAN))

        # --- Response Handling & Validation ---
        if not response.candidates or not response.candidates.content.parts:
            reason = response.candidates.finish_reason.name if response.candidates else "UNKNOWN"; safety = response.candidates.safety_ratings if response.candidates else "N/A"
            logger.error(f"LLM plan generation stopped/empty. Reason: {reason}, Safety: {safety}")
            return None, f"Error: AI plan generation blocked/empty ({reason})."

        # Extract and parse JSON robustly
        plan_text = "".join(p.text for p in response.candidates.content.parts).strip(); logger.debug(f"Raw surgical plan: {plan_text}")
        match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', plan_text, re.DOTALL | re.MULTILINE)
        plan_str = match.group(1).strip() if match else plan_text
        if not (plan_str.startswith('[') and plan_str.endswith(']')): raise ValueError("Response not a JSON list.")
        plan = json.loads(plan_str)
        if not isinstance(plan, list): raise ValueError("Parsed plan is not a list.")

        # --- Detailed Validation of Operations ---
        # ===> Change Applied Here: Confirm validation logic matches instructions <===
        validated_plan = []
        for i, op in enumerate(plan):
            op_log_prefix = f"Plan Op {i+1}:"
            if not isinstance(op, dict):
                logger.warning(f"{op_log_prefix} Invalid format (not a dict). Skipping: {op}"); continue
            op_type = op.get("operation")
            file_path = op.get("file_path")

            # Basic checks
            if op_type not in ALLOWED_OPS: logger.warning(f"{op_log_prefix} Invalid operation type '{op_type}'. Skipping."); continue
            if not file_path or not isinstance(file_path, str) or ".." in file_path or file_path.startswith("/"):
                logger.warning(f"{op_log_prefix} Invalid file_path '{file_path}'. Skipping."); continue

            # Operation-specific validation
            valid_op = True
            if op_type in [OP_REPLACE_ENTIRE_FILE, OP_CREATE_FILE]:
                if not isinstance(op.get("new_content"), str): valid_op = False; logger.warning(f"{op_log_prefix} Missing/invalid 'new_content' (string).")
            elif op_type == OP_INSERT_LINES:
                if not isinstance(op.get("after_line_number"), int) or op.get("after_line_number") < 0: valid_op = False; logger.warning(f"{op_log_prefix} Invalid 'after_line_number' (int >= 0).")
                if not isinstance(op.get("lines_to_insert"), list): valid_op = False; logger.warning(f"{op_log_prefix} Invalid 'lines_to_insert' (list).")
            elif op_type in [OP_DELETE_LINES, OP_REPLACE_LINES]:
                start_line = op.get("start_line_number")
                end_line = op.get("end_line_number")
                if not isinstance(start_line, int) or start_line < 1: valid_op = False; logger.warning(f"{op_log_prefix} Invalid 'start_line_number' (int >= 1).")
                if not isinstance(end_line, int) or end_line < start_line: valid_op = False; logger.warning(f"{op_log_prefix} Invalid 'end_line_number' (int >= start_line).")
                if op_type == OP_REPLACE_LINES and not isinstance(op.get("replacement_lines"), list): valid_op = False; logger.warning(f"{op_log_prefix} Invalid 'replacement_lines' (list).")
            # Could add checks against file_line_counts here if needed, but plan_executor is a better place

            if valid_op:
                validated_plan.append(op)
            else:
                 logger.warning(f"{op_log_prefix} Invalid operation structure skipped: {op}")

        if len(validated_plan) != len(plan): logger.warning(f"Plan validation removed {len(plan)-len(validated_plan)} items.")
        logger.info(f"Validated surgical plan includes {len(validated_plan)} operations.")
        return validated_plan, None # Return validated plan

    except (json.JSONDecodeError, ValueError) as e: logger.error(f"JSON plan error: {e}\nResponse:\n{plan_text}"); return None, f"Error: AI response invalid JSON ({e})."
    except Exception as e: logger.error(f"LLM plan generation error: {e}", exc_info=True); return None, f"Error generating plan: {e}"


def analyze_log_data(user_query, log_lines):
    """Analyzes log lines."""
    model_instance = _get_model();
    if not model_instance: return {"error": "AI model unavailable."}, 503
    # Ensure log_lines is a single string for the prompt
    if isinstance(log_lines, list): log_context = "\n".join(log_lines)
    elif isinstance(log_lines, str): log_context = log_lines
    else: log_context = str(log_lines) # Fallback conversion

    MAX_CHARS=25000
    if len(log_context) > MAX_CHARS: log_context = "...[TRUNCATED]\n" + log_context[-MAX_CHARS:]; logger.warning(f"Truncated logs to {MAX_CHARS} chars.")
    prompt = f"""Analyze logs based on query. Be concise. Query: "{user_query}"\nLogs:\n```\n{log_context}\n```\nAnalysis:"""
    try:
        logger.info(f"Generating log analysis...")
        # Use generation config from config.py
        response = model_instance.generate_content(prompt, generation_config=GenerationConfig(**config.GENERATION_CONFIG_ANALYZE))
        if not response.candidates or not response.candidates.content.parts:
             reason = response.candidates.finish_reason.name if response.candidates else "UNKNOWN"
             logger.error(f"LLM log analysis stopped/empty. Reason: {reason}"); return {"error": f"Log analysis blocked/empty ({reason})."}, 500
        analysis = "".join(p.text for p in response.candidates.content.parts).strip()
        logger.info("Received log analysis.")
        return {"response": analysis}, 200
    except Exception as e: logger.error(f"LLM log analysis error: {e}"); return {"error": f"Error analyzing logs: {e}"}, 500