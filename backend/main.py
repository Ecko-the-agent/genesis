import functions_framework
import json
import os
import datetime
from google.cloud import firestore # <-- Προστέθηκε ξανά
import google.generativeai as genai # <-- Προστέθηκε ξανά

print("--- Python script starting (No Flask - Full Logic - Top Level) ---")

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
CONVERSATION_COLLECTION = 'conversations'
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10
ALLOWED_ORIGIN = "https://ecko-the-agent.github.io" # Το Origin του Frontend

# Headers για τις απαντήσεις CORS
CORS_HEADERS = {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '3600'
}

# --- Initialize Clients ---
llm_model = None
db = None
initialization_error = None

try:
    print("Attempting to initialize Firestore client...")
    db = firestore.Client(project=PROJECT_ID)
    print("Firestore client initialized successfully.")

    print("Attempting to configure Gemini API Key...")
    gemini_api_key = os.environ.get("GEMINI_API_KEY") # Παίρνουμε το κλειδί από το env var
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        llm_model = genai.GenerativeModel('gemini-1.5-flash')
        print("Gemini API Key configured successfully via environment variable.")
    else:
        print("WARNING: GEMINI_API_KEY not found in environment variables. LLM will not be available.")
        initialization_error = "LLM API Key not configured." # Θέτουμε σφάλμα αν λείπει το κλειδί

except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    initialization_error = str(e)
    if not db: print("Firestore client failed to initialize.")
    if not llm_model: print("LLM failed to initialize.")

# --- Firestore Helper Functions --- (Ίδιες με πριν)
def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    if not db:
        print("Firestore client not available in get_conversation_history.")
        return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            history = doc.to_dict().get('messages', [])
            print(f"Retrieved {len(history)} messages from Firestore doc: {doc_id}")
            return history[-limit:] # Επιστροφή των τελευταίων 'limit'
        else:
            print(f"Firestore document {doc_id} not found.")
            return []
    except Exception as e:
        print(f"Error getting conversation history from Firestore: {e}")
        return []

def add_to_conversation_history(doc_id, user_msg, ecko_msg):
    if not db:
        print("Firestore client not available in add_to_conversation_history.")
        return False
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        new_messages = [
            {"sender": "user", "message": user_msg, "timestamp": timestamp},
            {"sender": "ecko", "message": ecko_msg, "timestamp": timestamp}
        ]
        # Χρήση merge=True για να μην αντικαταστήσουμε όλο το doc αν υπάρχει ήδη
        doc_ref.set(
            {'messages': firestore.ArrayUnion(new_messages)},
            merge=True
        )
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    """
    Χειρίζεται HTTP requests απευθείας, χωρίς Flask.
    Περιλαμβάνει χειρισμό CORS και τη λογική του Ecko.
    """
    print(f"--- GCF Entry Point Triggered (ecko_main), Method: {request.method}, Path: {request.path} ---")

    # --- Έλεγχος για σφάλμα αρχικοποίησης νωρίς ---
    if initialization_error:
        print(f"!!! Initialization Error detected: {initialization_error}")
        error_response = json.dumps({"error": f"Server initialization failed: {initialization_error}"})
        # Στέλνουμε 500 Internal Server Error με CORS headers
        error_headers = CORS_HEADERS.copy()
        error_headers['Content-Type'] = 'application/json'
        return (error_response, 500, error_headers)

    # --- Χειρισμός CORS Preflight (OPTIONS) ---
    if request.method == 'OPTIONS':
        print("--- Handling OPTIONS request ---")
        return ('', 204, CORS_HEADERS)

    # --- Χειρισμός POST Requests ---
    if request.method == 'POST':
        print("--- Handling POST request ---")
        response_headers = {'Access-Control-Allow-Origin': CORS_HEADERS['Access-Control-Allow-Origin']} # Βάζουμε πάντα το βασικό CORS header

        ecko_response_text = "Παρουσιάστηκε ένα σφάλμα κατά την επεξεργασία." # Default error message
        user_message = ""

        try:
            request_json = request.get_json(silent=True)
            print(f"--- Received JSON data: {request_json} ---")

            if not request_json or 'message' not in request_json:
                print("--- Error: Missing 'message' in JSON body ---")
                error_response = json.dumps({"error": "Missing 'message' in request body"})
                response_headers['Content-Type'] = 'application/json'
                return (error_response, 400, response_headers)

            user_message = request_json['message']
            print(f"--- User message: {user_message} ---")

            # --- Ecko Logic (Firestore & Gemini) ---
            if llm_model and db:
                print("LLM and DB OK. Getting history...")
                history = get_conversation_history(MAIN_CONVERSATION_DOC_ID)

                prompt_parts = ["You are Ecko, a helpful AI assistant. Here is the recent conversation history:"]
                for entry in history:
                    sender_prefix = "User" if entry.get('sender') == 'user' else "Ecko"
                    prompt_parts.append(f"{sender_prefix}: {entry.get('message', '')}")
                prompt_parts.append(f"User: {user_message}")
                prompt_parts.append("Ecko:")
                full_prompt = "\n".join(prompt_parts)
                print(f"--- Sending Prompt to LLM (length: {len(full_prompt)}) ---")

                try:
                    # Κλήση στο Gemini
                    response = llm_model.generate_content(full_prompt)

                    # Έλεγχος για block reason
                    if not response.parts:
                         block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                         print(f"LLM response blocked. Reason: {block_reason}")
                         ecko_response_text = f"[AI response blocked: {block_reason}]"
                    else:
                         ecko_response_text = response.text
                    print(f"LLM Raw Response: {ecko_response_text}")

                    # Αποθήκευση στο Firestore ΜΟΝΟ αν έχουμε μήνυμα χρήστη και απάντηση AI
                    if user_message and ecko_response_text and not ecko_response_text.startswith("[AI response blocked"):
                       print("Attempting to save conversation to Firestore...")
                       add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response_text)
                       if not add_success:
                           print("Warning: Failed to save conversation to Firestore.")

                except Exception as llm_error:
                    print(f"LLM generation error: {llm_error}")
                    ecko_response_text = "Συνέβη ένα σφάλμα κατά την επικοινωνία με το AI model."

            elif not llm_model:
                 print("LLM not configured, returning basic response.")
                 ecko_response_text = f"Ecko received: '{user_message}'. However, the LLM is not configured correctly (check API Key/Initialization)."
            else: # not db
                 print("Firestore DB not configured, returning basic response.")
                 ecko_response_text = f"Ecko received: '{user_message}'. However, the Database is not configured correctly (check Initialization)."
            # -------------------------------------

            print(f"--- Sending final response (200 OK): {ecko_response_text} ---")
            response_data = json.dumps({"response": ecko_response_text})
            response_headers['Content-Type'] = 'application/json'
            return (response_data, 200, response_headers)

        except Exception as e:
            print(f"--- ERROR processing POST request: {e} ---")
            # Επιστρέφουμε σφάλμα 500
            error_response = json.dumps({"error": f"An internal error occurred during POST processing: {e}"})
            response_headers['Content-Type'] = 'application/json'
            return (error_response, 500, response_headers)

    # --- Άλλες μέθοδοι ---
    else:
        print(f"--- Method Not Allowed: {request.method} ---")
        # Επιστρέφουμε 405 με CORS headers
        return ('Method Not Allowed', 405, CORS_HEADERS)

print("--- Python script finished loading (No Flask - Full Logic - Bottom Level) ---")


