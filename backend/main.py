import functions_framework
import os
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig
from datetime import datetime, timezone
import pytz # Για timezone conversion του Firestore timestamp
import json
import git # Για GitPython
import tempfile # Για προσωρινό φάκελο
import logging # Για καλύτερο logging

# --- Configuration ---
PROJECT_ID = "projectgenesis-457923"
REGION = "us-central1" # Η περιοχή όπου τρέχει η function και το Vertex AI
MODEL_NAME = "gemini-2.5-flash-preview-04-17" # Το μοντέλο που χρησιμοποιούμε (διορθωμένο!)
FIRESTORE_COLLECTION = "conversations"
CONVERSATION_DOC_ID = "main_chat_history"
GITHUB_REPO_URL_TEMPLATE = "https://{pat}@github.com/Ecko-the-agent/genesis.git" # Template for repo URL with PAT
COMMIT_AUTHOR_NAME = "Ecko Agent"
COMMIT_AUTHOR_EMAIL = "ecko.the.agent@gmail.com" # Χρησιμοποίησε το δικό σου email ή ένα no-reply

# Configure logging
logging.basicConfig(level=logging.INFO)

# --- Initialize Clients ---
try:
    firestore_db = google.cloud.firestore.Client()
    logging.info("Firestore client initialized successfully.")
except Exception as e:
    logging.error(f"Error initializing Firestore client: {e}")
    firestore_db = None

try:
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel(MODEL_NAME)
    logging.info(f"Vertex AI initialized successfully for model {MODEL_NAME} in region {REGION}.")
except Exception as e:
    logging.error(f"Error initializing Vertex AI: {e}")
    model = None

# --- Firestore Helper Functions ---
def get_conversation_history(limit=20):
    """Fetches the last 'limit' messages from Firestore."""
    if not firestore_db:
        logging.warning("Firestore client not available, returning empty history.")
        return []
    try:
        doc_ref = firestore_db.collection(FIRESTORE_COLLECTION).document(CONVERSATION_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            # Get messages, sort by timestamp (newest first), then take the limit, then reverse back to oldest first
            all_messages = doc.to_dict().get("messages", [])
            # Ensure timestamp exists and handle potential timezone issues
            def get_timestamp(msg):
                ts = msg.get('timestamp')
                if isinstance(ts, datetime):
                    # If already datetime, ensure it's timezone-aware (assume UTC if naive)
                    return ts.replace(tzinfo=ts.tzinfo or timezone.utc)
                return datetime.min.replace(tzinfo=timezone.utc) # Default for sorting if no timestamp

            sorted_messages = sorted(all_messages, key=get_timestamp, reverse=True)
            limited_messages = sorted_messages[:limit]
            # Reverse again to have oldest message first for the LLM context
            history = limited_messages[::-1]
            logging.info(f"Fetched {len(history)} messages from Firestore history.")
            return history
        else:
            logging.info("Conversation document does not exist, returning empty history.")
            return []
    except Exception as e:
        logging.error(f"Error getting conversation history from Firestore: {e}")
        return []

def add_to_conversation_history(sender, message):
    """Adds a message to the Firestore history."""
    if not firestore_db:
        logging.warning("Firestore client not available, cannot add message to history.")
        return
    try:
        doc_ref = firestore_db.collection(FIRESTORE_COLLECTION).document(CONVERSATION_DOC_ID)
        timestamp = datetime.now(pytz.utc) # Use timezone-aware timestamp
        new_message = {"sender": sender, "message": message, "timestamp": timestamp}

        doc = doc_ref.get()
        if doc.exists:
            doc_ref.update({
                "messages": google.cloud.firestore.ArrayUnion([new_message])
            })
            logging.info(f"Appended message from {sender} to Firestore.")
        else:
            # Create document if it doesn't exist
            doc_ref.set({
                "messages": [new_message]
            })
            logging.info(f"Created conversation document and added first message from {sender}.")
    except Exception as e:
        logging.error(f"Error adding message to Firestore history: {e}")


# --- Self-Modification Helper Functions ---

def get_github_pat():
    """Retrieves the GitHub PAT from the environment variable."""
    pat = os.environ.get("GITHUB_PAT_VALUE")
    if not pat:
        logging.error("GITHUB_PAT_VALUE environment variable not found.")
        return None
    logging.info("Successfully retrieved GitHub PAT from environment variable.")
    return pat

def generate_modification_plan(user_request):
    """Uses the LLM to generate a JSON plan for code modification."""
    if not model:
        logging.error("Vertex AI model not available for generating modification plan.")
        return None

    prompt = f"""
You are an AI assistant helping to modify the code of a project.
The user wants to make the following change: '{user_request}'

Analyze the request and generate a JSON object describing the necessary file modifications.
The JSON object should be a list of dictionaries, where each dictionary has:
- "file_path": The relative path to the file within the repository (e.g., "frontend/index.html", "backend/main.py").
- "new_content": The *entire* new content of the file after the modification.

Only output the JSON object, nothing else. Make sure the JSON is valid.

Example request: "Change the h1 title in index.html to 'Ecko Lives!'"
Example JSON output:
[
  {{
    "file_path": "frontend/index.html",
    "new_content": "<!DOCTYPE html>\\n<html lang=\\"el\\">\\n<head>...</head>\\n<body>\\n    <h1>Ecko Lives!</h1>\\n    ..."
  }}
]

Now, generate the JSON plan for the user request: '{user_request}'
"""
    try:
        logging.info(f"Sending request to LLM to generate modification plan for: {user_request}")
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                temperature=0.2, # Lower temperature for more predictable code/JSON generation
                max_output_tokens=8192 # Allow large file content
            )
        )
        plan_text = response.text.strip()
        logging.info(f"Raw response from LLM for plan:\n{plan_text}")

        # Clean potential markdown code block formatting
        if plan_text.startswith("```json"):
            plan_text = plan_text[7:]
        if plan_text.endswith("```"):
            plan_text = plan_text[:-3]
        plan_text = plan_text.strip()

        plan = json.loads(plan_text)
        logging.info(f"Successfully generated and parsed modification plan: {plan}")
        return plan
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON plan from LLM response: {e}\nRaw response was:\n{plan_text}")
        return None
    except Exception as e:
        logging.error(f"Error generating modification plan from LLM: {e}")
        return None

