import functions_framework
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig
# from vertexai.preview.generative_models import Tool # Δεν χρησιμοποιούμε tools προς το παρόν
# import google.cloud.secretmanager # Δεν το χρησιμοποιούμε πια

import os
import datetime
import pytz # <-- Βεβαιώσου ότι είναι εδώ
import json
import tempfile
import shutil
from git import Repo, Actor # <-- Βεβαιώσου ότι είναι εδώ
import traceback # Για καλύτερο logging σφαλμάτων

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "us-central1"
MODEL_NAME = "gemini-1.5-flash-preview-04-17" # Ή όποιο μοντέλο χρησιμοποιείς
GITHUB_REPO_URL = "github.com/Ecko-the-agent/genesis.git" # Το repo σου
GITHUB_USER = "Ecko-the-agent" # Το username σου
# GITHUB_PAT_SECRET_NAME = "github-pat" # Δεν χρειάζεται πλέον
GITHUB_PAT_ENV_VAR_NAME = "GITHUB_PAT_VALUE" # Το όνομα της μεταβλητής περιβάλλοντος

CONVERSATION_COLLECTION = "conversations"
MAIN_CHAT_HISTORY_DOC = "main_chat_history"
MAX_HISTORY_LENGTH = 20 # Πόσα μηνύματα ιστορικού στέλνουμε στο LLM

# --- Initialize Clients ---
db = None
model = None
# secret_client = None # Αφαιρέθηκε

# Forcing redeploy with this comment
print("Initializing Ecko Backend...")

try:
    # Initialize Vertex AI
    vertexai.init(project=PROJECT_ID, location=REGION)
    print(f"Vertex AI initialized for project {PROJECT_ID} in {REGION}")

    # Initialize Firestore
    db = google.cloud.firestore.Client()
    print("Firestore client initialized.")

    # Load the generative model
    model = GenerativeModel(MODEL_NAME)
    print(f"Generative model {MODEL_NAME} loaded.")

    # Generation Config (Optional - can be customized)
    generation_config = GenerationConfig(
        temperature=0.7,
        max_output_tokens=1024,
    )
    # Safety settings (Optional - adjust as needed)
    # safety_settings = { ... }

except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    traceback.print_exc() # Εκτύπωσε το πλήρες σφάλμα στα logs
    # Αν η αρχικοποίηση αποτύχει, ίσως θέλουμε η function να μην μπορεί να τρέξει καθόλου
    raise RuntimeError(f"Initialization failed: {e}")

# --- Helper Functions ---

def get_github_pat():
    """Ανακτά το GitHub PAT από τη μεταβλητή περιβάλλοντος."""
    github_pat = os.environ.get(GITHUB_PAT_ENV_VAR_NAME)
    if not github_pat:
        print(f"ERROR: Environment variable '{GITHUB_PAT_ENV_VAR_NAME}' not found or empty.")
        return None
    else:
        # Μην τυπώνεις το PAT στα logs!
        print(f"Successfully retrieved GitHub PAT from environment variable '{GITHUB_PAT_ENV_VAR_NAME}'.")
        return github_pat

def get_conversation_history(doc_id=MAIN_CHAT_HISTORY_DOC, limit=MAX_HISTORY_LENGTH):
    """Ανακτά το ιστορικό της συνομιλίας από το Firestore."""
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
                # Αγνόησε κενά μηνύματα που μπορεί να έχουν γραφτεί κατά λάθος
                if msg.get('message'):
                    history.append(Content(role=role, parts=[Part.from_text(msg.get('message'))]))
                else:
                    print(f"Warning: Skipping empty message in history from sender '{msg.get('sender')}'")
            return history
        else:
            print(f"Conversation history document '{doc_id}' not found.")
            return []
    except Exception as e:
        print(f"Error getting conversation history (doc: {doc_id}): {e}")
        traceback.print_exc()
        return []

