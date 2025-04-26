import functions_framework
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig

import os
import datetime
import pytz
import json
import tempfile
import shutil
from git import Repo, Actor
import subprocess # Keep using subprocess for clone
import traceback

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "us-central1"
MODEL_NAME = "gemini-1.5-flash"
GITHUB_REPO_URL = "github.com/Ecko-the-agent/genesis.git" # Base URL
GITHUB_USER = "Ecko-the-agent"
GITHUB_PAT_ENV_VAR_NAME = "GITHUB_PAT_VALUE"

CONVERSATION_COLLECTION = "conversations"
MAIN_CHAT_HISTORY_DOC = "main_chat_history"
MAX_HISTORY_LENGTH = 20

# --- Initialize Clients ---
db = None
model = None
print("Initializing Ecko Backend...")
try:
    vertexai.init(project=PROJECT_ID, location=REGION)
    print(f"Vertex AI initialized for project {PROJECT_ID} in {REGION}")
    db = google.cloud.firestore.Client()
    print("Firestore client initialized.")
    model = GenerativeModel(MODEL_NAME)
    print(f"Generative model {MODEL_NAME} loaded.")
    generation_config = GenerationConfig(temperature=0.7, max_output_tokens=2048)
except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    traceback.print_exc()
    raise RuntimeError(f"Initialization failed: {e}")

# --- Helper Functions ---
def get_github_pat():
    github_pat = os.environ.get(GITHUB_PAT_ENV_VAR_NAME)
    if not github_pat:
        print(f"ERROR: Environment variable '{GITHUB_PAT_ENV_VAR_NAME}' not found or empty.")
        return None
    else:
        print(f"Successfully retrieved GitHub PAT from environment variable '{GITHUB_PAT_ENV_VAR_NAME}'.")
        return github_pat

def get_conversation_history(doc_id=MAIN_CHAT_HISTORY_DOC, limit=MAX_HISTORY_LENGTH):
    if not db:
        print("Error: Firestore client not initialized in get_conversation_history.")
        return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            messages = doc.to_dict().get("messages", [])[-limit:]
            history = []
            for msg in messages:
                role = 'user' if msg.get('sender', '').lower() == 'user' else 'model'
                if msg.get('message'):
                    history.append(Content(role=role, parts=[Part.from_text(msg.get('message'))]))
                else:
                    print(f"Warning: Skipping empty message in history from sender '{msg.get('sender')}'")
            print(f"Retrieved and formatted history for Vertex AI: {len(history)} items")
            return history
        else:
            print(f"Conversation history document '{doc_id}' not found.")
            return []
    except Exception as e:
        print(f"Error getting conversation history (doc: {doc_id}): {e}")
        traceback.print_exc()
        return []


def add_to_conversation_history(message_text, sender, doc_id=MAIN_CHAT_HISTORY_DOC):
    if not db:
        print("Error: Firestore client not initialized in add_to_conversation_history.")
        return
    if not message_text or not message_text.strip():
        print(f"Warning: Attempted to add empty message from sender '{sender}'. Skipping.")
        return
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(pytz.utc)
        new_message = {"sender": sender, "message": message_text, "timestamp": timestamp}
        doc_ref.set({"messages": google.cloud.firestore.ArrayUnion([new_message])}, merge=True)
        print(f"Added message from '{sender}' to history (doc: {doc_id}).")
    except Exception as e:
        print(f"Error adding to conversation history (doc: {doc_id}): {e}")
        traceback.print_exc()

