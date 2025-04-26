import functions_framework
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS # Αφήνουμε μόνο το CORS εδώ
import google.generativeai as genai
import os
from google.cloud import firestore
import datetime

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
CONVERSATION_COLLECTION = 'conversations'
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10
# Το Origin του Frontend μας στο GitHub Pages
ALLOWED_ORIGIN = "https://ecko-the-agent.github.io"

# --- Initialize Clients ---
# Μεταφέρουμε την αρχικοποίηση μέσα σε try/except για καλύτερο εντοπισμό σφαλμάτων
llm_model = None
db = None
initialization_error = None

try:
    print("Attempting to initialize Firestore client...")
    db = firestore.Client(project=PROJECT_ID)
    print("Firestore client initialized successfully.")

    print("Attempting to configure Gemini API Key...")
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        llm_model = genai.GenerativeModel('gemini-1.5-flash')
        print("Gemini API Key configured successfully via environment variable.")
    else:
        print("GEMINI_API_KEY not found in environment variables. LLM will not be available.")
        # Θα μπορούσαμε να θέσουμε ένα σφάλμα εδώ αν το LLM είναι κρίσιμο
        # initialization_error = "LLM API Key not configured."

except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    initialization_error = str(e) # Αποθήκευση του σφάλματος
    if not db: print("Firestore client failed to initialize.")
    if not llm_model: print("LLM failed to initialize.")


# --- Flask App ---
app = Flask(__name__)
# Ρητή διαμόρφωση CORS:
# Επιτρέπουμε ΜΟΝΟ το origin του GitHub Pages μας και συγκεκριμένες μεθόδους/κεφαλίδες.
CORS(app, resources={r"/ecko": {"origins": ALLOWED_ORIGIN}}, supports_credentials=False)
# Το supports_credentials=False είναι συνήθως ασφαλέστερο αν δεν χρειάζεσαι cookies/auth headers.
# Το resources={r"/ecko": ...} εφαρμόζει το CORS μόνο στο route /ecko.

# --- Firestore Helper Functions --- (Παραμένουν ίδιες)
def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    # ... (ίδιο με πριν) ...
    if not db:
        print("Firestore client not available in get_conversation_history.")
        return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            history = doc.to_dict().get('messages', [])
            return history[-limit:]
        else:
            return []
    except Exception as e:
        print(f"Error getting conversation history from Firestore: {e}")
        return []


def add_to_conversation_history(doc_id, user_msg, ecko_msg):
     # ... (ίδιο με πριν) ...
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
        doc_ref.set(
            {'messages': firestore.ArrayUnion(new_messages)},
            merge=True
        )
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

@app.route('/ecko', methods=['POST', 'OPTIONS'])
# Δεν χρειαζόμαστε @cross_origin() εδώ αφού ρυθμίσαμε το CORS στην αρχικοποίηση
def handle_ecko_request():
    print(f"--- Request received for /ecko, Method: {request.method} ---")

    # Έλεγχος αν η αρχικοποίηση απέτυχε
    if initialization_error:
        print(f"Returning error due to initialization failure: {initialization_error}")
        # Στέλνουμε 500 Internal Server Error
        return make_response(jsonify({"error": f"Server initialization failed: {initialization_error}"}), 500)

    # Το OPTIONS request χειρίζεται αυτόματα από το Flask-Cors τώρα με τη νέα ρύθμιση
    if request.method == 'POST':
        ecko_response = "Παρουσιάστηκε ένα σφάλμα."
        user_message = ""

        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                print("Error: Missing 'message' in request body")
                return make_response(jsonify({"error": "Missing 'message' in request body"}), 400)

            user_message = request_json['message']
            print(f"Received user message: {user_message}")

            if llm_model and db: # Έλεγχος ότι και το LLM και το DB είναι ΟΚ
                print("LLM and DB seem OK. Proceeding with logic...")
                history = get_conversation_history(MAIN_CONVERSATION_DOC_ID)
                print(f"Retrieved history (last {len(history)}): {history}")

                prompt_parts = ["You are Ecko, a helpful AI assistant. Here is the recent conversation history:"]
                for entry in history:
                    sender_prefix = "User" if entry.get('sender') == 'user' else "Ecko"
                    prompt_parts.append(f"{sender_prefix}: {entry.get('message', '')}")
                prompt_parts.append(f"User: {user_message}")
                prompt_parts.append("Ecko:")
                full_prompt = "\n".join(prompt_parts)
                print(f"--- Sending Prompt to LLM (length: {len(full_prompt)}) ---") # Log prompt length

                try:
                    response = llm_model.generate_content(full_prompt)
                    # Έλεγχος για πιθανό άδειο response ή block reason
                    if not response.parts:
                         # Gemini μπορεί να μπλοκάρει περιεχόμενο
                         block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                         print(f"LLM response blocked. Reason: {block_reason}")
                         ecko_response = f"[AI response blocked: {block_reason}]"
                    else:
                         ecko_response = response.text
                    print(f"LLM Raw Response: {ecko_response}")


                    if user_message and ecko_response:
                       print("Attempting to save conversation to Firestore...")
                       add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response)
                       if not add_success:
                           print("Warning: Failed to save conversation to Firestore.")

                except Exception as llm_error:
                    print(f"LLM generation error: {llm_error}")
                    ecko_response = "Συνέβη ένα σφάλμα κατά την επικοινωνία με το AI model."

            elif not llm_model:
                 print("LLM not configured, returning basic response.")
                 ecko_response = f"Ecko received: '{user_message}'. However, the LLM is not configured correctly."
            else: # not db
                 print("Firestore DB not configured, returning basic response.")
                 ecko_response = f"Ecko received: '{user_message}'. However, the Database is not configured correctly."


            print(f"Sending final response (200 OK): {ecko_response}")
            return make_response(jsonify({"response": ecko_response}), 200)

        except Exception as e:
            print(f"Error processing POST request: {e}")
            # Δεν προσπαθούμε να γράψουμε στο DB εδώ αν υπάρχει ήδη σφάλμα
            return make_response(jsonify({"error": f"An internal error occurred during POST processing: {e}"}), 500)
    else:
        # Αυτό δεν θα έπρεπε να συμβεί αφού το OPTIONS χειρίζεται από το CORS
        print(f"Received unexpected method: {request.method}")
        return make_response(jsonify({"error": "Method not allowed"}), 405)

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    # ΑΜΕΣΟ LOGGING ΜΟΛΙΣ ΚΛΗΘΕΙ Η FUNCTION
    print("--- GCF Entry Point Triggered ---")
    # Έλεγχος αν υπάρχει ήδη σφάλμα αρχικοποίησης
    if initialization_error:
         print(f"!!! Initialization Error detected early: {initialization_error}")
         # Δεν μπορούμε να επιστρέψουμε Flask response εδώ, αλλά το log είναι σημαντικό.
         # Η κλήση της app παρακάτω θα πιάσει το σφάλμα.

    # Δρομολόγηση του request στην Flask app
    # Το Flask και το Flask-Cors θα χειριστούν τα υπόλοιπα, συμπεριλαμβανομένων των CORS headers
    return app(request.environ, lambda status, headers: None)


# --- Local testing (optional) ---
# if __name__ == '__main__':
#     print("Running Flask app locally for testing with explicit CORS origin...")
#     # Το Flask-Cors θα λειτουργήσει και τοπικά με τις ρυθμίσεις που κάναμε
#     app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))