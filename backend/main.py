import functions_framework
from flask import Flask, request, jsonify, make_response
import google.generativeai as genai
import os
# import google.cloud.secretmanager # Δεν χρειάζεται πια εδώ

# --- Configuration ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "projectgenesis-457923")
# GEMINI_API_KEY_SECRET_NAME = os.environ.get("GEMINI_API_KEY_SECRET_NAME", "gemini-api-key") # Δεν χρειάζεται πια εδώ

# --- Initialize Clients ---
llm_model = None
try:
    gemini_api_key = os.environ.get("GEMINI_API_KEY") # <-- ΔΙΑΒΑΣΕ ΑΠΕΥΘΕΙΑΣ ΤΟ ΚΛΕΙΔΙ
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        llm_model = genai.GenerativeModel('gemini-1.5-flash')
        print("Gemini API Key configured successfully via environment variable.") # <-- Αλλαγμένο μήνυμα
    else:
        print("GEMINI_API_KEY not found in environment variables.") # <-- Αλλαγμένο μήνυμα
except Exception as e:
    print(f"Error configuring Gemini from environment variable: {e}")
    print("LLM will not be available.")


# --- Flask App (for GCF HTTP Trigger) ---
# Define the Flask app globally but initialize within the function scope if needed for GCF
app = Flask(__name__)

# Simple CORS handling function
def _build_cors_preflight_response():
    response = make_response()
    response.headers.add("Access-Control-Allow-Origin", "*") # Προσοχή: Πολύ ανοιχτό για παραγωγή
    response.headers.add("Access-Control-Allow-Headers", "Content-Type")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

# Function to add CORS headers to actual responses
def _corsify_actual_response(response):
    response.headers.add("Access-Control-Allow-Origin", "*") # Προσοχή
    return response

@app.route('/ecko', methods=['POST', 'OPTIONS'])
def handle_ecko_request():
    # Handle CORS preflight requests
    if request.method == 'OPTIONS':
        return _build_cors_preflight_response()

    # Handle actual POST requests
    if request.method == 'POST':
        try:
            request_json = request.get_json(silent=True)
            if not request_json or 'message' not in request_json:
                return _corsify_actual_response(make_response(jsonify({"error": "Missing 'message' in request body"}), 400))

            user_message = request_json['message']
            print(f"Received message: {user_message}")

            # --- Ecko Logic ---
            if llm_model:
                try:
                    # TODO: Add conversation history management from Firestore
                    # TODO: Add logic for self-modification triggers
                    # TODO: Implement more robust error handling for LLM calls
                    full_prompt = f"You are Ecko, a helpful AI assistant. User asks: {user_message}\nEcko:"
                    response = llm_model.generate_content(full_prompt)
                    ecko_response = response.text
                except Exception as llm_error:
                     print(f"LLM generation error: {llm_error}")
                     ecko_response = "Συνέβη ένα σφάλμα κατά την επικοινωνία με το AI model."

            else:
                ecko_response = f"Ecko received: '{user_message}'. However, the LLM is not configured correctly."

            print(f"Sending response: {ecko_response}")
            return _corsify_actual_response(make_response(jsonify({"response": ecko_response}), 200))

        except Exception as e:
            print(f"Error processing request: {e}")
            # Avoid sending detailed errors to the client in production
            return _corsify_actual_response(make_response(jsonify({"error": "An internal error occurred"}), 500))
    else:
        # Method Not Allowed for other HTTP methods like GET, PUT, DELETE etc.
         return _corsify_actual_response(make_response(jsonify({"error": "Method not allowed"}), 405))


# Entry point for Google Cloud Functions HTTP trigger
@functions_framework.http
def ecko_main(request):
    """
    This function is the entry point registered with GCF.
    It uses the Flask app internally to handle routing and requests.
    """
    # Create a new WSGI environ for each request specific to GCF
    # This delegates handling to the Flask app instance.
    return app(request.environ, lambda status, headers: None)

# Local testing (optional, run with 'python main.py' locally if needed)
# Requires Flask to be installed: pip install Flask
# Requires google-generativeai: pip install google-generativeai
# Requires google-cloud-secretmanager: pip install google-cloud-secretmanager
# You'll also need to set up Application Default Credentials locally:
# gcloud auth application-default login
# And set the environment variables GCP_PROJECT and GEMINI_API_KEY_SECRET_NAME
# if __name__ == '__main__':
#     # Note: CORS headers might behave differently in local Flask dev server
#     # compared to GCF environment if not handled explicitly for local run.
#     print("Running Flask app locally for testing...")
#     # Make sure environment variables are set if you run this directly
#     if not os.environ.get("GEMINI_API_KEY_SECRET_NAME"):
#          print("Warning: GEMINI_API_KEY_SECRET_NAME env var not set.")
#     if not os.environ.get("GCP_PROJECT"):
#          print("Warning: GCP_PROJECT env var not set.")
#     app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))