import functions_framework
import google.cloud.firestore
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Content, GenerationConfig

import os
import datetime
import pytz
import json
import traceback

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
REGION = "us-central1"  # Περιοχή που τρέχει η Function
MODEL_NAME = "gemini-2.5-flash-preview-04-17" # <-- Το σωστό Preview Model

CONVERSATION_COLLECTION = "conversations"
MAIN_CHAT_HISTORY_DOC = "main_chat_history"
MAX_HISTORY_LENGTH = 20 # Max history messages for context

# --- Initialize Clients ---
db = None
model = None
print("Initializing Ecko Backend (Chat only)...")
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

    # Generation Config (Optional)
    generation_config = GenerationConfig(
        temperature=0.7,
        max_output_tokens=2048,
    )

except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    traceback.print_exc()
    raise RuntimeError(f"Initialization failed: {e}")

# --- Helper Functions ---

def get_conversation_history(doc_id=MAIN_CHAT_HISTORY_DOC, limit=MAX_HISTORY_LENGTH):
    """Retrieves conversation history from Firestore for Vertex AI."""
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
    """Adds a new message to the conversation history in Firestore."""
    if not db:
        print("Error: Firestore client not initialized in add_to_conversation_history.")
        return
    if not message_text or not message_text.strip():
        print(f"Warning: Attempted to add empty message from sender '{sender}'. Skipping.")
        return
    try:
        doc_ref = db.collection(CONVERSATION_COLLECTION).document(doc_id)
        timestamp = datetime.datetime.now(pytz.utc)
        new_message = {
            "sender": sender,
            "message": message_text,
            "timestamp": timestamp
        }
        doc_ref.set({"messages": google.cloud.firestore.ArrayUnion([new_message])}, merge=True)
        print(f"Added message from '{sender}' to history (doc: {doc_id}).")
    except Exception as e:
        print(f"Error adding to conversation history (doc: {doc_id}): {e}")
        traceback.print_exc()


# --- Main HTTP Cloud Function ---
@functions_framework.http
def ecko_main(request):
    """Handles incoming HTTP requests."""

    # --- CORS Preflight Handling ---
    if request.method == 'OPTIONS':
        headers = {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Max-Age': '3600'
        }
        return ('', 204, headers)

    # --- CORS Headers for Actual Response ---
    cors_headers = {
        'Access-Control-Allow-Origin': '*'
    }

    # --- Check for Initialization Errors ---
    if not db or not model:
         print("ERROR: Initialization failed (db or model). Function cannot proceed.")
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

            ecko_response = "Συνέβη ένα απρόσμενο σφάλμα κατά την επεξεργασία." # Default

            # --- Normal Conversation Logic ---
            add_to_conversation_history(user_message, 'user')
            conversation_history = get_conversation_history()

            try:
                print(f"Starting chat with history (length: {len(conversation_history)})...")
                chat = model.start_chat(history=conversation_history)
                llm_api_response = chat.send_message(
                    Part.from_text(user_message),
                    generation_config=generation_config,
                )
                ecko_response = llm_api_response.text
                print(f"LLM chat response received successfully: '{ecko_response[:100]}...'")
                add_to_conversation_history(ecko_response, 'model')
            except Exception as llm_error:
                print(f"Error during LLM communication: {llm_error}")
                traceback.print_exc()
                ecko_response = "Συγγνώμη, αντιμετώπισα ένα πρόβλημα κατά την προσπάθεια να σου απαντήσω."
                # Add error message to history as Ecko's response
                add_to_conversation_history(ecko_response, 'model')


            print(f"Sending final response (200 OK): '{ecko_response[:100]}...'")
            return (json.dumps({"response": ecko_response}), 200, cors_headers)

        except Exception as e:
            print(f"Critical error processing POST request: {e}")
            traceback.print_exc()
            return (json.dumps({"error": "An internal server error occurred."}), 500, cors_headers)

    # --- Handle other methods ---
    else:
        print(f"Method not allowed: {request.method}")
        return (json.dumps({"error": "Method Not Allowed"}), 405, cors_headers)