def execute_code_modification(instruction):
    if not model: return "Σφάλμα: Το LLM δεν είναι διαθέσιμο για να επεξεργαστεί την τροποποίηση."
    print(f"Attempting code modification based on: '{instruction}'")

    # --- 1. LLM ---
    # ... (Same LLM logic) ...
    prompt = f"""
    You are Ecko, an AI agent capable of modifying your own source code stored in a Git repository ({GITHUB_REPO_URL}).
    The user wants to make the following change: '{instruction}'
    Analyze the request and determine:
    1. Which file(s) in the repository (e.g., 'frontend/index.html', 'backend/main.py') need modification? Use relative paths from the root of the repository.
    2. What is the exact new content for the specified section(s) or the entire file(s)? Ensure the new content is complete and correct for the file type.
    Respond ONLY with a valid JSON object containing the file path as the key and the complete new file content as the string value.
    Example for changing the H1 title in frontend/index.html:
    {{
      "frontend/index.html": "<!DOCTYPE html>\\n<html lang=\\"el\\">\\n<head>\\n    <meta charset=\\"UTF-8\\">\\n    <meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1.0\\">\\n    <title>Ecko Interface</title>\\n    <link rel=\\"stylesheet\\" href=\\"style.css\\">\\n</head>\\n<body>\\n    <h1>Ecko Agent v2.0</h1> <!-- Changed Title -->\\n    <div id=\\"chatbox\\">\\n        <p><strong>Ecko:</strong> Γεια σου! Είμαι ο Ecko. Ρώτα με κάτι.</p>\\n    </div>\\n    <input type=\\"text\\" id=\\"userInput\\" placeholder=\\"Γράψε εδώ...\\" aria-label=\\"User input\\">\\n    <button id=\\"sendButton\\" onclick=\\"sendMessage()\\">Αποστολή</button>\\n    <div id=\\"loading\\" style=\\"display: none;\\">Περιμένετε...</div>\\n\\n    <script src=\\"ecko_script.js\\"></script>\\n</body>\\n</html>"
    }}
    If the request is unclear, too complex, involves actions other than modifying file content (like creating/deleting files, running commands), or requires modifying multiple unrelated files in one go, respond with:
    {{ "error": "The request is unclear, too complex, involves unsupported actions, or targets multiple files. Please provide a specific content change request for one file at a time." }}
    """
    llm_output = ""
    try:
        response = model.generate_content(prompt)
        llm_output = response.text.strip()
        print(f"LLM Response for modification plan:\n-----\n{llm_output}\n-----")
        if llm_output.startswith("```json"): llm_output = llm_output[7:]
        if llm_output.endswith("```"): llm_output = llm_output[:-3]
        llm_output = llm_output.strip()
        modification_plan = json.loads(llm_output)
        if "error" in modification_plan: return f"Αποτυχία τροποποίησης: {modification_plan['error']}"
        if not isinstance(modification_plan, dict) or not modification_plan: raise ValueError("LLM response is not a valid JSON object with file modifications.")
    except json.JSONDecodeError as json_err:
         print(f"JSON Decode Error from LLM output: {json_err}\nRaw Output: {llm_output}")
         return f"Σφάλμα: Το AI δεν μπόρεσε να επεξεργαστεί το αίτημα τροποποίησης (Invalid JSON)."
    except Exception as e:
        print(f"LLM generation error for modification: {e}")
        traceback.print_exc()
        return f"Σφάλμα: Το AI δεν μπόρεσε να επεξεργαστεί το αίτημα τροποποίησης ({e})."

    # --- 2. Get PAT ---
    github_pat = get_github_pat()
    if not github_pat: return "Σφάλμα: Αδυναμία ανάκτησης του GitHub token."

    # --- 3. Clone using subprocess ---
    repo_dir = None
    try:
        with tempfile.TemporaryDirectory() as repo_dir:
            print(f"Created temporary directory: {repo_dir}")

            repo_path = GITHUB_REPO_URL.rstrip('/')
            authenticated_repo_url = f"https://{github_pat}@{repo_path}"

            # *** ADDED LOGGING ***
            print(f"EXACT URL passed to subprocess: '{authenticated_repo_url}'")

            git_command = ["git", "clone", "--quiet", authenticated_repo_url, repo_dir]
            print(f"Executing command list: {git_command[:3]} ['********'] {git_command[4:]}")

            env = os.environ.copy()
            env['GIT_TERMINAL_PROMPT'] = '0'
            process = subprocess.run(git_command, capture_output=True, text=True, check=False, env=env)

            if process.returncode != 0:
                print(f"ERROR: git clone failed with exit code {process.returncode}")
                stderr_output = process.stderr.strip()
                print(f"stderr: {stderr_output}")
                print(f"stdout: {process.stdout.strip()}")
                # Return the exact stderr from Git
                return f"Κρίσιμο σφάλμα κατά την κλωνοποίηση του repository: {stderr_output}"

            print("Repository cloned successfully using subprocess.")

            # --- 4. Apply Changes ---
            # ... (Same logic) ...
            cloned_repo = Repo(repo_dir)
            changed_files = []
            for file_path, new_content in modification_plan.items():
                if ".." in file_path or file_path.startswith("/"):
                     print(f"WARNING: Skipping potentially unsafe file path from LLM: {file_path}")
                     continue
                normalized_file_path = os.path.join(*file_path.split('/'))
                target_file = os.path.join(cloned_repo.working_tree_dir, normalized_file_path)
                print(f"Applying changes to: {target_file}")
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                with open(target_file, 'w', encoding='utf-8') as f: f.write(new_content)
                changed_files.append(file_path)
            if not changed_files: return "Δεν πραγματοποιήθηκαν αλλαγές (ίσως λόγω μη έγκυρων paths από το AI)."

            # --- 5. Commit & Push ---
            # ... (Same logic) ...
            if cloned_repo.is_dirty(untracked_files=True):
                print("Changes detected. Staging, committing, and pushing...")
                committer = Actor("Ecko Agent (via GCF)", f"{GITHUB_USER}+gcf@users.noreply.github.com")
                cloned_repo.git.add(update=True)
                for file_path in changed_files:
                    normalized_file_path = os.path.join(*file_path.split('/'))
                    full_path = os.path.join(cloned_repo.working_tree_dir, normalized_file_path)
                    if os.path.exists(full_path):
                         print(f"Staging file: {full_path}")
                         cloned_repo.git.add(full_path)
                    else: print(f"Warning: File {full_path} not found after write, cannot stage.")
                commit_message = f"Automated code modification by Ecko: {instruction}"
                if len(commit_message) > 72: commit_message = commit_message[:69] + "..."
                if cloned_repo.index.diff("HEAD") or cloned_repo.untracked_files:
                    print("Staged changes found, proceeding with commit.")
                    cloned_repo.index.commit(commit_message, author=committer, committer=committer)
                    print("Changes committed locally.")
                    origin = cloned_repo.remote(name='origin')
                    print(f"Pushing to remote branch 'main': {origin.url}")
                    push_info = origin.push(refspec='HEAD:main')
                    print("Push command executed.")
                    push_failed = False
                    for info in push_info:
                        if info.flags & (info.ERROR | info.REJECTED | info.REMOTE_REJECTED | info.REMOTE_FAILURE):
                            print(f"ERROR/REJECTION during push: Flags={info.flags}, Summary={info.summary}")
                            push_failed = True
                    if push_failed: return f"Σφάλμα: Οι αλλαγές έγιναν commit, αλλά απέτυχε το push στο GitHub. Ελέγξτε τα logs."
                    return f"Επιτυχής τροποποίηση! Οι αλλαγές ({', '.join(changed_files)}) στάλθηκαν στο GitHub."
                else:
                     print("No changes staged for commit after add.")
                     return "Δεν εντοπίστηκαν αλλαγές προς αποστολή (ίσως τα αρχεία ήταν ήδη ενημερωμένα)."
            else:
                print("No changes detected after applying LLM plan (repo not dirty).")
                return "Δεν εντοπίστηκαν αλλαγές για αποστολή στο GitHub."

    except Exception as e:
        print(f"ERROR during code modification execution: {e}")
        traceback.print_exc()
        return f"Κρίσιμο σφάλμα κατά την προσπάθεια τροποποίησης: {e}"


