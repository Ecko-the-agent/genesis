// --- CONFIGURATION ---
// Αντικατάστησε με το URL που θα σου δώσει η Google Cloud Function μετά το deploy
// ΠΡΟΣΟΧΗ: Θα χρειαστεί να το ενημερώσεις ΑΦΟΥ γίνει deploy η function!
// Προς το παρόν, άφησέ το κενό ή ένα placeholder.
const ECKO_BACKEND_URL = 'https://ecko-http-function-p2bsy3odya-ew.a.run.app'; // <--- ΘΑ ΑΛΛΑΞΕΙ ΜΕΤΑ!

const chatbox = document.getElementById('chatbox');
const userInput = document.getElementById('userInput');
const sendButton = document.getElementById('sendButton');
const loadingIndicator = document.getElementById('loading');

function addMessage(sender, message) {
    const p = document.createElement('p');
    // Basic sanitation to prevent HTML injection from response
    const sanitizedMessage = message.replace(/</g, "<").replace(/>/g, ">");
    p.innerHTML = `<strong>${sender}:</strong> ${sanitizedMessage}`;
    chatbox.appendChild(p);
    chatbox.scrollTop = chatbox.scrollHeight; // Scroll to bottom
}

async function sendMessage() {
    const message = userInput.value.trim();
    if (!message) return;

    // Ο ΕΛΕΓΧΟΣ IF ΕΧΕΙ ΑΦΑΙΡΕΘΕΙ ΑΠΟ ΕΔΩ

    addMessage('Εσύ', message);
    userInput.value = '';
    sendButton.disabled = true;
    loadingIndicator.style.display = 'block';

    try {
        const response = await fetch(ECKO_BACKEND_URL, { // Τώρα θα εκτελεστεί κανονικά
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: message }),
        });

        if (!response.ok) {
            // Handling non-2xx responses, including potential CORS issues if they reappear
            // We might need more specific error handling based on status code later
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        addMessage('Ecko', data.response);

    } catch (error) {
        console.error('Error sending message:', error);
        // Check if the error is a TypeError (often network/CORS related)
        if (error instanceof TypeError) {
             addMessage('System', `Σφάλμα δικτύου ή CORS κατά την επικοινωνία με τον Ecko. Βεβαιωθείτε ότι το backend URL (${ECKO_BACKEND_URL}) είναι σωστό και προσβάσιμο.`);
        } else {
             addMessage('System', `Σφάλμα επικοινωνίας με τον Ecko: ${error.message}`);
        }
    } finally {
        sendButton.disabled = false;
        loadingIndicator.style.display = 'none';
    }
}