def execute_code_modification(plan, pat):
    """Clones the repo, applies changes from the plan, commits, and pushes."""
    if not pat:
        logging.error("GitHub PAT is missing, cannot execute modification.")
        return False, "GitHub PAT is missing."

    repo_url = GITHUB_REPO_URL_TEMPLATE.format(pat=pat)
    repo_dir = None # Initialize repo_dir

    try:
        # Create a temporary directory to clone the repo
        repo_dir = tempfile.mkdtemp()
        logging.info(f"Cloning repository {repo_url.replace(pat, '***PAT***')} into {repo_dir}") # Avoid logging PAT

        # Clone the repository
        repo = git.Repo.clone_from(repo_url, repo_dir)
        logging.info("Repository cloned successfully.")

        # Apply changes from the plan
        changed_files = []
        for item in plan:
            file_path_str = item.get("file_path")
            new_content = item.get("new_content")

            if not file_path_str or new_content is None:
                logging.warning(f"Skipping invalid plan item: {item}")
                continue

            # Ensure file path uses correct OS separators (though git usually handles it)
            # file_path = os.path.join(repo_dir, *file_path_str.split('/')) # Less reliable if path starts with /
            # Construct absolute path safely
            target_path = os.path.abspath(os.path.join(repo_dir, file_path_str))

            # Security check: Ensure the path is still within the cloned directory
            if not target_path.startswith(os.path.abspath(repo_dir)):
                 logging.error(f"Security risk: Attempted write outside repo directory: {file_path_str}")
                 raise ValueError(f"Invalid file path specified: {file_path_str}")

            logging.info(f"Modifying file: {target_path}")
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            # Write the new content (ensure newline consistency if needed)
            with open(target_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_content)
            changed_files.append(file_path_str) # Track relative path for git add

        if not changed_files:
             logging.warning("No valid file changes specified in the plan.")
             return False, "No valid file changes specified in the plan."

        # Stage the changes
        logging.info(f"Staging changed files: {changed_files}")
        repo.index.add(changed_files)

        # Commit the changes
        commit_message = "Automated code modification by Ecko Agent"
        logging.info(f"Committing changes with message: '{commit_message}'")
        # Configure author details for the commit
        repo.config_writer().set_value("user", "name", COMMIT_AUTHOR_NAME).release()
        repo.config_writer().set_value("user", "email", COMMIT_AUTHOR_EMAIL).release()
        repo.index.commit(commit_message)

        # Push the changes to the main branch
        logging.info("Pushing changes to origin main...")
        origin = repo.remote(name='origin')
        push_info = origin.push(refspec='main:main') # Push main branch

        # Check push results (optional, but good practice)
        for info in push_info:
            if info.flags & git.PushInfo.ERROR:
                logging.error(f"Error pushing changes: {info.summary}")
                raise git.GitCommandError("push", info.summary)
            elif info.flags & git.PushInfo.REJECTED:
                 logging.error(f"Push rejected: {info.summary}")
                 raise git.GitCommandError("push", info.summary)
            else:
                 logging.info(f"Push summary: {info.summary}")


        logging.info("Code modification pushed successfully.")
        return True, "Code modification completed and pushed successfully."

    except git.GitCommandError as e:
        logging.error(f"Git command error during modification: {e}")
        return False, f"Git command failed: {e}"
    except Exception as e:
        logging.error(f"Unexpected error during code modification: {e}")
        return False, f"An unexpected error occurred: {e}"
    finally:
        # Clean up the temporary directory
        if repo_dir and os.path.exists(repo_dir):
            try:
                # On Windows, git processes might hold locks. Add error handling.
                import shutil
                shutil.rmtree(repo_dir, ignore_errors=True)
                logging.info(f"Cleaned up temporary directory: {repo_dir}")
            except Exception as e_clean:
                logging.error(f"Error cleaning up temporary directory {repo_dir}: {e_clean}")

