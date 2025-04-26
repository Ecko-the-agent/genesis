// --- CONFIGURATION ---
// Το σωστό URL της Cloud Function σου
const ECKO_BACKEND_URL = 'https://ecko-http-function-p2bsy3odya-uc.a.run.app';

// --- DOM Elements ---
const chatbox = document.getElementById('chatbox');
const userInput = document.getElementById('userInput');
const sendButton = document.getElementById('sendButton');
const loadingIndicator = document.getElementById('loading');

// --- Functions ---

/**
 * Προσθέτει ένα μήνυμα στο chatbox.
 * @param {string} sender - Ο αποστολέας ('Εσύ', 'Ecko', 'System').
 * @param {string} message - Το περιεχόμενο του μηνύματος.
 */
function addMessage(sender, message) {
    const p = document.createElement('p');
    // Απλή απολύμανση για να αποφευχθεί βασική εισαγωγή HTML από την απάντηση
    // Αντικαθιστά < με < και > με >
    const sanitizedMessage = message.replace(/</g, "<").replace(/>/g, ">");
    p.innerHTML = `<strong>${sender}:</strong> ${sanitizedMessage}`;
    chatbox.appendChild(p);
    chatbox.scrollTop = chatbox.scrollHeight; // Κύλιση προς τα κάτω
}

/**
 * Στέλνει το μήνυμα του χρήστη στο backend και εμφανίζει την απάντηση.
 */
async function sendMessage() {
    const message = userInput.value.trim();
    if (!message) return; // Αν το μήνυμα είναι κενό, μην κάνεις τίποτα

    // --- ΑΦΑΙΡΕΘΗΚΕ Ο ΠΕΡΙΤΤΟΣ ΕΛΕΓΧΟΣ IF ΑΠΟ ΕΔΩ ---

    addMessage('Εσύ', message); // Εμφάνισε το μήνυμα του χρήστη
    userInput.value = ''; // Καθάρισε το πεδίο εισαγωγής
    sendButton.disabled = true; // Απενεργοποίησε το κουμπί αποστολής
    loadingIndicator.style.display = 'block'; // Εμφάνισε την ένδειξη φόρτωσης

    try {
        // Στείλε το αίτημα στο backend
        const response = await fetch(ECKO_BACKEND_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            // Στείλε το μήνυμα μέσα σε ένα JSON αντικείμενο
            body: JSON.stringify({ message: message }),
        });

        // Έλεγχος αν η απάντηση HTTP ήταν επιτυχής (π.χ. 200 OK)
        if (!response.ok) {
            // Αν όχι, δημιούργησε ένα σφάλμα με την κατάσταση HTTP
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        // Αν η απάντηση ήταν ΟΚ, πάρε τα δεδομένα JSON
        const data = await response.json();

        // Έλεγχος αν υπάρχει η απάντηση μέσα στα δεδομένα
        if (data && data.response) {
           addMessage('Ecko', data.response); // Εμφάνισε την απάντηση του Ecko
        } else {
           addMessage('System', 'Λήφθηκε μη αναμενόμενη απάντηση από τον Ecko.');
        }

    } catch (error) {
        // Αν συνέβη οποιοδήποτε σφάλμα (δικτύου ή επεξεργασίας)
        console.error('Error sending message:', error);
        // Εμφάνισε ένα μήνυμα σφάλματος στο chatbox
        addMessage('System', `Σφάλμα επικοινωνίας με τον Ecko: ${error.message}`);
    } finally {
        // Αυτό εκτελείται ΠΑΝΤΑ, είτε με επιτυχία είτε με σφάλμα
        sendButton.disabled = false; // Ενεργοποίησε ξανά το κουμπί
        loadingIndicator.style.display = 'none'; // Κρύψε την ένδειξη φόρτωσης
    }
}

// --- Event Listeners ---

// Επιτρέπει την αποστολή μηνύματος πατώντας το Enter στο πεδίο εισαγωγής
userInput.addEventListener('keypress', function (e) {
    if (e.key === 'Enter') {
        sendMessage();
    }
});

// Προσθήκη event listener και στο κουμπί (παρόλο που υπάρχει και το onclick στο HTML)
// για καλή πρακτική, αν και το onclick θα δουλέψει.
// sendButton.addEventListener('click', sendMessage);

// Αρχικό μήνυμα καλωσορίσματος (προαιρετικό, μπορεί να μπει και στο HTML)
// addMessage('System', 'Έτοιμος για εντολές.');