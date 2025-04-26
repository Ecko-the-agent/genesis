import functions_framework
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig
from vertexai.preview.generative_models import Tool
import google.cloud.secretmanager

import os
import datetime
import pytz
import json
import tempfile
import shutil
from git import Repo, Actor

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "us-central1"  # Η περιοχή που τρέχει η Function και υποστηρίζει το μοντέλο
MODEL_NAME = "gemini-1.5-flash-preview-04-17" # Το μοντέλο που χρησιμοποιούμε
# --- Νέες Σταθερές ---
GITHUB_REPO_URL = "github.com/Ecko-the-agent/genesis.git" # Χωρίς https:// μπροστά αρχικά
GITHUB_USER = "Ecko-the-agent"
GITHUB_PAT_SECRET_NAME = "github-pat" # Το όνομα του secret με το PAT
CONVERSATION_COLLECTION = "conversations"
MAIN_CHAT_HISTORY_DOC = "main_chat_history" # Έγγραφο για την κύρια συνομιλία
MAX_HISTORY_LENGTH = 20 # Μέγιστος αριθμός μηνυμάτων ιστορικού που στέλνουμε στο LLM

# --- Initialize Clients ---
try:
    # Initialize Vertex AI
    vertexai.init(project=PROJECT_ID, location=REGION)
    print(f"Vertex AI initialized for project {PROJECT_ID} in {REGION}")

    # Initialize Firestore
    db = google.cloud.firestore.Client()
    print("Firestore client initialized.")

    # Initialize Secret Manager client
    secret_client = google.cloud.secretmanager.SecretManagerServiceClient()
    print("Secret Manager client initialized.")

    # Load the generative model
    model = GenerativeModel(MODEL_NAME)
    print(f"Generative model {MODEL_NAME} loaded.")

    # Generation Config (Optional - can be customized)
    generation_config = GenerationConfig(
        temperature=0.7, # Adjust creativity vs factualness
        # top_p=0.9,
        # top_k=40,
        max_output_tokens=1024, # Limit response length
    )
    # Safety settings (Optional - adjust as needed)
    # safety_settings = { ... }

except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    # Αν η αρχικοποίηση αποτύχει, ίσως θέλουμε η function να μην μπορεί να τρέξει καθόλου
    db = None
    model = None
    secret_client = None
    # Raise an exception or handle appropriately to prevent function execution
    raise RuntimeError(f"Initialization failed: {e}")

# --- Helper Functions ---

def get_secret(secret_name):
    """Ανακτά την τελευταία έκδοση ενός secret από το Secret Manager."""
    if not secret_client:
        print("ERROR: Secret Manager client not initialized.")
        return None
    try:
        secret_version_name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
        response = secret_client.access_secret_version(request={"name": secret_version_name})
        secret_value = response.payload.data.decode("UTF-8")
        print(f"Successfully retrieved secret: {secret_name}")
        return secret_value
    except Exception as e:
        print(f"ERROR retrieving secret {secret_name}: {e}")
        return None

def get_conversation_history(doc_id=MAIN_CHAT_HISTORY_DOC, limit=MAX_HISTORY_LENGTH):
    """Ανακτά το ιστορικό της συνομιλίας από το Firestore."""
    if not db: return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            # Παίρνει τα τελευταία 'limit' μηνύματα
            messages = doc.to_dict().get("messages", [])[-limit:]
            # Μετατροπή σε Content objects για το Vertex AI SDK
            history = []
            for msg in messages:
                 # Χαρτογράφηση 'sender' σε 'role'
                role = 'user' if msg.get('sender', '').lower() == 'user' else 'model'
                history.append(Content(role=role, parts=[Part.from_text(msg.get('message', ''))]))
            return history
        else:
            return []
    except Exception as e:
        print(f"Error getting conversation history (doc: {doc_id}): {e}")
        return []

def add_to_conversation_history(message_text, sender, doc_id=MAIN_CHAT_HISTORY_DOC):
    """Προσθέτει ένα νέο μήνυμα στο ιστορικό της συνομιλίας στο Firestore."""
    if not db: return
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(pytz.utc) # Χρήση UTC timezone
        new_message = {
            "sender": sender, # 'user' or 'model'
            "message": message_text,
            "timestamp": timestamp
        }
        # Χρήση arrayUnion για ατομική προσθήκη στο τέλος του array 'messages'
        doc_ref.set({"messages": google.cloud.firestore.ArrayUnion([new_message])}, merge=True)
    except Exception as e:
        print(f"Error adding to conversation history (doc: {doc_id}): {e}")

