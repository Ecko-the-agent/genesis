import functions_framework
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig
from vertexai.preview.generative_models import Tool
# import google.cloud.secretmanager # <-- Αφαίρεση import

import os
import datetime
import pytz
import json
import tempfile
import shutil
from git import Repo, Actor
import traceback # Για καλύτερο logging σφαλμάτων

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "us-central1"
MODEL_NAME = "gemini-1.5-flash-preview-04-17"
GITHUB_REPO_URL = "github.com/Ecko-the-agent/genesis.git"
GITHUB_USER = "Ecko-the-agent"
# GITHUB_PAT_SECRET_NAME = "github-pat" # Δεν χρειάζεται πλέον το όνομα του secret εδώ
# --- ΝΕΑ ΣΤΑΘΕΡΑ για το όνομα του env var ---
GITHUB_PAT_ENV_VAR_NAME = "GITHUB_PAT_VALUE" # Το όνομα της μεταβλητής περιβάλλοντος που θα περιέχει το PAT

CONVERSATION_COLLECTION = "conversations"
MAIN_CHAT_HISTORY_DOC = "main_chat_history"
MAX_HISTORY_LENGTH = 20

# --- Initialize Clients ---
db = None
model = None
# secret_client = None # <-- Αφαίρεση secret client

try:
    vertexai.init(project=PROJECT_ID, location=REGION)
    print(f"Vertex AI initialized for project {PROJECT_ID} in {REGION}")
    db = google.cloud.firestore.Client()
    print("Firestore client initialized.")
    # secret_client = google.cloud.secretmanager.SecretManagerServiceClient() # <-- Αφαίρεση init
    # print("Secret Manager client initialized.") # <-- Αφαίρεση μηνύματος
    model = GenerativeModel(MODEL_NAME)
    print(f"Generative model {MODEL_NAME} loaded.")
    generation_config = GenerationConfig(temperature=0.7, max_output_tokens=1024)
except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    traceback.print_exc() # Εκτύπωσε το πλήρες σφάλμα στα logs
    raise RuntimeError(f"Initialization failed: {e}")

# --- Helper Functions ---

def get_github_pat(): # <-- Άλλαξε όνομα και λειτουργία
    """Ανακτά το GitHub PAT από τη μεταβλητή περιβάλλοντος."""
    github_pat = os.environ.get(GITHUB_PAT_ENV_VAR_NAME)
    if not github_pat:
        print(f"ERROR: Environment variable '{GITHUB_PAT_ENV_VAR_NAME}' not found or empty.")
        return None
    else:
        # Μην τυπώνεις το PAT στα logs!
        print(f"Successfully retrieved GitHub PAT from environment variable '{GITHUB_PAT_ENV_VAR_NAME}'.")
        return github_pat

# ... (get_conversation_history και add_to_conversation_history παραμένουν ίδια) ...

