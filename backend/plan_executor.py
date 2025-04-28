# backend/plan_executor.py
import logging
import os # Keep for basic path checks if needed, but main traversal check moved

# --- Operation Constants (Imported from centralized config) ---
# Ensure these match definitions in config.py
from config import (
    OP_REPLACE_ENTIRE_FILE, OP_CREATE_FILE, OP_INSERT_LINES,
    OP_DELETE_LINES, OP_REPLACE_LINES, ALLOWED_OPS
)

logger = logging.getLogger(__name__)

def execute_plan(plan, current_files_content):
    """
    Executes a detailed modification plan, handling line-based operations.

    Applies operations sequentially. Changes made by one operation on a file
    are visible to subsequent operations on the same file within the same plan.

    Args:
        plan (list): A list of operation dictionaries from the LLM, validated
                     for basic structure by llm_interface.py.
                     Example Ops:
                     {"operation": "insert_lines", "file_path": "a.py", "after_line_number": 5, "lines_to_insert": ["new line 1", "new line 2"]}
                     {"operation": "delete_lines", "file_path": "b.txt", "start_line_number": 3, "end_line_number": 4}
                     {"operation": "replace_lines", "file_path": "a.py", "start_line_number": 10, "end_line_number": 12, "replacement_lines": ["replacement"]}
                     {"operation": "create_file", "file_path": "new.txt", "new_content": "Initial content"}
                     {"operation": "replace_entire_file", "file_path": "old.py", "new_content": "Rewritten content"}
        current_files_content (dict): A dictionary mapping relative file paths (str)
                                      to their *current* full content (str or None if unreadable).
                                      This is MANDATORY for line-based operations.

    Returns:
        dict: A dictionary mapping relative file paths to their final *new*
              string content after applying all operations. Keys are only files
              that were actually modified or created.
        list: A list of error or warning messages encountered during execution.
    """
    # Start with the current content; modifications will update this map
    # Use deep copy if modifying lists/dicts directly, but here we replace string values
    modified_content_map = current_files_content.copy()
    # Track only files that are actually changed by the plan
    final_changes_map = {}
    errors = []
    logger.info(f"Executing surgical plan with {len(plan)} operation(s)...")

    if not isinstance(plan, list):
        msg = "Plan execution failed: Input plan must be a list."
        logger.error(msg)
        return {}, [msg]
    # ===> Change Applied Here: Ensure current_files_content is required <===
    if current_files_content is None:
         msg = "Plan execution failed: current_files_content is required for surgical edits."
         logger.error(msg)
         return {}, [msg]

    for i, operation in enumerate(plan):
        op_type = operation.get("operation")
        file_path = operation.get("file_path")
        op_log_prefix = f"Plan Op {i+1} ({op_type} on {file_path}):"

        # --- Basic Validation (already done partly in LLM interface) ---
        if not isinstance(operation, dict) or op_type not in ALLOWED_OPS or not file_path:
            msg = f"{op_log_prefix} Skipping - Invalid operation structure or type."
            logger.warning(msg); errors.append(msg); continue

        # ===> Change Applied Here: Removed os.path based traversal check <===
        # Path traversal check is now handled primarily in git_ops.apply_changes
        # Basic path validation (no '..' or absolute) is done in llm_interface

        # --- Get Current State for the File ---
        # Use the potentially already modified content from previous ops in *this* plan execution
        current_content = modified_content_map.get(file_path)
        # Track if the file existed *before* this specific operation ran
        file_existed_before_op = file_path in modified_content_map

        # --- Execute Operation ---
        original_content_before_op = current_content # Store for comparison later
        new_content_after_op = current_content # Initialize
        operation_successful = False

        try:
            if op_type == OP_CREATE_FILE:
                new_content = operation.get("new_content")
                if not isinstance(new_content, str): raise ValueError("'new_content' (string) required.")
                if file_existed_before_op and current_content is not None: # Check if it had content before
                     logger.warning(f"{op_log_prefix} '{op_type}' requested for file that already exists/was created. Overwriting.")
                modified_content_map[file_path] = new_content
                new_content_after_op = new_content
                operation_successful = True
                logger.info(f"{op_log_prefix} File planned for creation/overwrite.")

            elif op_type == OP_REPLACE_ENTIRE_FILE:
                new_content = operation.get("new_content")
                if not isinstance(new_content, str): raise ValueError("'new_content' (string) required.")
                if not file_existed_before_op:
                     logger.warning(f"{op_log_prefix} '{op_type}' requested for file that didn't exist initially. Creating it.")
                modified_content_map[file_path] = new_content
                new_content_after_op = new_content
                operation_successful = True
                logger.info(f"{op_log_prefix} File planned for complete replacement.")

            else: # Line-based operations (Insert, Delete, Replace)
                 if current_content is None:
                      # Cannot perform line ops on file that doesn't exist or was unreadable initially
                      # and hasn't been created/replaced by a prior op in this plan
                      raise ValueError(f"Cannot perform line operation on non-existent or unreadable file '{file_path}'.")

                 lines = current_content.splitlines()
                 original_line_count = len(lines)

                 if op_type == OP_INSERT_LINES:
                     after_line = operation.get("after_line_number")
                     lines_to_insert = operation.get("lines_to_insert")
                     # ===> Change Applied Here: Confirm line number validation <===
                     if not isinstance(after_line, int) or after_line < 0: raise ValueError("Invalid 'after_line_number' (int >= 0).")
                     if not isinstance(lines_to_insert, list): raise ValueError("Invalid 'lines_to_insert' (list).")
                     # Ensure line number is within bounds (0 to original_line_count)
                     if after_line > original_line_count: raise ValueError(f"'after_line_number' ({after_line}) out of bounds (0-{original_line_count}).")

                     # ===> Change Applied Here: Confirm 0-based index conversion <===
                     insert_index = after_line # 0-based index IS the line number after which to insert
                     lines[insert_index:insert_index] = lines_to_insert # Python slice insert
                     new_content_after_op = "\n".join(lines)
                     modified_content_map[file_path] = new_content_after_op
                     operation_successful = True
                     logger.info(f"{op_log_prefix} Inserted {len(lines_to_insert)} lines after line {after_line}.")

                 elif op_type == OP_DELETE_LINES:
                     start_line = operation.get("start_line_number")
                     end_line = operation.get("end_line_number")
                     # ===> Change Applied Here: Confirm line number validation <===
                     if not isinstance(start_line, int) or start_line < 1: raise ValueError("Invalid 'start_line_number' (int >= 1).")
                     if not isinstance(end_line, int) or end_line < start_line: raise ValueError("Invalid 'end_line_number' (int >= start_line).")
                     # Check bounds (1-based for LLM, compare against original_line_count)
                     if start_line > original_line_count: raise ValueError(f"'start_line_number' ({start_line}) out of bounds (1-{original_line_count}).")
                     # ===> Change Applied Here: Confirm handling of end_line exceeding count <===
                     if end_line > original_line_count:
                          logger.warning(f"{op_log_prefix} 'end_line_number' ({end_line}) exceeds max line {original_line_count}. Deleting up to the end.")
                          end_line = original_line_count

                     # ===> Change Applied Here: Confirm 1-based to 0-based index conversion <===
                     start_index = start_line - 1
                     end_index = end_line # Exclusive index for slice deletion
                     deleted_count = len(lines[start_index:end_index])
                     del lines[start_index:end_index]
                     new_content_after_op = "\n".join(lines)
                     modified_content_map[file_path] = new_content_after_op
                     operation_successful = True
                     logger.info(f"{op_log_prefix} Deleted {deleted_count} lines from {start_line} to {end_line}.")

                 elif op_type == OP_REPLACE_LINES:
                     start_line = operation.get("start_line_number")
                     end_line = operation.get("end_line_number")
                     replacement_lines = operation.get("replacement_lines")
                     # ===> Change Applied Here: Confirm line number validation <===
                     if not isinstance(start_line, int) or start_line < 1: raise ValueError("Invalid 'start_line_number' (int >= 1).")
                     if not isinstance(end_line, int) or end_line < start_line: raise ValueError("Invalid 'end_line_number' (int >= start_line).")
                     if not isinstance(replacement_lines, list): raise ValueError("Invalid 'replacement_lines' (list).")
                     # Check bounds
                     if start_line > original_line_count: raise ValueError(f"'start_line_number' ({start_line}) out of bounds (1-{original_line_count}).")
                     # ===> Change Applied Here: Confirm handling of end_line exceeding count <===
                     if end_line > original_line_count:
                         logger.warning(f"{op_log_prefix} 'end_line_number' ({end_line}) exceeds max line {original_line_count}. Replacing up to the end.")
                         end_line = original_line_count

                     # ===> Change Applied Here: Confirm 1-based to 0-based index conversion <===
                     start_index = start_line - 1
                     end_index = end_line # Exclusive index for replacement end
                     replaced_count = len(lines[start_index:end_index])
                     lines[start_index:end_index] = replacement_lines # Python slice assignment handles replacement
                     new_content_after_op = "\n".join(lines)
                     modified_content_map[file_path] = new_content_after_op
                     operation_successful = True
                     logger.info(f"{op_log_prefix} Replaced {replaced_count} lines ({start_line}-{end_line}) with {len(replacement_lines)} new lines.")

            # After successful operation, check if content actually changed and update final map
            if operation_successful and new_content_after_op != original_content_before_op:
                final_changes_map[file_path] = new_content_after_op
                logger.debug(f"{op_log_prefix} Content changed. Added/Updated in final changes map.")
            elif operation_successful:
                logger.debug(f"{op_log_prefix} Operation applied, but content did not change.")
                # Ensure the file exists in the map if it was created, even if empty
                if op_type == OP_CREATE_FILE and file_path not in final_changes_map:
                    final_changes_map[file_path] = new_content_after_op


        except ValueError as ve:
            msg = f"{op_log_prefix} Skipping - Execution Error: {ve}"
            logger.warning(msg); errors.append(msg); continue
        except Exception as e:
            msg = f"{op_log_prefix} Skipping - Unexpected Execution Error: {e}"
            logger.error(msg, exc_info=True); errors.append(msg); continue


    logger.info(f"Plan execution finished. Final changes prepared for {len(final_changes_map)} files. Encountered {len(errors)} errors/warnings during execution.")
    # Return the map containing only the files whose content was actually changed or created
    return final_changes_map, errors