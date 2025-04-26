import functions_framework
import json
import os
import datetime
from google.cloud import firestore
import vertexai
# --- Σωστό Import για Content & Part ---
from vertexai.generative_models import GenerativeModel, GenerationConfig, Content, Part

print("--- Python script starting (Vertex AI SDK - History Fix - Top Level) ---")

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "europe-west1"
MODEL_NAME = "gemini-1.5-flash" # <-- ΤΟ ΣΩΣΤΟ PREVIEW ΜΟΝΤΕΛΟ

CONVERSATION_COLLECTION = 'conversations'
MAIN_CONVERSATION_DOC_ID = 'main_chat_history'
HISTORY_LIMIT = 10
ALLOWED_ORIGIN = "https://ecko-the-agent.github.io"

CORS_HEADERS = {
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '3600'
}

# --- Initialize Clients ---
db = None
model = None
initialization_error_msg = None

try:
    print("Attempting to initialize Firestore client...")
    db = firestore.Client(project=PROJECT_ID)
    print("Firestore client initialized successfully.")
except Exception as e:
    print(f"CRITICAL ERROR during Firestore initialization: {e}")
    initialization_error_msg = f"Firestore Init Failed: {e}"

if not initialization_error_msg:
    try:
        print(f"Attempting to initialize Vertex AI for project {PROJECT_ID} in region {REGION}...")
        vertexai.init(project=PROJECT_ID, location=REGION)
        print("Vertex AI base initialized. Getting GenerativeModel...")
        model = GenerativeModel(MODEL_NAME)
        print(f"Vertex AI GenerativeModel '{MODEL_NAME}' obtained successfully.")
    except Exception as e:
        print(f"CRITICAL ERROR during Vertex AI initialization or model getting: {e}")
        initialization_error_msg = f"Vertex AI Init/Model Failed: {e}"


# --- Firestore Helper Functions --- (Ίδιες)
def get_conversation_history(doc_id, limit=HISTORY_LIMIT):
    if not db: return []
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        doc = doc_ref.get()
        if doc.exists:
            history = doc.to_dict().get('messages', [])
            print(f"Retrieved {len(history)} messages from Firestore doc: {doc_id}")
            return history[-(limit * 2):]
        else:
            print(f"Firestore document {doc_id} not found.")
            return []
    except Exception as e:
        print(f"Error getting conversation history from Firestore: {e}")
        return []

def add_to_conversation_history(doc_id, user_msg, ecko_msg):
    if not db: return False
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
        user_entry = {"sender": "user", "message": user_msg, "timestamp": timestamp}
        ecko_entry = {"sender": "ecko", "message": ecko_msg, "timestamp": timestamp}
        doc_ref.set({'messages': firestore.ArrayUnion([user_entry, ecko_entry])}, merge=True)
        print(f"Successfully added messages to Firestore doc: {doc_id}")
        return True
    except Exception as e:
        print(f"Error adding messages to Firestore: {e}")
        return False

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    print(f"--- GCF Entry Point Triggered (Vertex History Fix), Method: {request.method}, Path: {request.path} ---")

    if initialization_error_msg:
        print(f"!!! Initialization Error detected: {initialization_error_msg}")
        error_response = json.dumps({"error": f"Server initialization failed: {initialization_error_msg}"})
        error_headers = CORS_HEADERS.copy(); error_headers['Content-Type'] = 'application/json'
        return (error_response, 500, error_headers)

    if request.method == 'OPTIONS':
        print("--- Handling OPTIONS request ---")
        return ('', 204, CORS_HEADERS)

    if request.method == 'POST':
        print("--- Handling POST request (Vertex AI History Fix) ---")
        response_headers = {'Access-Control-Allow-Origin': CORS_HEADERS['Access-Control-Allow-Origin']}
        response_headers['Content-Type'] = 'application/json'

        ecko_response_text = "Error processing request."
        user_message = ""

        if not db or not model:
             print("--- Error: DB or AI Model client is not available ---")
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
            history_list = get_conversation_history(MAIN_CONVERSATION_DOC_ID)

            # --- ΔΙΟΡΘΩΣΗ: Σωστή Διαμόρφωση Ιστορικού ---
            chat_history_for_vertex = []
            for entry in history_list:
                 role = "user" if entry.get("sender") == "user" else "model"
                 # Δημιουργούμε ένα Content object για κάθε μήνυμα
                 chat_history_for_vertex.append(
                     Content(role=role, parts=[Part.from_text(entry.get("message", ""))])
                 )
            print(f"Formatted history for Vertex AI: {len(chat_history_for_vertex)} items")
            # -------------------------------------------

            # Create Chat Session
            chat = model.start_chat(history=chat_history_for_vertex)

            print(f"--- Sending message to Vertex AI Model {MODEL_NAME} via chat ---")
            try:
                # Send message via chat session
                prediction_response = chat.send_message(
                    user_message,
                    generation_config=GenerationConfig(
                        max_output_tokens=2048,
                        temperature=0.7,
                        top_p=1.0,
                    )
                )
                print(f"Vertex AI Chat Response: {prediction_response}")

                # Extract text
                if not prediction_response.candidates:
                    block_reason = prediction_response.prompt_feedback.block_reason if prediction_response.prompt_feedback else 'Unknown'
                    print(f"Vertex AI response blocked or empty. Reason: {block_reason}")
                    ecko_response_text = f"[AI response blocked/empty: {block_reason}]"
                else:
                    try:
                        ecko_response_text = prediction_response.candidates[0].content.parts[0].text
                    except (IndexError, AttributeError) as extraction_error:
                        print(f"Error extracting text from Vertex AI response: {extraction_error}. Response: {prediction_response}")
                        ecko_response_text = "[Error processing AI response structure]"

                print(f"Extracted Response: {ecko_response_text}")

                # Save to Firestore
                if user_message and ecko_response_text and not ecko_response_text.startswith("["):
                   print("Attempting to save conversation to Firestore...")
                   add_success = add_to_conversation_history(MAIN_CONVERSATION_DOC_ID, user_message, ecko_response_text)
                   if not add_success: print("Warning: Failed to save conversation to Firestore.")

            except Exception as vertex_error:
                # Το σφάλμα "history must be..." ΔΕΝ θα έπρεπε να ξαναβγεί εδώ
                print(f"Vertex AI prediction error: {vertex_error}")
                ecko_response_text = f"Error communicating with AI model (Vertex): {vertex_error}"

            # -------------------------------------

            print(f"--- Sending final response (200 OK): {ecko_response_text} ---")
            response_data = json.dumps({"response": ecko_response_text})
            return (response_data, 200, response_headers)

        except Exception as e:
            print(f"--- ERROR processing POST request: {e} ---") # Αυτό πιάνει το σφάλμα history αν αποτύχει η διόρθωση
            error_response = json.dumps({"error": f"An internal error occurred: {e}"})
            return (error_response, 500, response_headers)

    # --- Other methods ---
    else:
        print(f"--- Method Not Allowed: {request.method} ---")
        return ('Method Not Allowed', 405, CORS_HEADERS)

print("--- Python script finished loading (Vertex AI SDK - History Fix - Bottom Level) ---")