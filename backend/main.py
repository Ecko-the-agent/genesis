import functions_framework
import json # Για χειρισμό JSON
import os

# Headers για τις απαντήσεις CORS
# ΣΗΜΑΝΤΙΚΟ: Αν το frontend σου αλλάξει domain, πρέπει να αλλάξεις κι αυτό!
CORS_HEADERS = {
    'Access-Control-Allow-Origin': 'https://ecko-the-agent.github.io',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '3600' # Πόσο χρόνο ο browser μπορεί να κάνει cache το OPTIONS response
}

print("--- Python script starting (No Flask - Top Level) ---")

# --- GCF Entry Point ---
@functions_framework.http
def ecko_main(request):
    """
    Χειρίζεται HTTP requests απευθείας, χωρίς Flask.
    Περιλαμβάνει χειρισμό CORS.
    """
    print(f"--- GCF Entry Point Triggered (ecko_main), Method: {request.method} ---")

    # --- Χειρισμός CORS Preflight (OPTIONS) ---
    if request.method == 'OPTIONS':
        print("--- Handling OPTIONS request ---")
        # Στείλε τις κεφαλίδες CORS και μια κενή απάντηση 204 (No Content)
        # Η tuple μορφή (body, status, headers) είναι ο τρόπος επιστροφής στο functions-framework
        return ('', 204, CORS_HEADERS)

    # --- Χειρισμός POST Requests ---
    if request.method == 'POST':
        print("--- Handling POST request ---")

        # Πρόσθεσε την Allow-Origin κεφαλίδα και στις POST απαντήσεις
        response_headers = {'Access-Control-Allow-Origin': CORS_HEADERS['Access-Control-Allow-Origin']}

        try:
            # Πάρε τα δεδομένα JSON από το request body
            request_json = request.get_json(silent=True)
            print(f"--- Received JSON data: {request_json} ---")

            if not request_json or 'message' not in request_json:
                print("--- Error: Missing 'message' in JSON body ---")
                error_response = json.dumps({"error": "Missing 'message' in request body"})
                # Πρόσθεσε Content-Type και επέστρεψε 400
                response_headers['Content-Type'] = 'application/json'
                return (error_response, 400, response_headers)

            user_message = request_json['message']
            print(f"--- User message: {user_message} ---")

            # --- ΕΔΩ ΘΑ ΜΠΕΙ Η ΛΟΓΙΚΗ ΤΟΥ ECKO (Firestore, Gemini κλπ.) ---
            # Προς το παρόν, απλά επιστρέφουμε μια σταθερή απάντηση
            ecko_response_text = f"Ecko (No Flask) received: '{user_message}' - Test OK"
            # --------------------------------------------------------------

            print(f"--- Sending response: {ecko_response_text} ---")
            response_data = json.dumps({"response": ecko_response_text})
            # Πρόσθεσε Content-Type και επέστρεψε 200
            response_headers['Content-Type'] = 'application/json'
            return (response_data, 200, response_headers)

        except Exception as e:
            print(f"--- ERROR processing POST request: {e} ---")
            error_response = json.dumps({"error": f"An internal error occurred: {e}"})
            # Πρόσθεσε Content-Type και επέστρεψε 500
            response_headers['Content-Type'] = 'application/json'
            return (error_response, 500, response_headers)

    # --- Άλλες μέθοδοι (π.χ., GET) δεν επιτρέπονται ---
    else:
        print(f"--- Method Not Allowed: {request.method} ---")
        # Πρόσθεσε τις βασικές CORS headers και εδώ για συνέπεια
        return ('Method Not Allowed', 405, CORS_HEADERS)

print("--- Python script finished loading (No Flask - Bottom Level) ---")