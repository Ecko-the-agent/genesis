// --- CONFIGURATION ---
// Αντικατάστησε με το URL που θα σου δώσει η Google Cloud Function μετά το deploy
// ΠΡΟΣΟΧΗ: Θα χρειαστεί να το ενημερώσεις ΑΦΟΥ γίνει deploy η function!
// Προς το παρόν, άφησέ το κενό ή ένα placeholder.
const ECKO_BACKEND_URL = 'YOUR_FUNCTION_URL_HERE'; // <--- ΘΑ ΑΛΛΑΞΕΙ ΜΕΤΑ!

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

    if (ECKO_BACKEND_URL === 'YOUR_FUNCTION_URL_HERE' || !ECKO_BACKEND_URL) {
         addMessage('System', 'Το URL του backend δεν έχει οριστεί ακόμα στο script.js. Κάνε deploy πρώτα το backend.');
         userInput.value = '';
         return;
    }

    addMessage('Εσύ', message);
    userInput.value = '';
    sendButton.disabled = true;
    loadingIndicator.style.display = 'block';

    try {
        const response = await fetch(ECKO_BACKEND_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: message }),
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        addMessage('Ecko', data.response);

    } catch (error) {
        console.error('Error sending message:', error);
        addMessage('System', `Σφάλμα επικοινωνίας με τον Ecko: ${error.message}`);
    } finally {
        sendButton.disabled = false;
        loadingIndicator.style.display = 'none';
    }
}

// Allow sending message with Enter key
userInput.addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        sendMessage();
    }
});