# --- Main HTTP Cloud Function ---
@functions_framework.http
def ecko_main(request):
    """HTTP Cloud Function entry point."""
    # ... (CORS, Init checks, POST handling, Normal Chat logic - same as before) ...
    if request.method == 'OPTIONS':
        headers = {'Access-Control-Allow-Origin': '*','Access-Control-Allow-Methods': 'POST, OPTIONS','Access-Control-Allow-Headers': 'Content-Type','Access-Control-Max-Age': '3600'}
        return ('', 204, headers)
    cors_headers = {'Access-Control-Allow-Origin': '*'}
    if not db or not model:
         print("ERROR: Initialization failed (db or model). Function cannot proceed.")
         return (json.dumps({"error": "Internal server error during initialization."}), 500, cors_headers)

    if request.method == 'POST':
        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                print("Error: Missing or invalid JSON body or 'message' field.")
                return (json.dumps({"error": "Invalid request body. 'message' field is required."}), 400, cors_headers)

            user_message = request_json['message'].strip()
            print(f"Received message: {user_message}")
            ecko_response = "Συνέβη ένα απρόσμενο σφάλμα κατά την επεξεργασία."

            modification_trigger = "Ecko, modify code: "
            if user_message.lower().startswith(modification_trigger.lower()):
                instruction = user_message[len(modification_trigger):].strip()
                if instruction:
                    print(f"Modification instruction received: '{instruction}'")
                    add_to_conversation_history(user_message, 'user')
                    ecko_response = execute_code_modification(instruction)
                    add_to_conversation_history(ecko_response, 'model')
                else:
                    ecko_response = "Παρακαλώ δώσε μια συγκεκριμένη οδηγία τροποποίησης μετά τη φράση-κλειδί."
                    print("Received empty modification instruction.")
            else:
                add_to_conversation_history(user_message, 'user')
                conversation_history = get_conversation_history()
                try:
                    print(f"Starting chat with history (length: {len(conversation_history)})...")
                    chat = model.start_chat(history=conversation_history)
                    llm_api_response = chat.send_message(Part.from_text(user_message), generation_config=generation_config)
                    ecko_response = llm_api_response.text
                    print(f"LLM chat response received successfully: '{ecko_response[:100]}...'")
                    add_to_conversation_history(ecko_response, 'model')
                except Exception as llm_error:
                    print(f"Error during LLM communication: {llm_error}")
                    traceback.print_exc()
                    ecko_response = "Συγγνώμη, αντιμετώπισα ένα πρόβλημα κατά την προσπάθεια να σου απαντήσω."
                    add_to_conversation_history(ecko_response, 'model')

            print(f"Sending final response (200 OK): '{ecko_response[:100]}...'")
            return (json.dumps({"response": ecko_response}), 200, cors_headers)

        except Exception as e:
            print(f"Critical error processing POST request: {e}")
            traceback.print_exc()
            return (json.dumps({"error": "An internal server error occurred."}), 500, cors_headers)
    else:
        print(f"Method not allowed: {request.method}")
        return (json.dumps({"error": "Method Not Allowed"}), 405, cors_headers)