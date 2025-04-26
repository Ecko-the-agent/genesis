import functions_framework
import json
import os
import datetime
from google.cloud import firestore
from google.cloud import aiplatform # Χρησιμοποιούμε το Vertex AI SDK
# from google.cloud.aiplatform.gapic.schema import predict # Δεν χρειάζεται για το generate_content

print("--- Python script starting (Vertex AI SDK - Corrected - Top Level) ---")

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "europe-west1" # Η region της function (σημαντικό για Vertex AI)
MODEL_NAME = "gemini-1.5-flash-001" # Συγκεκριμένο όνομα μοντέλου στο Vertex AI

CONVERSATION_COLLECTION = 'conversations'
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10
ALLOWED_ORIGIN = "https://ecko-the-agent.github.io" # Το Origin του Frontend

# Headers for CORS responses
CORS_HEADERS = {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '3600'
}

# --- Initialize Clients ---
db = None
model = None # Θα είναι το Vertex AI model object
initialization_error_msg = None # Μήνυμα για τυχόν σφάλμα

# Initialize Firestore
try:
    print("Attempting to initialize Firestore client...")
    db = firestore.Client(project=PROJECT_ID)
    print("Firestore client initialized successfully.")
except Exception as e:
    print(f"CRITICAL ERROR during Firestore initialization: {e}")
    initialization_error_msg = f"Firestore Init Failed: {e}"

# Initialize Vertex AI *ΜΟΝΟ* αν δεν υπάρχει ήδη σφάλμα Firestore
if not initialization_error_msg:
    try:
        print(f"Attempting to initialize Vertex AI for project {PROJECT_ID} in region {REGION}...")
        aiplatform.init(project=PROJECT_ID, location=REGION)
        print("Vertex AI base initialized. Getting GenerativeModel...")
        # Παίρνουμε το model object εδώ - βασίζεται σε ADC για πιστοποίηση
        model = aiplatform.GenerativeModel(MODEL_NAME)
        print(f"Vertex AI GenerativeModel '{MODEL_NAME}' obtained successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR during Vertex AI initialization or model getting: {e}")
        initialization_error_msg = f"Vertex AI Init/Model Failed: {e}"


# --- Firestore Helper Functions --- (Ίδιες)
def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    if not db: return [] # Επιστρέφει κενό αν το db δεν αρχικοποιήθηκε
    # ... (rest of the function is the same) ...
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            history = doc.to_dict().get('messages', [])
            print(f"Retrieved {len(history)} messages from Firestore doc: {doc_id}")
            return history[-limit:]
        else:
            print(f"Firestore document {doc_id} not found.")
            return []
    except Exception as e:
        print(f"Error getting conversation history from Firestore: {e}")
        return []