# --- CORS Helper ---
def _build_cors_preflight_response():
    response = ('', 204) # No content needed for preflight
    headers = {
        'Access-Control-Allow-Origin': '*', # Allow all origins for now
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Max-Age': '3600'
    }
    return response, headers

def _corsify_actual_response(response_body, status_code):
    headers = {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'application/json'
    }
    return (response_body, status_code, headers)

# --- Main Function Entry Point ---
@functions_framework.http
def ecko_main(request):
    """HTTP Cloud Function entry point."""

    # Handle CORS preflight requests (OPTIONS)
    if request.method == 'OPTIONS':
        response, headers = _build_cors_preflight_response()
        # The functions-framework expects a Flask Response object or (body, status, headers) tuple
        from flask import make_response
        resp = make_response(response)
        resp.headers.extend(headers)
        return resp # Must return a Flask Response for OPTIONS

    # Handle actual requests (POST)
    if request.method == 'POST':
        response_data = {}
        status_code = 500 # Default to internal server error

        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                response_data = {"error": "Missing 'message' in request body"}
                status_code = 400
                return _corsify_actual_response(json.dumps(response_data), status_code)

            user_message = request_json['message']
            logging.info(f"Received message: {user_message}")

            # Add user message to history *before* processing
            add_to_conversation_history("User", user_message)

            # --- Ecko Logic ---
            if user_message.lower().startswith("ecko, modify code:"):
                # --- Self-Modification Request ---
                modification_request = user_message[len("ecko, modify code:"):].strip()
                logging.info(f"Processing self-modification request: {modification_request}")

                pat = get_github_pat()
                if not pat:
                    ecko_response = "Error: Cannot perform modification, GitHub PAT is not configured correctly."
                    status_code = 500
                else:
                    plan = generate_modification_plan(modification_request)
                    if not plan:
                        ecko_response = "Error: Could not generate a modification plan from the LLM."
                        status_code = 500
                    else:
                        success, message = execute_code_modification(plan, pat)
                        ecko_response = message # Report success or failure message back to user
                        status_code = 200 if success else 500

            else:
                # --- Normal Conversation ---
                if not model:
                    ecko_response = "Error: The AI model is not available."
                    status_code = 500
                else:
                    # Prepare history for the LLM
                    history_messages = get_conversation_history()
                    vertex_history = []
                    for msg in history_messages:
                        role = 'user' if msg.get('sender') == 'User' else 'model'
                        # Ensure message content is a string
                        message_content = str(msg.get('message', ''))
                        vertex_history.append(Content(role=role, parts=[Part.from_text(message_content)]))

                    # Start chat with history
                    chat = model.start_chat(history=vertex_history)

                    # Send the new user message
                    logging.info(f"Sending message to LLM with history length: {len(vertex_history)}")
                    llm_response = chat.send_message(
                         Part.from_text(user_message),
                         generation_config=GenerationConfig(temperature=0.7) # Standard temperature for chat
                         )
                    ecko_response = llm_response.text
                    status_code = 200
                    logging.info(f"Received response from LLM: {ecko_response}")

            # Add Ecko's response to history
            add_to_conversation_history("Ecko", ecko_response)
            response_data = {"response": ecko_response}

        except Exception as e:
            logging.exception("An unexpected error occurred processing the request.") # Log full traceback
            response_data = {"error": "An internal server error occurred."}
            status_code = 500
            # Avoid adding generic error to history

        # Return the response with CORS headers
        return _corsify_actual_response(json.dumps(response_data), status_code)

    # Handle other methods (GET, PUT, DELETE, etc.)
    else:
        response_data = {"error": f"Method {request.method} not allowed."}
        status_code = 405
        # Return the response with CORS headers
        return _corsify_actual_response(json.dumps(response_data), status_code)