def add_to_conversation_history(message_text, sender, doc_id=MAIN_CHAT_HISTORY_DOC):
    """Προσθέτει ένα νέο μήνυμα στο ιστορικό της συνομιλίας στο Firestore."""
    if not db:
        print("Error: Firestore client not initialized in add_to_conversation_history.")
        return
    # Μην γράφεις κενά μηνύματα
    if not message_text or not message_text.strip():
        print(f"Warning: Attempted to add empty message from sender '{sender}'. Skipping.")
        return
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(pytz.utc) # Χρήση UTC timezone
        new_message = {
            "sender": sender, # 'user' or 'model'
            "message": message_text,
            "timestamp": timestamp
        }
        # Χρήση arrayUnion για ατομική προσθήκη στο τέλος του array 'messages'
        # Αν το document δεν υπάρχει, το .set(..., merge=True) θα το δημιουργήσει.
        doc_ref.set({"messages": google.cloud.firestore.ArrayUnion([new_message])}, merge=True)
        print(f"Added message from '{sender}' to history (doc: {doc_id}).")
    except Exception as e:
        print(f"Error adding to conversation history (doc: {doc_id}): {e}")
        traceback.print_exc()

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
    prompt = f"""
    You are Ecko, an AI agent capable of modifying your own source code stored in a Git repository ({GITHUB_REPO_URL}).
    The user wants to make the following change: '{instruction}'

    Analyze the request and determine:
    1. Which file(s) in the repository (e.g., 'frontend/index.html', 'backend/main.py') need modification? Use relative paths from the root of the repository.
    2. What is the exact new content for the specified section(s) or the entire file(s)? Ensure the new content is complete and correct for the file type.

    Respond ONLY with a valid JSON object containing the file path as the key and the complete new file content as the string value.
    Example for changing the H1 title in frontend/index.html:
    {{
      "frontend/index.html": "<!DOCTYPE html>\\n<html lang=\\"el\\">\\n<head>\\n    <meta charset=\\"UTF-8\\">\\n    <meta name=\\"viewport\\" content=\\"width=device-width, initial-scale=1.0\\">\\n    <title>Ecko Interface</title>\\n    <link rel=\\"stylesheet\\" href=\\"style.css\\">\\n</head>\\n<body>\\n    <h1>Ecko AI v2</h1> <!-- Changed Title -->\\n    <div id=\\"chatbox\\">\\n        <p><strong>Ecko:</strong> Γεια σου! Είμαι ο Ecko. Ρώτα με κάτι.</p>\\n    </div>\\n    <input type=\\"text\\" id=\\"userInput\\" placeholder=\\"Γράψε εδώ...\\" aria-label=\\"User input\\">\\n    <button id=\\"sendButton\\" onclick=\\"sendMessage()\\">Αποστολή</button>\\n    <div id=\\"loading\\" style=\\"display: none;\\">Περιμένετε...</div>\\n\\n    <script src=\\"ecko_script.js\\"></script>\\n</body>\\n</html>"
    }}

    If the request is unclear, too complex, involves actions other than modifying file content (like creating/deleting files, running commands), or requires modifying multiple unrelated files in one go, respond with:
    {{ "error": "The request is unclear, too complex, involves unsupported actions, or targets multiple files. Please provide a specific content change request for one file at a time." }}
    """
    try:
        # Set a timeout for the LLM call? Maybe later.
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
    github_pat = get_github_pat()
    if not github_pat:
        return "Σφάλμα: Αδυναμία ανάκτησης του GitHub token (δεν βρέθηκε η μεταβλητή περιβάλλοντος). Η τροποποίηση ακυρώθηκε."

    # --- 3. Δημιουργία Temp Dir & Clone ---
    repo_dir = None
    try:
        with tempfile.TemporaryDirectory() as repo_dir:
            print(f"Created temporary directory: {repo_dir}")
            # Δημιουργία του URL με το PAT για κλωνοποίηση
            authenticated_repo_url = f"https://{github_pat}@{GITHUB_REPO_URL}"

            print(f"Cloning repository {GITHUB_REPO_URL} into {repo_dir}...")
            # Disable terminal prompts for credentials, use PAT instead
            cloned_repo = Repo.clone_from(authenticated_repo_url, repo_dir, env={'GIT_TERMINAL_PROMPT': '0'}, progress=None)
            print("Repository cloned successfully.")

            # --- 4. Εφαρμογή Αλλαγών ---
            changed_files = []
            for file_path, new_content in modification_plan.items():
                if ".." in file_path or file_path.startswith("/"):
                     print(f"WARNING: Skipping potentially unsafe file path from LLM: {file_path}")
                     continue

                # Ensure the path uses OS-specific separators (just in case)
                normalized_file_path = os.path.join(*file_path.split('/'))
                target_file = os.path.join(cloned_repo.working_tree_dir, normalized_file_path)

                print(f"Applying changes to: {target_file}")
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                with open(target_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                changed_files.append(file_path) # Keep original format for reporting

            if not changed_files:
                return "Δεν πραγματοποιήθηκαν αλλαγές (ίσως λόγω μη έγκυρων paths από το AI)."

            # --- 5. Commit & Push ---
            if cloned_repo.is_dirty(untracked_files=True):
                print("Changes detected. Staging, committing, and pushing...")
                committer = Actor("Ecko Agent (via GCF)", f"{GITHUB_USER}+gcf@users.noreply.github.com") # Use noreply email

                # Stage modified files only first to avoid adding unrelated untracked files
                cloned_repo.git.add(update=True)
                # Explicitly stage the files we intended to change (handles new files too)
                for file_path in changed_files:
                    normalized_file_path = os.path.join(*file_path.split('/'))
                    full_path = os.path.join(cloned_repo.working_tree_dir, normalized_file_path)
                    if os.path.exists(full_path): # Check if file exists before adding
                         cloned_repo.git.add(full_path)
                    else:
                         print(f"Warning: File {full_path} intended for modification not found after write.")


                commit_message = f"Automated code modification by Ecko: {instruction}"
                if len(commit_message) > 72:
                    commit_message = commit_message[:69] + "..."

                # Check if there are changes staged before committing
                if cloned_repo.index.diff("HEAD"):
                    cloned_repo.index.commit(commit_message, author=committer, committer=committer)
                    print("Changes committed locally.")

                    origin = cloned_repo.remote(name='origin')
                    print(f"Pushing to remote branch 'main': {origin.url}")
                    # Explicitly push the current branch to the 'main' branch on origin
                    push_info = origin.push(refspec='HEAD:main')
                    print("Changes pushed to origin.")

                    # More detailed error checking for push
                    push_failed = False
                    for info in push_info:
                        if info.flags & (info.ERROR | info.REJECTED | info.REMOTE_REJECTED | info.REMOTE_FAILURE):
                            print(f"ERROR/REJECTION during push: Flags={info.flags}, Summary={info.summary}")
                            push_failed = True
                    if push_failed:
                        # Try to provide more context if possible
                        return f"Σφάλμα: Οι αλλαγές έγιναν commit, αλλά απέτυχε το push στο GitHub. Ελέγξτε τα logs της Cloud Function για λεπτομέρειες."

                    return f"Επιτυχής τροποποίηση! Οι αλλαγές ({', '.join(changed_files)}) στάλθηκαν στο GitHub και θα εφαρμοστούν σύντομα."
                else:
                     print("No changes staged for commit.")
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

    # --- CORS Preflight Handling ---
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*', # Be more specific in production
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    # --- CORS Headers for Actual Response ---
    cors_headers = {
        'Access-Control-Allow-Origin': '*' # Be more specific in production
    }

    # --- Check for Initialization Errors ---
    if not db or not model:
         print("ERROR: Initialization failed (db or model). Function cannot proceed.")
         # Ensure valid JSON response for errors
         return (json.dumps({"error": "Internal server error during initialization."}), 500, cors_headers)

    # --- Handle POST Request ---
    if request.method == 'POST':
        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                print("Error: Missing or invalid JSON body or 'message' field.")
                return (json.dumps({"error": "Invalid request body. 'message' field is required."}), 400, cors_headers)

            user_message = request_json['message'].strip()
            print(f"Received message: {user_message}")

            ecko_response = "Συνέβη ένα απρόσμενο σφάλμα κατά την επεξεργασία." # Default error response

            # --- Logic Processing ---
            modification_trigger = "Ecko, modify code: "
            if user_message.lower().startswith(modification_trigger.lower()):
                instruction = user_message[len(modification_trigger):].strip()
                if instruction:
                    print(f"Modification instruction received: '{instruction}'")
                    # Record user command *before* execution attempt
                    add_to_conversation_history(user_message, 'user')
                    ecko_response = execute_code_modification(instruction)
                    # Record Ecko's response (success or failure message)
                    add_to_conversation_history(ecko_response, 'model')
                else:
                    ecko_response = "Παρακαλώ δώσε μια συγκεκριμένη οδηγία τροποποίησης μετά τη φράση-κλειδί."
                    # Optionally log this, but don't add to persistent history
                    print("Received empty modification instruction.")
            else:
                # Normal conversation flow
                # Record user message
                add_to_conversation_history(user_message, 'user')

                # Retrieve history
                conversation_history = get_conversation_history()
                print(f"Retrieved history with {len(conversation_history)} messages.")

                # Call LLM
                try:
                    chat = model.start_chat(history=conversation_history)
                    llm_api_response = chat.send_message(
                        Part.from_text(user_message),
                        generation_config=generation_config,
                        # safety_settings=safety_settings # If defined
                    )
                    ecko_response = llm_api_response.text
                    print("LLM response received successfully.")
                    # Record Ecko's response
                    add_to_conversation_history(ecko_response, 'model')
                except Exception as llm_error:
                    print(f"Error during LLM communication: {llm_error}")
                    traceback.print_exc()
                    ecko_response = "Συγγνώμη, αντιμετώπισα ένα πρόβλημα κατά την προσπάθεια να σου απαντήσω."
                    # Optionally add this error message to history as 'model' response?
                    # add_to_conversation_history(ecko_response, 'model')


            print(f"Sending response: {ecko_response}")
            # Ensure the response is always valid JSON
            return (json.dumps({"response": ecko_response}), 200, cors_headers)

        except Exception as e:
            print(f"Critical error processing POST request: {e}")
            traceback.print_exc()
            # Return a generic error in valid JSON format
            return (json.dumps({"error": "An internal server error occurred."}), 500, cors_headers)

    # --- Handle other methods (GET, etc.) ---
    else:
        print(f"Method not allowed: {request.method}")
        return (json.dumps({"error": "Method Not Allowed"}), 405, cors_headers)