import functions_framework
from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import os

print("--- Python script starting (Top Level) ---")

# --- Configuration ---
ALLOWED_ORIGIN = "https://ecko-the-agent.github.io"

# --- Flask App ---
app = Flask(__name__)
print("--- Flask app instance created ---")
# Apply CORS specifically
try:
    CORS(app, resources={r"/ecko": {"origins": ALLOWED_ORIGIN}}, supports_credentials=False)
    print("--- CORS configured successfully ---")
except Exception as cors_ex:
    print(f"--- ERROR configuring CORS: {cors_ex} ---")


# Απλή διαδρομή για έλεγχο - ΔΕΝ χρησιμοποιεί clients ή request data
@app.route('/ecko', methods=['POST', 'OPTIONS'])
def handle_ecko_request():
    print(f"--- Entered handle_ecko_request route, Method: {request.method} ---") # Log εισόδου στη route

    # Το Flask-Cors θα χειριστεί το OPTIONS αυτόματα

    if request.method == 'POST':
        print("--- Processing POST request (Simplified) ---")
        try:
            # Απλά επιστρέφουμε μια σταθερή απάντηση
            response_data = {"response": "Ecko Test OK"}
            print(f"--- Sending hardcoded response: {response_data} ---")
            # Το Flask-Cors θα προσθέσει κεφαλίδες εδώ
            return make_response(jsonify(response_data), 200)
        except Exception as e:
            print(f"--- ERROR in POST handler: {e} ---")
            # Και εδώ θα προσθέσει κεφαλίδες
            return make_response(jsonify({"error": f"Internal error: {e}"}), 500)
    else:
        # Κανονικά δεν θα έπρεπε να φτάσει εδώ για OPTIONS
        print(f"--- Received non-POST/non-OPTIONS method: {request.method} ---")
        return make_response(jsonify({"error": "Method not allowed"}), 405)

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    print("--- GCF Entry Point Triggered (ecko_main) ---")
    try:
        # Δρομολόγηση στην Flask app
        return app(request.environ, lambda status, headers: None)
    except Exception as main_ex:
        print(f"--- ERROR in ecko_main calling app: {main_ex} ---")
        # Προσπάθεια επιστροφής γενικού σφάλματος αν η κλήση της app αποτύχει
        # (Αυτό μπορεί να μην δουλέψει καλά, αλλά το log είναι χρήσιμο)
        return "Internal Server Error", 500

print("--- Python script finished loading (Bottom Level) ---")