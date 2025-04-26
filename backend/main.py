import functions_framework
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS # <-- Νέα εισαγωγή
import google.generativeai as genai
import os
from google.cloud import firestore
import datetime

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
CONVERSATION_COLLECTION = 'conversations'
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10

# --- Initialize Clients ---
llm_model = None
db = None

try:
    db = firestore.Client(project=PROJECT_ID)
    print("Firestore client initialized successfully.")

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

# --- Firestore Helper Functions --- (Παραμένουν ίδιες)
def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    if not db:
        print("Firestore client not available.")
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
        doc_ref.set(
            {'messages': firestore.ArrayUnion(new_messages)},
            merge=True
        )
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

# --- Flask App ---
app = Flask(__name__)
CORS(app) # <-- ΕΝΕΡΓΟΠΟΙΗΣΗ CORS ΓΙΑ ΟΛΗ ΤΗΝ ΕΦΑΡΜΟΓΗ

# --- ΑΦΑΙΡΟΥΝΤΑΙ ΟΙ ΠΑΛΙΕΣ ΣΥΝΑΡΤΗΣΕΙΣ CORS ---
# def _build_cors_preflight_response(): ...
# def _corsify_actual_response(response): ...

@app.route('/ecko', methods=['POST', 'OPTIONS']) # Το OPTIONS το χειρίζεται πλέον το Flask-Cors
def handle_ecko_request():
    # Το OPTIONS request χειρίζεται αυτόματα από το Flask-Cors
    # Δεν χρειάζεται ειδικός χειρισμός εδώ πια για το OPTIONS.

    if request.method == 'POST':
        ecko_response = "Παρουσιάστηκε ένα σφάλμα."
        user_message = ""

        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                # Αφαιρέθηκε η κλήση _corsify_actual_response
                return make_response(jsonify({"error": "Missing 'message' in request body"}), 400)

            user_message = request_json['message']
            print(f"Received message: {user_message}")

            if llm_model:
                history = get_conversation_history(MAIN_CONVERSATION_DOC_ID)
                prompt_parts = ["You are Ecko, a helpful AI assistant. Here is the recent conversation history:"]
                for entry in history:
                    sender_prefix = "User" if entry.get('sender') == 'user' else "Ecko"
                    prompt_parts.append(f"{sender_prefix}: {entry.get('message', '')}")
                prompt_parts.append(f"User: {user_message}")
                prompt_parts.append("Ecko:")
                full_prompt = "\n".join(prompt_parts)
                print(f"--- Sending Prompt to LLM ---\n{full_prompt}\n---------------------------")

                try:
                    response = llm_model.generate_content(full_prompt)
                    ecko_response = response.text
                    print(f"LLM Raw Response: {ecko_response}")

                    if user_message and ecko_response:
                       add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response)
                       if not add_success:
                           print("Warning: Failed to save conversation to Firestore.")

                except Exception as llm_error:
                    print(f"LLM generation error: {llm_error}")
                    ecko_response = "Συνέβη ένα σφάλμα κατά την επικοινωνία με το AI model."

            else:
                ecko_response = f"Ecko received: '{user_message}'. However, the LLM is not configured correctly."

            print(f"Sending response: {ecko_response}")
            # Αφαιρέθηκε η κλήση _corsify_actual_response
            return make_response(jsonify({"response": ecko_response}), 200)

        except Exception as e:
            print(f"Error processing request: {e}")
            if user_message:
                 add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, f"[System Error: {e}]")
            # Αφαιρέθηκε η κλήση _corsify_actual_response
            return make_response(jsonify({"error": f"An internal error occurred: {e}"}), 500)
    else:
         # Το Flask-Cors θα χειριστεί το OPTIONS, οπότε αυτό το block
         # πιθανόν δεν θα εκτελεστεί ποτέ εκτός αν έρθει πχ GET request.
         # Αφαιρέθηκε η κλήση _corsify_actual_response
        return make_response(jsonify({"error": "Method not allowed"}), 405)

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    # Δεν χρειάζεται πια το app_context εδώ για το request αν δεν χρησιμοποιούμε Flask globals όπως το `request` άμεσα εδώ.
    # Η κλήση της app την χειρίζεται σωστά.
    return app(request.environ, lambda status, headers: None)

# --- Local testing (optional) ---
# if __name__ == '__main__':
#     # CORS θα δουλέψει και τοπικά τώρα
#     print("Running Flask app locally for testing with CORS enabled...")
#     app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))