def add_to_conversation_history(doc_id, user_msg, ecko_msg):
    if not db: return False # Επιστρέφει False αν το db δεν αρχικοποιήθηκε
    # ... (rest of the function is the same) ...
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        new_messages = [
            {"sender": "user", "message": user_msg, "timestamp": timestamp},
            {"sender": "ecko", "message": ecko_msg, "timestamp": timestamp}
        ]
        doc_ref.set({'messages': firestore.ArrayUnion(new_messages)}, merge=True)
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    print(f"--- GCF Entry Point Triggered (ecko_main - Vertex Corrected), Method: {request.method}, Path: {request.path} ---")

    # --- Check for initialization errors ---
    if initialization_error_msg:
        print(f"!!! Initialization Error detected: {initialization_error_msg}")
        error_response = json.dumps({"error": f"Server initialization failed: {initialization_error_msg}"})
        error_headers = CORS_HEADERS.copy()
        error_headers['Content-Type'] = 'application/json'
        # ΣΗΜΑΝΤΙΚΟ: Επιστρέφουμε 500 ΑΚΟΜΑ ΚΑΙ ΓΙΑ OPTIONS αν η αρχικοποίηση απέτυχε
        return (error_response, 500, error_headers)

    # --- Handle CORS Preflight (OPTIONS) ---
    # Αυτό εκτελείται ΜΟΝΟ αν ΔΕΝ υπήρξε σφάλμα αρχικοποίησης
    if request.method == 'OPTIONS':
        print("--- Handling OPTIONS request ---")
        return ('', 204, CORS_HEADERS)

    # --- Handle POST Requests ---
    # Αυτό εκτελείται ΜΟΝΟ αν ΔΕΝ υπήρξε σφάλμα αρχικοποίησης
    if request.method == 'POST':
        print("--- Handling POST request (Vertex AI Corrected) ---")
        response_headers = {'Access-Control-Allow-Origin': CORS_HEADERS['Access-Control-Allow-Origin']}
        response_headers['Content-Type'] = 'application/json'

        ecko_response_text = "Error processing request."
        user_message = ""

        # Έλεγχος αν τα clients είναι έτοιμα (διπλός έλεγχος μετά την αρχικοποίηση)
        if not db or not model:
             print("--- Error: DB or AI Model client is not available post-init ---")
             error_response = json.dumps({"error": "Server error: Required clients not available."})
             return (error_response, 500, response_headers)

        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                print("--- Error: Missing 'message' in JSON body ---")
                error_response = json.dumps({"error": "Missing 'message' in request body"})
                return (error_response, 400, response_headers)

            user_message = request_json['message']
            print(f"--- User message: {user_message} ---")

            # --- Ecko Logic (Firestore & Vertex AI) ---
            print("Getting history...")
            history = get_conversation_history(MAIN_CONVERSATION_DOC_ID)

            # --- Format prompt ---
            prompt_for_vertex = f"User: {user_message}\nEcko:" # Απλό prompt για αρχή
            # TODO: Ενσωμάτωση ιστορικού στο prompt αν χρειαστεί

            print(f"--- Sending Prompt to Vertex AI Model {MODEL_NAME} ---")
            try:
                # Κλήση στο Vertex AI model (που αρχικοποιήθηκε στην αρχή)
                prediction_response = model.generate_content(
                    prompt_for_vertex,
                    generation_config={"max_output_tokens": 2048, "temperature": 0.7, "top_p": 1.0}
                )
                print(f"Vertex AI Raw Response: {prediction_response}")

                # Extract text
                if not prediction_response.candidates:
                    block_reason = prediction_response.prompt_feedback.block_reason if prediction_response.prompt_feedback else 'Unknown'
                    print(f"Vertex AI response blocked or empty. Reason: {block_reason}")
                    ecko_response_text = f"[AI response blocked/empty: {block_reason}]"
                else:
                    # Προσοχή: Η δομή μπορεί να διαφέρει ελαφρώς
                    try:
                        ecko_response_text = prediction_response.candidates[0].content.parts[0].text
                    except (IndexError, AttributeError) as extraction_error:
                        print(f"Error extracting text from Vertex AI response: {extraction_error}. Response: {prediction_response}")
                        ecko_response_text = "[Error processing AI response structure]"

                print(f"Extracted Response: {ecko_response_text}")

                # Save to Firestore
                if user_message and ecko_response_text and not ecko_response_text.startswith("["): # Αποφυγή αποθήκευσης σφαλμάτων
                   print("Attempting to save conversation to Firestore...")
                   add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response_text)
                   if not add_success: print("Warning: Failed to save conversation to Firestore.")

            except Exception as vertex_error:
                print(f"Vertex AI prediction error: {vertex_error}")
                # Εδώ θα πρέπει να εμφανιστούν τα σφάλματα όπως το "Illegal metadata" αν παραμένουν
                ecko_response_text = f"Error communicating with AI model (Vertex): {vertex_error}"

            # -------------------------------------

            print(f"--- Sending final response (200 OK): {ecko_response_text} ---")
            response_data = json.dumps({"response": ecko_response_text})
            return (response_data, 200, response_headers)

        except Exception as e:
            print(f"--- ERROR processing POST request: {e} ---")
            error_response = json.dumps({"error": f"An internal error occurred: {e}"})
            return (error_response, 500, response_headers)

    # --- Other methods ---
    else:
        print(f"--- Method Not Allowed: {request.method} ---")
        return ('Method Not Allowed', 405, CORS_HEADERS)

print("--- Python script finished loading (Vertex AI SDK - Corrected - Bottom Level) ---")