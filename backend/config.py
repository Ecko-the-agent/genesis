# backend/config.py
import os
import logging

# --- Basic Logging Setup ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level_name = getattr(logging, LOG_LEVEL, logging.INFO)
# Ensure logging is configured only once (important in GCF environment)
if not logging.getLogger().hasHandlers():
    logging.basicConfig(level=log_level_name, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Core GCP Settings ---
# GCP_PROJECT_ID is in REQUIRED_ENV_VARS, so no default here
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
# Region can have a default as it's less critical for basic function if not set,
# though deployment might fail if not specified there either.
REGION = os.environ.get("GCP_REGION", "us-central1")

# --- Vertex AI Settings ---
MODEL_NAME = os.environ.get("VERTEX_MODEL_NAME", "gemini-2.5-flash-preview-04-17") # Updated to a recommended model
GENERATION_CONFIG_CHAT = {"temperature": 0.7, "max_output_tokens": 2048}
# Increased max_output_tokens for plan generation to accommodate potentially larger outputs
GENERATION_CONFIG_PLAN = {"temperature": 0.15, "max_output_tokens": 8192} # Low temp for JSON/code/patches
GENERATION_CONFIG_ANALYZE = {"temperature": 0.4, "max_output_tokens": 4096}

# --- Firestore Settings ---
FIRESTORE_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "conversations")
CONVERSATION_DOC_ID = os.environ.get("CONVERSATION_DOC_ID", "main_chat_history_v2")
HISTORY_LIMIT = 30 # Number of messages to fetch for context

# --- GitHub Settings ---
# These are in REQUIRED_ENV_VARS, so no defaults here
GCP_GITHUB_PAT_SECRET_NAME = os.environ.get("GCP_GITHUB_PAT_SECRET_NAME")
GITHUB_REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER")
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPO_NAME")
# COMMIT_AUTHOR_EMAIL is now in REQUIRED_ENV_VARS
COMMIT_AUTHOR_EMAIL = os.environ.get("COMMIT_AUTHOR_EMAIL") # Removed default

# These can have defaults if they are standard
GITHUB_MAIN_BRANCH = os.environ.get("GITHUB_MAIN_BRANCH", "main")
GITHUB_REPO_URL_TEMPLATE = os.environ.get("GITHUB_REPO_URL_TEMPLATE", "https://{pat}@github.com/{owner}/{repo}.git")
COMMIT_AUTHOR_NAME = os.environ.get("COMMIT_AUTHOR_NAME", "Ecko Agent")
BACKEND_WORKFLOW_FILENAME = os.environ.get("BACKEND_WORKFLOW_FILENAME", "deploy-backend.yml")
FRONTEND_WORKFLOW_FILENAME = os.environ.get("FRONTEND_WORKFLOW_FILENAME", "deploy-frontend.yml")
GITHUB_API_BASE_URL = "https://api.github.com"

# --- Agent & Command Configuration ---
AGENT_NAME = "Ecko"
MODIFY_CODE_PREFIX = f"{AGENT_NAME.lower()}, manage project:"
LOG_QUERY_PREFIX = f"{AGENT_NAME.lower()}, show logs:"
DEPLOY_PREFIX = f"{AGENT_NAME.lower()}, deploy:"
STATUS_PREFIX = f"{AGENT_NAME.lower()}, status:"
LEGACY_MODIFY_PREFIX = f"{AGENT_NAME.lower()}, modify code:" # Support old command

# --- Security Configuration ---
# ECKO_SHARED_SECRET_ENV_VAR name itself is not secret
ECKO_SHARED_SECRET_ENV_VAR = "ECKO_SHARED_SECRET" # Name of env var holding the shared secret value
AUTH_HEADER_NAME = "X-Ecko-Auth" # Name of the custom header
# ALLOWED_ORIGIN is in REQUIRED_ENV_VARS, so no default here
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN") # The specific frontend URL (e.g., https://owner.github.io)

# --- Validation ---
REQUIRED_ENV_VARS = [
    "GCP_PROJECT_ID",
    "GCP_GITHUB_PAT_SECRET_NAME",
    "GITHUB_REPO_OWNER",
    "GITHUB_REPO_NAME",
    ECKO_SHARED_SECRET_ENV_VAR, # The *value* must be set in the environment
    "ALLOWED_ORIGIN",           # The frontend origin URL must be set
    "COMMIT_AUTHOR_EMAIL"       # Added as required
]

# ===> Change Applied Here: Use logger.debug for CORS origin logging <===
# Log the allowed origin for confirmation at DEBUG level
logger.debug(f"CORS Allowed Origin configured: {ALLOWED_ORIGIN}")


missing_vars = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
if missing_vars:
    # Log critical error AND raise exception to prevent startup
    error_message = f"CRITICAL: Missing required environment variables: {', '.join(missing_vars)}. Function cannot start."
    logger.critical(error_message)
    # Raise ValueError to halt execution if critical config is missing
    raise ValueError(error_message)
else:
    logger.info("All required configuration variables appear to be set.")