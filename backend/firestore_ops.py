# backend/firestore_ops.py
import logging
from datetime import datetime, timezone
import google.cloud.firestore
from google.api_core.exceptions import NotFound
import config

logger = logging.getLogger(__name__)
firestore_db = None # Initialize as None

def _get_db():
    """Initializes Firestore client if needed."""
    global firestore_db
    # Check if initialization is needed (is None)
    if firestore_db is None:
        try:
            # Project ID should be inferred from the environment when running on GCP
            # Passing the project ID explicitly can sometimes help in local/non-standard environments
            firestore_db = google.cloud.firestore.Client(project=config.GCP_PROJECT_ID)
            logger.info("Firestore client initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Firestore client: {e}", exc_info=True)
            # Mark as None on failure to potentially allow retries on subsequent calls
            firestore_db = None # Changed from False to None
    # Return the current state of the client (either the client object or None)
    return firestore_db

def get_conversation_history(limit=config.HISTORY_LIMIT):
    """Fetches the last 'limit' messages from Firestore."""
    db = _get_db()
    # Check if db client is available (not None)
    if not db:
        logger.error("Firestore client not available, cannot fetch history.")
        return []
    try:
        doc_ref = db.collection(config.FIRESTORE_COLLECTION).document(config.CONVERSATION_DOC_ID)
        doc = doc_ref.get()
        if doc.exists:
            all_messages = doc.to_dict().get("messages", [])
            # Sort by timestamp (most recent first), limit, then reverse
            def get_timestamp(msg):
                ts = msg.get('timestamp')
                # Handle both datetime objects and potential string representations if legacy data exists
                if isinstance(ts, datetime):
                    # Ensure timezone-aware for proper comparison (assume UTC if naive)
                    return ts.replace(tzinfo=ts.tzinfo or timezone.utc)
                elif isinstance(ts, str):
                    try:
                         # Attempt to parse common formats, ensuring timezone awareness
                         dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                         return dt.replace(tzinfo=dt.tzinfo or timezone.utc) # Ensure UTC
                    except ValueError:
                         # Fallback for unparseable strings
                         return datetime.min.replace(tzinfo=timezone.utc)
                # Fallback for missing/invalid timestamp
                return datetime.min.replace(tzinfo=timezone.utc)

            sorted_messages = sorted(all_messages, key=get_timestamp, reverse=True)
            limited_messages = sorted_messages[:limit]
            history = limited_messages[::-1] # Oldest first for LLM context
            logger.info(f"Fetched {len(history)} messages from Firestore history.")
            return history
        else:
            logger.info(f"Conversation document '{config.CONVERSATION_DOC_ID}' does not exist.")
            return []
    except Exception as e:
        logger.error(f"Error getting conversation history: {e}", exc_info=True)
        return []

def add_to_conversation_history(sender, message):
    """Adds a message to the Firestore history."""
    db = _get_db()
    # Check if db client is available (not None)
    if not db:
        logger.error("Firestore client not available, cannot add message to history.")
        return
    try:
        doc_ref = db.collection(config.FIRESTORE_COLLECTION).document(config.CONVERSATION_DOC_ID)
        timestamp = datetime.now(timezone.utc) # Use timezone-aware UTC timestamp

        # Truncate potentially very long messages before storing
        # Firestore has document size limits (~1MB)
        MAX_MSG_LENGTH = 15000 # Adjust as needed, consider average message size and history length
        truncated_message = message
        if isinstance(message, str) and len(message) > MAX_MSG_LENGTH:
            truncated_message = message[:MAX_MSG_LENGTH] + "...[truncated]"
            logger.warning(f"Message from {sender} truncated to {MAX_MSG_LENGTH} chars for history.")
        elif not isinstance(message, str):
             # Convert non-strings, but log a warning
             logger.warning(f"Non-string message from {sender} being converted to string for history: {type(message)}")
             truncated_message = str(message) # Simple conversion
             if len(truncated_message) > MAX_MSG_LENGTH:
                  truncated_message = truncated_message[:MAX_MSG_LENGTH] + "...[truncated]"


        new_message = {"sender": sender, "message": truncated_message, "timestamp": timestamp}

        # Use FieldValue.array_union to atomically add the message
        # This requires the document to exist. Handle NotFound.
        try:
            doc_ref.update({"messages": google.cloud.firestore.FieldValue.array_union([new_message])})
            logger.info(f"Appended message from {sender} to Firestore.")
        except NotFound:
            # Document doesn't exist, create it with the first message
            logger.info(f"Creating conversation document '{config.CONVERSATION_DOC_ID}' with first message.")
            doc_ref.set({"messages": [new_message]})
        except Exception as update_err:
             # Catch potential errors during update (e.g., document size limit exceeded)
             logger.error(f"Error updating conversation document: {update_err}", exc_info=True)
             # Potentially add a fallback or retry mechanism if needed

    except Exception as e:
        # Catch errors preparing the message itself
        logger.error(f"Error preparing message for Firestore history: {e}", exc_info=True)