# backend/gcp_ops.py
import logging
import re # Keep re import in case it's needed elsewhere or in future changes
import os
import json
from google.cloud import secretmanager, logging as cloud_logging
from google.api_core.exceptions import NotFound, PermissionDenied
import config # Use centralized config

logger = logging.getLogger(__name__)

secret_manager_client = None
logging_client = None

def _init_clients():
    """Initializes GCP clients if not already initialized."""
    global secret_manager_client, logging_client
    # Check if initialization is needed (is None)
    if secret_manager_client is None:
        try:
            secret_manager_client = secretmanager.SecretManagerServiceClient()
            logger.info("Secret Manager client initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Secret Manager client: {e}", exc_info=True)
            # Mark as None on failure to allow potential retries if needed
            secret_manager_client = None
    # Check if initialization is needed (is None)
    if logging_client is None:
        try:
            # Project ID should be picked up automatically from the environment in GCF/Cloud Run
            logging_client = cloud_logging.Client()
            logger.info("Cloud Logging client initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Cloud Logging client: {e}", exc_info=True)
            # Mark as None on failure
            logging_client = None

def get_gcp_secret(secret_id, version="latest"):
    """Retrieves a secret value from GCP Secret Manager."""
    _init_clients()
    # Check if the client failed to initialize (is None)
    if secret_manager_client is None:
        logger.error("Secret Manager client is not available.")
        return None, "Secret Manager client unavailable."
    if not secret_id or not config.GCP_PROJECT_ID:
        logger.error("Secret ID or GCP Project ID is not configured.")
        return None, "Configuration error: Secret ID or Project ID missing."

    secret_name = f"projects/{config.GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version}"
    logger.info(f"Attempting to access secret: {secret_name}")

    try:
        response = secret_manager_client.access_secret_version(request={"name": secret_name})
        payload = response.payload.data.decode("UTF-8")
        logger.info(f"Successfully retrieved secret '{secret_id}'.")
        return payload, None # Return value and no error
    except NotFound:
        error_msg = f"Secret '{secret_name}' not found."
        logger.error(error_msg)
        return None, error_msg
    except PermissionDenied:
        error_msg = f"Permission denied accessing secret '{secret_name}'. Check function's Service Account roles."
        logger.error(error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error accessing secret '{secret_name}': {e}"
        logger.error(error_msg, exc_info=True)
        return None, error_msg

def get_cleaned_github_pat():
    """Retrieves and cleans the GitHub PAT using strip and prefix check."""
    if not config.GCP_GITHUB_PAT_SECRET_NAME:
        logger.error("GCP_GITHUB_PAT_SECRET_NAME is not configured.")
        return None, "GitHub PAT Secret Name not configured."

    raw_pat, error = get_gcp_secret(config.GCP_GITHUB_PAT_SECRET_NAME)
    if error:
        logger.error(f"Failed to retrieve GitHub PAT: {error}")
        return None, error
    if raw_pat is None: # Should not happen if error is None, but check anyway
        logger.error("Retrieved GitHub PAT secret value is None.")
        return None, "GitHub PAT secret value is None."

    # --- New Cleaning Logic ---
    cleaned_pat = raw_pat.strip()

    # Validate the cleaned PAT
    is_valid_pat_format = (
        cleaned_pat and (cleaned_pat.startswith("ghp_") or cleaned_pat.startswith("github_pat_"))
    )

    if is_valid_pat_format:
        if cleaned_pat != raw_pat: # Log if stripping actually removed characters
             logger.warning("Raw PAT needed cleaning (whitespace stripped).")
        logger.info("Cleaned GitHub PAT retrieved and validated successfully.")
        return cleaned_pat, None
    elif cleaned_pat: # It has content, but wrong format
        error_msg = "Invalid GitHub PAT format. Expected 'ghp_...' or 'github_pat_...'"
        logger.critical(error_msg) # Log as critical because auth will fail
        return None, error_msg
    else: # It's empty after stripping
        error_msg = "GitHub PAT secret value is empty after stripping whitespace."
        logger.error(error_msg)
        return None, error_msg


def get_gcf_logs(limit=50):
    """Retrieves the latest logs for the current Cloud Function."""
    _init_clients()
    # Check if the client failed to initialize (is None)
    if logging_client is None:
        logger.error("Cloud Logging client is not available.")
        return ["Error: Cloud Logging client unavailable."], "Logging client error."

    try:
        # Determine resource filter based on environment (prefer K_SERVICE for Gen2/Cloud Run)
        # Use a default function name if needed, though it might not be accurate
        default_func_name = 'ecko-http-function' # Fallback if no env var found
        function_name = os.environ.get('K_SERVICE', os.environ.get('FUNCTION_NAME', default_func_name))

        if os.environ.get('K_SERVICE'): # Gen2 / Cloud Run
            resource_type = "cloud_run_revision"
            label_key = "service_name"
        else: # Assume Gen1 or fallback
             resource_type = "cloud_function"
             label_key = "function_name"
             if function_name == default_func_name and not os.environ.get('FUNCTION_NAME'):
                 logger.warning(f"Using default function name '{default_func_name}' for logs as no specific env var was found.")


        filter_str = f'resource.type="{resource_type}" AND resource.labels.{label_key}="{function_name}" AND severity >= INFO'

        logger.info(f"Fetching GCF logs with filter: {filter_str} and limit: {limit}")

        # Fetch entries using the client
        log_entries_iterator = logging_client.list_entries(
            filter_=filter_str,
            order_by=cloud_logging.DESCENDING
            # page_size argument is deprecated for list_entries, use max_results instead if limiting is critical here
            # For simplicity, fetch default pages and limit in Python after retrieval
        )

        log_lines = []
        count = 0
        # Iterate pages and entries
        for entry in log_entries_iterator:
             if count >= limit:
                 break # Stop once the desired limit is reached

             timestamp_str = entry.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC') if entry.timestamp else '?'
             severity = getattr(entry, 'severity', 'DEFAULT') # Handle entries without severity
             message = ""
             payload = entry.payload # Can be dict, string, etc.

             # Handle different payload types
             if isinstance(payload, dict):
                 # Try common keys, fallback to JSON dump
                 message = payload.get('message', payload.get('textPayload', json.dumps(payload)))
             elif payload is not None:
                 message = str(payload)

             # Basic cleaning: replace newlines within message for single-line display in monitor
             # Avoid modifying structured JSON logs too much if that's the format
             if not isinstance(payload, dict):
                 message = ' '.join(message.splitlines())

             # Optional: Filter out noisy infrastructure messages
             # Convert severity to string for comparison if needed
             severity_str = str(severity).upper()
             if severity_str == "DEBUG" and ("Function execution started" in message or "Function execution took" in message):
                 continue # Skip only noisy DEBUG messages

             log_lines.append(f"[{timestamp_str}] [{severity_str}] {message}")
             count += 1

        # Reverse the list to show oldest first in the UI
        log_lines.reverse()

        logger.info(f"Retrieved {len(log_lines)} GCF log lines (limit was {limit}).")
        if not log_lines:
            return ["No recent log entries found for this function based on the filter."], None
        return log_lines, None

    except PermissionDenied:
         error_msg = "Permission denied reading logs. Check Service Account roles (Logs Viewer)."
         logger.error(error_msg)
         return [f"Error: {error_msg}"], error_msg
    except Exception as e:
        error_msg = f"Error retrieving GCF logs: {e}"
        logger.error(error_msg, exc_info=True)
        return [f"Error: {error_msg}"], error_msg