def execute_code_modification(instruction):
    """
    Εκτελεί την τροποποίηση κώδικα:
    1. Παίρνει οδηγίες από το LLM.
    2. Κλωνοποιεί το repo χρησιμοποιώντας PAT από env var.
    3. Εφαρμόζει αλλαγές.
    4. Κάνει Commit & Push.
    """
    if not model:
        return "Σφάλμα: Το LLM δεν είναι διαθέσιμο για να επεξεργαστεί την τροποποίηση."

    print(f"Attempting code modification based on: '{instruction}'")

    # --- 1. LLM για παραγωγή αλλαγών ---
    # ... (Η λογική του LLM παραμένει ίδια) ...
    # ... (Αντιγραφή κώδικα από προηγούμενη έκδοση για το LLM part) ...
    prompt = f"""
    You are Ecko, an AI agent capable of modifying your own source code stored in a Git repository.
    The user wants to make the following change: '{instruction}'

    Analyze the request and determine:
    1. Which file(s) in the repository (e.g., 'frontend/index.html', 'backend/main.py') need modification?
    2. What is the exact new content for the specified section(s) or the entire file(s)?

    Respond ONLY with a JSON object containing the file path as the key and the complete new file content as the value.
    Example for changing the H1 title:
    {{
      "frontend/index.html": "<!DOCTYPE html>\\n<html lang=\\"el\\">\\n<head>\\n    <meta charset=\\"UTF-8\\">\\n    <meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1.0\\">\\n    <title>Ecko Interface</title>\\n    <link rel=\\"stylesheet\\" href=\\"style.css\\">\\n</head>\\n<body>\\n    <h1>Ecko AI v2</h1> <!-- Changed Title -->\\n    <div id=\\"chatbox\\">\\n        <p><strong>Ecko:</strong> Γεια σου! Είμαι ο Ecko. Ρώτα με κάτι.</p>\\n    </div>\\n    <input type=\\"text\\" id=\\"userInput\\" placeholder=\\"Γράψε εδώ...\\" aria-label=\\"User input\\">\\n    <button id=\\"sendButton\\" onclick=\\"sendMessage()\\">Αποστολή</button>\\n    <div id=\\"loading\\" style=\\"display: none;\\">Περιμένετε...</div>\\n\\n    <script src=\\"ecko_script.js\\"></script>\\n</body>\\n</html>"
    }}

    If the request is unclear, too complex, or requires modifying multiple unrelated files in one go, respond with:
    {{ "error": "The request is too complex or unclear. Please provide a specific change for one file at a time." }}
    """
    try:
        response = model.generate_content(prompt)
        llm_output = response.text.strip()
        print(f"LLM Response for modification plan:\n{llm_output}")

        # Αφαίρεση πιθανών ```json ... ``` περιτυλιγμάτων
        if llm_output.startswith("```json"):
            llm_output = llm_output[7:]
        if llm_output.endswith("```"):
            llm_output = llm_output[:-3]
        llm_output = llm_output.strip()

        modification_plan = json.loads(llm_output)

        if "error" in modification_plan:
            return f"Αποτυχία τροποποίησης: {modification_plan['error']}"
        if not isinstance(modification_plan, dict) or not modification_plan:
             raise ValueError("LLM response is not a valid JSON object with file modifications.")

    except json.JSONDecodeError as json_err:
         print(f"JSON Decode Error from LLM output: {json_err}")
         print(f"Raw LLM Output was: {llm_output}")
         return f"Σφάλμα: Το AI δεν μπόρεσε να επεξεργαστεί το αίτημα τροποποίησης (Invalid JSON: {json_err}). Δοκίμασε μια πιο απλή αλλαγή."
    except Exception as e:
        print(f"LLM generation error for modification: {e}")
        traceback.print_exc()
        return f"Σφάλμα: Το AI δεν μπόρεσε να επεξεργαστεί το αίτημα τροποποίησης ({e})."


    # --- 2. Λήψη GitHub PAT (Από Env Var) ---
    github_pat = get_github_pat() # <-- Κάλεσε τη νέα συνάρτηση
    if not github_pat:
        # Το σφάλμα τυπώνεται ήδη μέσα στη get_github_pat()
        return "Σφάλμα: Αδυναμία ανάκτησης του GitHub token (δεν βρέθηκε η μεταβλητή περιβάλλοντος). Η τροποποίηση ακυρώθηκε."

    # --- 3. Δημιουργία Temp Dir & Clone ---
    # ... (Η λογική κλωνοποίησης, εφαρμογής αλλαγών, commit, push παραμένει ίδια) ...
    # ... (Αντιγραφή από προηγούμενη έκδοση) ...
    repo_dir = None
    try:
        with tempfile.TemporaryDirectory() as repo_dir:
            print(f"Created temporary directory: {repo_dir}")
            authenticated_repo_url = f"https://{github_pat}@{GITHUB_REPO_URL}" # Χρησιμοποιεί το PAT που πήραμε

            print(f"Cloning repository {GITHUB_REPO_URL} into {repo_dir}...")
            # Timeout προστέθηκε για robustness
            cloned_repo = Repo.clone_from(authenticated_repo_url, repo_dir, env={'GIT_TERMINAL_PROMPT': '0'}, progress=None) # Disable prompt
            print("Repository cloned successfully.")

            changed_files = []
            for file_path, new_content in modification_plan.items():
                if ".." in file_path or file_path.startswith("/"):
                     print(f"WARNING: Skipping potentially unsafe file path from LLM: {file_path}")
                     continue
                target_file = os.path.join(cloned_repo.working_tree_dir, file_path)
                print(f"Applying changes to: {target_file}")
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                with open(target_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                changed_files.append(file_path)

            if not changed_files:
                return "Δεν πραγματοποιήθηκαν αλλαγές (ίσως λόγω μη έγκυρων paths από το AI)."

            if cloned_repo.is_dirty(untracked_files=True):
                print("Changes detected. Staging, committing, and pushing...")
                committer = Actor("Ecko Agent (via GCF)", "ecko.the.agent+gcf@gmail.com")
                # Stage modified files
                cloned_repo.git.add(update=True)
                # Stage newly created files
                for file in changed_files:
                    # Handle potential errors if file doesn't exist (though it should)
                    try:
                        cloned_repo.git.add(os.path.join(cloned_repo.working_tree_dir, file))
                    except Exception as git_add_err:
                        print(f"Warning: Could not stage file {file}: {git_add_err}")

                commit_message = f"Automated code modification by Ecko: {instruction}"
                if len(commit_message) > 72:
                    commit_message = commit_message[:69] + "..."

                cloned_repo.index.commit(commit_message, author=committer, committer=committer)
                print("Changes committed locally.")

                origin = cloned_repo.remote(name='origin')
                print(f"Pushing to remote: {origin.url}") # Log the remote URL being pushed to
                push_info = origin.push()
                print("Changes pushed to origin.")

                for info in push_info:
                    if info.flags & (info.ERROR | info.REJECTED | info.REMOTE_REJECTED | info.REMOTE_FAILURE):
                        print(f"ERROR/REJECTION during push: {info.summary}")
                        return f"Σφάλμα: Οι αλλαγές έγιναν commit, αλλά απέτυχε το push στο GitHub. Summary: {info.summary}"

                return f"Επιτυχής τροποποίηση! Οι αλλαγές ({', '.join(changed_files)}) στάλθηκαν στο GitHub και θα εφαρμοστούν σύντομα."
            else:
                print("No changes detected after applying LLM plan.")
                return "Δεν εντοπίστηκαν αλλαγές για αποστολή στο GitHub."

    except Exception as e:
        print(f"ERROR during code modification execution: {e}")
        traceback.print_exc()
        return f"Κρίσιμο σφάλμα κατά την προσπάθεια τροποποίησης: {e}"


# --- Main HTTP Cloud Function ---
@functions_framework.http
def ecko_main(request):
    """HTTP Cloud Function entry point."""

    # --- CORS Preflight Handling ---
    # ... (Παραμένει ίδιο) ...
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*', # Προσοχή: Για ευκολία, αλλά σε παραγωγή καλύτερα συγκεκριμένο domain
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    # --- CORS Headers for Actual Response ---
    cors_headers = {
        'Access-Control-Allow-Origin': '*' # Προσοχή
    }

    # --- Check for Initialization Errors ---
    if not db or not model: # Αφαίρεσε τον έλεγχο για secret_client
         print("ERROR: Initialization failed (db or model). Function cannot proceed.")
         # Επιστροφή σωστού JSON format
         return (json.dumps({"error": "Internal server error during initialization."}), 500, cors_headers)


    # --- Handle POST Request ---
    # ... (Η υπόλοιπη λογική παραμένει ίδια, καλεί την execute_code_modification) ...
    if request.method == 'POST':
        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                return (json.dumps({"error": "Missing 'message' in request body"}), 400, cors_headers)

            user_message = request_json['message'].strip()
            print(f"Received message: {user_message}")

            ecko_response = "Συνέβη ένα απρόσμενο σφάλμα." # Default response

            # --- Λογική Επεξεργασίας ---
            modification_trigger = "Ecko, modify code: " # Η φράση-κλειδί
            if user_message.lower().startswith(modification_trigger.lower()):
                instruction = user_message[len(modification_trigger):].strip()
                if instruction:
                    add_to_conversation_history(user_message, 'user')
                    ecko_response = execute_code_modification(instruction)
                    add_to_conversation_history(ecko_response, 'model')
                else:
                    ecko_response = "Παρακαλώ δώσε μια συγκεκριμένη οδηγία τροποποίησης μετά τη φράση-κλειδί."
                    # Δεν καταγράφουμε
            else:
                # Κανονική συνομιλία
                add_to_conversation_history(user_message, 'user')
                conversation_history = get_conversation_history()
                chat = model.start_chat(history=conversation_history)
                llm_api_response = chat.send_message(
                    Part.from_text(user_message),
                    generation_config=generation_config,
                )
                ecko_response = llm_api_response.text
                add_to_conversation_history(ecko_response, 'model')

            print(f"Sending response: {ecko_response}")
            return (json.dumps({"response": ecko_response}), 200, cors_headers)

        except Exception as e:
            print(f"Error processing POST request: {e}")
            traceback.print_exc()
            return (json.dumps({"error": "An internal server error occurred."}), 500, cors_headers)
    else:
        print(f"Method not allowed: {request.method}")
        return (json.dumps({"error": "Method Not Allowed"}), 405, cors_headers)