def execute_code_modification(instruction):
    """
    Εκτελεί την τροποποίηση κώδικα:
    1. Παίρνει οδηγίες από το LLM.
    2. Κλωνοποιεί το repo.
    3. Εφαρμόζει αλλαγές.
    4. Κάνει Commit & Push.
    """
    if not model:
        return "Σφάλμα: Το LLM δεν είναι διαθέσιμο για να επεξεργαστεί την τροποποίηση."

    print(f"Attempting code modification based on: '{instruction}'")

    # --- 1. LLM για παραγωγή αλλαγών ---
    # TODO: Βελτίωση του prompt για να ζητάει συγκεκριμένες αλλαγές αρχείων
    # Προς το παρόν, ζητάμε μια περιγραφή του τι πρέπει να γίνει.
    # Το ιδανικό θα ήταν να ζητήσει αλλαγές σε μορφή diff ή τα νέα περιεχόμενα αρχείων.
    prompt = f"""
    You are Ecko, an AI agent capable of modifying your own source code stored in a Git repository.
    The user wants to make the following change: '{instruction}'

    Analyze the request and determine:
    1. Which file(s) in the repository (e.g., 'frontend/index.html', 'backend/main.py') need modification?
    2. What is the exact new content for the specified section(s) or the entire file(s)?

    Respond ONLY with a JSON object containing the file path as the key and the complete new file content as the value.
    Example for changing the H1 title:
    {{
      "frontend/index.html": "<!DOCTYPE html>\n<html lang=\"el\">\n<head>\n    <meta charset=\"UTF-8\">\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n    <title>Ecko Interface</title>\n    <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n    <h1>Ecko AI v2</h1> <!-- Changed Title -->\n    <div id=\"chatbox\">\n        <p><strong>Ecko:</strong> Γεια σου! Είμαι ο Ecko. Ρώτα με κάτι.</p>\n    </div>\n    <input type=\"text\" id=\"userInput\" placeholder=\"Γράψε εδώ...\" aria-label=\"User input\">\n    <button id=\"sendButton\" onclick=\"sendMessage()\">Αποστολή</button>\n    <div id=\"loading\" style=\"display: none;\">Περιμένετε...</div>\n\n    <script src=\"ecko_script.js\"></script>\n</body>\n</html>"
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
        return f"Σφάλμα: Το AI δεν μπόρεσε να επεξεργαστεί το αίτημα τροποποίησης ({e})."

    # --- 2. Λήψη GitHub PAT ---
    github_pat = get_secret(GITHUB_PAT_SECRET_NAME)
    if not github_pat:
        return "Σφάλμα: Αδυναμία ανάκτησης του GitHub token. Η τροποποίηση ακυρώθηκε."

    # --- 3. Δημιουργία Temp Dir & Clone ---
    repo_dir = None # Αρχικοποίηση για το finally block
    try:
        # Χρήση context manager για αυτόματο καθαρισμό
        with tempfile.TemporaryDirectory() as repo_dir:
            print(f"Created temporary directory: {repo_dir}")
            # Δημιουργία του URL με το PAT για κλωνοποίηση
            authenticated_repo_url = f"https://{github_pat}@{GITHUB_REPO_URL}"

            print(f"Cloning repository {GITHUB_REPO_URL} into {repo_dir}...")
            cloned_repo = Repo.clone_from(authenticated_repo_url, repo_dir)
            print("Repository cloned successfully.")

            # --- 4. Εφαρμογή Αλλαγών ---
            changed_files = []
            for file_path, new_content in modification_plan.items():
                # Διασφάλιση ότι το path είναι σχετικό και ασφαλές
                # (Αποφυγή ../ κλπ - αν και το LLM δεν θα έπρεπε να τα παράγει)
                if ".." in file_path or file_path.startswith("/"):
                     print(f"WARNING: Skipping potentially unsafe file path from LLM: {file_path}")
                     continue

                target_file = os.path.join(cloned_repo.working_tree_dir, file_path)
                print(f"Applying changes to: {target_file}")

                # Διασφάλιση ότι ο φάκελος υπάρχει
                os.makedirs(os.path.dirname(target_file), exist_ok=True)

                # Γράψιμο του νέου περιεχομένου
                with open(target_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                # Προσθήκη στη λίστα για το commit
                changed_files.append(file_path)

            if not changed_files:
                return "Δεν πραγματοποιήθηκαν αλλαγές (ίσως λόγω μη έγκυρων paths από το AI)."

            # --- 5. Commit & Push ---
            if cloned_repo.is_dirty(untracked_files=True): # Έλεγχος αν υπάρχουν αλλαγές
                print("Changes detected. Staging, committing, and pushing...")

                # Ρύθμιση συγγραφέα commit
                committer = Actor("Ecko Agent (via GCF)", "ecko.the.agent+gcf@gmail.com") # Χρησιμοποίησε ένα διακριτό email
                # Stage τις αλλαγές (συγκεκριμένα αρχεία ή όλα)
                cloned_repo.git.add(update=True) # Stage modified files
                # Stage newly created files (αν το LLM δημιούργησε νέα)
                for file in changed_files:
                     cloned_repo.git.add(os.path.join(cloned_repo.working_tree_dir, file))

                # Δημιουργία μηνύματος commit
                commit_message = f"Automated code modification by Ecko: {instruction}"
                if len(commit_message) > 72: # Κόψε το μήνυμα αν είναι πολύ μεγάλο
                    commit_message = commit_message[:69] + "..."

                cloned_repo.index.commit(commit_message, author=committer, committer=committer)
                print("Changes committed locally.")

                # Push στον origin (main branch by default)
                origin = cloned_repo.remote(name='origin')
                push_info = origin.push()
                print("Changes pushed to origin.")

                # Έλεγχος για σφάλματα κατά το push (προαιρετικά)
                for info in push_info:
                    if info.flags & info.ERROR:
                        print(f"ERROR during push: {info.summary}")
                        # Αν υπάρξει σφάλμα στο push, ίσως θέλουμε να κάνουμε rollback; (πιο πολύπλοκο)
                        return "Σφάλμα: Οι αλλαγές έγιναν commit τοπικά, αλλά απέτυχε το push στο GitHub."
                    elif info.flags & info.REJECTED:
                         print(f"Push rejected: {info.summary}")
                         return "Σφάλμα: Το push απορρίφθηκε από το GitHub (ίσως χρειάζεται pull;)."


                return f"Επιτυχής τροποποίηση! Οι αλλαγές ({', '.join(changed_files)}) στάλθηκαν στο GitHub και θα εφαρμοστούν σύντομα."

            else:
                print("No changes detected after applying LLM plan.")
                return "Δεν εντοπίστηκαν αλλαγές για αποστολή στο GitHub."

    except Exception as e:
        print(f"ERROR during code modification execution: {e}")
        # Κάνε traceback για debugging στα logs
        import traceback
        traceback.print_exc()
        return f"Κρίσιμο σφάλμα κατά την προσπάθεια τροποποίησης: {e}"
    # finally:
        # Ο context manager του TemporaryDirectory χειρίζεται τον καθαρισμό
        # if repo_dir and os.path.exists(repo_dir):
        #     print(f"Cleaning up temporary directory: {repo_dir}")
        #     shutil.rmtree(repo_dir) # Σβήνει τον φάκελο και τα περιεχόμενά του


# --- Main HTTP Cloud Function ---

@functions_framework.http
def ecko_main(request):
    """HTTP Cloud Function entry point."""

    # --- CORS Preflight Handling ---
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
    if not db or not model or not secret_client:
         print("ERROR: Initialization failed. Function cannot proceed.")
         return (json.dumps({"error": "Internal server error during initialization."}), 500, cors_headers)


    # --- Handle POST Request ---
    if request.method == 'POST':
        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                return (json.dumps({"error": "Missing 'message' in request body"}), 400, cors_headers)

            user_message = request_json['message'].strip()
            print(f"Received message: {user_message}")

            ecko_response = "Συνέβη ένα απρόσμενο σφάλμα." # Default response

            # --- Λογική Επεξεργασίας ---
            # 1. Έλεγχος για εντολή τροποποίησης
            modification_trigger = "Ecko, modify code: " # Η φράση-κλειδί
            if user_message.lower().startswith(modification_trigger.lower()):
                instruction = user_message[len(modification_trigger):].strip()
                if instruction:
                    # Καταγραφή της εντολής χρήστη (όχι της απάντησης modification) στο ιστορικό
                    add_to_conversation_history(user_message, 'user')
                    # Εκτέλεση της τροποποίησης
                    ecko_response = execute_code_modification(instruction)
                    # Καταγραφή της απάντησης του Ecko (αποτέλεσμα τροποποίησης) στο ιστορικό
                    add_to_conversation_history(ecko_response, 'model')
                else:
                    ecko_response = "Παρακαλώ δώσε μια συγκεκριμένη οδηγία τροποποίησης μετά τη φράση-κλειδί."
                    # Δεν καταγράφουμε την κενή εντολή
            else:
                # 2. Κανονική συνομιλία
                # Καταγραφή μηνύματος χρήστη
                add_to_conversation_history(user_message, 'user')

                # Ανάκτηση ιστορικού
                conversation_history = get_conversation_history()

                # Κλήση στο LLM (μέσω chat interface για context)
                chat = model.start_chat(history=conversation_history)
                llm_api_response = chat.send_message(
                    Part.from_text(user_message), # Το τελευταίο μήνυμα του χρήστη
                    generation_config=generation_config,
                    # safety_settings=safety_settings # Αν τα έχεις ορίσει
                )
                ecko_response = llm_api_response.text

                # Καταγραφή απάντησης Ecko
                add_to_conversation_history(ecko_response, 'model')


            print(f"Sending response: {ecko_response}")
            # Χρήση json.dumps για να διασφαλιστεί σωστό JSON format
            return (json.dumps({"response": ecko_response}), 200, cors_headers)

        except Exception as e:
            print(f"Error processing POST request: {e}")
            import traceback
            traceback.print_exc()
            # Επιστροφή γενικού σφάλματος στον client
            return (json.dumps({"error": "An internal server error occurred."}), 500, cors_headers)

    # --- Handle other methods (GET, etc.) ---
    else:
        print(f"Method not allowed: {request.method}")
        return (json.dumps({"error": "Method Not Allowed"}), 405, cors_headers)