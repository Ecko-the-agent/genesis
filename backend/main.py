import functions_framework
from flask import Flask, request, jsonify, make_response
import google.generativeai as genai
import os
from google.cloud import firestore # <-- Νέα εισαγωγή
import datetime # <-- Νέα εισαγωγή

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
CONVERSATION_COLLECTION = 'conversations' # Όνομα collection στο Firestore
# Για απλότητα, χρησιμοποιούμε ένα σταθερό document ID για όλη τη συζήτηση
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10 # Πόσα τελευταία μηνύματα να στέλνουμε στο LLM

# --- Initialize Clients ---
llm_model = None
db = None # <-- Firestore client

try:
    # Initialize Firestore Client
    db = firestore.Client(project=PROJECT_ID) # <-- Αρχικοποίηση Firestore
    print("Firestore client initialized successfully.")

    # Initialize Gemini (from environment variable)
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        llm_model = genai.GenerativeModel('gemini-1.5-flash')
        print("Gemini API Key configured successfully via environment variable.")
    else:
        print("GEMINI_API_KEY not found in environment variables. LLM will not be available.")
except Exception as e:
    print(f"Error during initialization: {e}")
    if not db: print("Firestore client failed to initialize.")
    if not llm_model: print("LLM failed to initialize.")

# --- Firestore Helper Functions ---

def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    """Ανακτά τα τελευταία μηνύματα από το Firestore."""
    if not db:
        print("Firestore client not available.")
        return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            # Η ιστορία αποθηκεύεται σε ένα πεδίο 'messages' που είναι array
            history = doc.to_dict().get('messages', [])
            # Επιστρέφει τα τελευταία 'limit' μηνύματα
            return history[-limit:]
        else:
            return []
    except Exception as e:
        print(f"Error getting conversation history from Firestore: {e}")
        return []

def add_to_conversation_history(doc_id, user_msg, ecko_msg):
    """Προσθέτει το νέο ζεύγος μηνυμάτων στο Firestore."""
    if not db:
        print("Firestore client not available.")
        return False
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc)

        new_messages = [
            {"sender": "user", "message": user_msg, "timestamp": timestamp},
            {"sender": "ecko", "message": ecko_msg, "timestamp": timestamp}
        ]

        # Χρησιμοποιούμε array_union για να προσθέσουμε τα νέα μηνύματα
        # Η set() διασφαλίζει ότι δεν θα υπάρχει το document αν δεν υπάρχει ήδη
        doc_ref.set(
            {'messages': firestore.ArrayUnion(new_messages)},
            merge=True # Χρησιμοποίησε merge=True για να μην αντικαταστήσεις άλλα πεδία αν υπάρχουν
        )
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

# --- Flask App (for GCF HTTP Trigger) ---
app = Flask(__name__)

def _build_cors_preflight_response():
    # ... (ίδιο με πριν)
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

def _corsify_actual_response(response):
    # ... (ίδιο με πριν)
    response.headers.add("Access-Control-Allow-Origin", "*")
    return response

@app.route('/ecko', methods=['POST', 'OPTIONS'])
def handle_ecko_request():
    if request.method == 'OPTIONS':
        return _build_cors_preflight_response()

    if request.method == 'POST':
        ecko_response = "Παρουσιάστηκε ένα σφάλμα." # Default error message
        user_message = "" # Initialize user_message

        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                return _corsify_actual_response(make_response(jsonify({"error": "Missing 'message' in request body"}), 400))

            user_message = request_json['message']
            print(f"Received message: {user_message}")

            if llm_model:
                # 1. Ανάκτηση Ιστορικού
                history = get_conversation_history(MAIN_CONVERSATION_DOC_ID)

                # 2. Δημιουργία Prompt με Ιστορικό
                prompt_parts = ["You are Ecko, a helpful AI assistant. Here is the recent conversation history:"]
                for entry in history:
                    # Format: "User: [message]" or "Ecko: [message]"
                    sender_prefix = "User" if entry.get('sender') == 'user' else "Ecko"
                    prompt_parts.append(f"{sender_prefix}: {entry.get('message', '')}")

                prompt_parts.append(f"User: {user_message}") # Προσθήκη νέου μηνύματος
                prompt_parts.append("Ecko:") # Προτροπή για την απάντηση του Ecko

                full_prompt = "\n".join(prompt_parts)
                print(f"--- Sending Prompt to LLM ---\n{full_prompt}\n---------------------------")

                try:
                    # 3. Κλήση LLM
                    response = llm_model.generate_content(full_prompt)
                    ecko_response = response.text
                    print(f"LLM Raw Response: {ecko_response}")

                    # 4. Αποθήκευση στο Firestore (ΜΟΝΟ αν η κλήση LLM ήταν επιτυχής)
                    if user_message and ecko_response: # Αποθήκευσε μόνο αν έχουμε και τα δύο
                       add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response)
                       if not add_success:
                           print("Warning: Failed to save conversation to Firestore.")
                           # Δεν επιστρέφουμε σφάλμα στον χρήστη γι' αυτό, η συζήτηση συνεχίζεται

                except Exception as llm_error:
                    print(f"LLM generation error: {llm_error}")
                    ecko_response = "Συνέβη ένα σφάλμα κατά την επικοινωνία με το AI model."

            else:
                ecko_response = f"Ecko received: '{user_message}'. However, the LLM is not configured correctly."

            print(f"Sending response: {ecko_response}")
            return _corsify_actual_response(make_response(jsonify({"response": ecko_response}), 200))

        except Exception as e:
            print(f"Error processing request: {e}")
            # Προσπάθησε να αποθηκεύσεις τουλάχιστον το σφάλμα αν υπάρχει user message
            if user_message:
                 add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, f"[System Error: {e}]")
            return _corsify_actual_response(make_response(jsonify({"error": f"An internal error occurred: {e}"}), 500)) # Εμφάνιση σφάλματος για debug
    else:
        return _corsify_actual_response(make_response(jsonify({"error": "Method not allowed"}), 405))

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    with app.app_context(): # Χρήση app context για Flask globals όπως το 'request'
         # Delegate to the Flask app instance.
         # The environ and start_response are handled by functions_framework's WSGI adapter.
         return app(request.environ, lambda status, headers: None)

# --- Local testing (optional) ---
# if __name__ == '__main__':
#     # ... (όπως πριν) ...