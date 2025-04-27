// --- CONFIGURATION ---
const ECKO_BACKEND_URL = 'https://ecko-http-function-p2bsy3odya-uc.a.run.app/ecko';

// Wait for the HTML document to be fully loaded before running the script logic
document.addEventListener('DOMContentLoaded', (event) => {

    // Get references to elements AFTER the DOM is ready
    const chatbox = document.getElementById('chatbox');
    const userInput = document.getElementById('userInput');
    const sendButton = document.getElementById('sendButton');
    const loadingIndicator = document.getElementById('loading');

    // Check if elements were found (basic validation)
    if (!chatbox || !userInput || !sendButton || !loadingIndicator) {
        console.error("Error: One or more required HTML elements not found!");
        // Optionally display an error message to the user
        // const body = document.querySelector('body');
        // if (body) body.innerHTML = "<p>Error loading chat interface. Required elements missing.</p>";
        return; // Stop script execution if elements are missing
    }


    function addMessage(sender, message) {
        const p = document.createElement('p');
        p.style.whiteSpace = 'pre-wrap';
        p.innerHTML = `<strong>${sender}:</strong> `;

        const messageText = document.createTextNode(message);
        p.appendChild(messageText);

        chatbox.appendChild(p);
        chatbox.scrollTop = chatbox.scrollHeight;
    }

    async function sendMessage() {
        const message = userInput.value.trim();
        if (!message) return;

        if (!ECKO_BACKEND_URL) {
            addMessage('System', 'Το URL του backend δεν έχει οριστεί ακόμα στο ecko_script.js.');
            userInput.value = '';
            return;
        }

        addMessage('Εσύ', message);
        userInput.value = '';
        sendButton.disabled = true;
        loadingIndicator.textContent = 'Ο Ecko σκέφτεται...';
        loadingIndicator.style.display = 'block';

        try {
            const response = await fetch(ECKO_BACKEND_URL, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message }),
                mode: 'cors'
            });

            if (!response.ok) {
                let errorMsg = `HTTP error! status: ${response.status}`;
                try {
                    const errorData = await response.json();
                    errorMsg += ` - ${errorData.error || 'No error details'}`;
                } catch (e) {
                    // Ignore
                }
                throw new Error(errorMsg);
            }

            const data = await response.json();
            addMessage('Ecko', data.response);

            if (data.modification_status) {
                addMessage('System', `Modification Status: ${data.modification_status}`);
                if (data.modification_details) {
                    addMessage('System', `Details: ${data.modification_details}`);
                }
                if (data.modification_status.includes("Success")) {
                    addMessage("System", "Οι αλλαγές κώδικα εφαρμόστηκαν. Ίσως χρειαστεί να ανανεώσετε τη σελίδα (F5) μετά την ολοκλήρωση του deployment για να δείτε τις αλλαγές στο UI.");
                }
            }

        } catch (error) {
            console.error('Error sending message:', error);
            addMessage('System', `Σφάλμα επικοινωνίας με τον Ecko: ${error.message}`);
        } finally {
            sendButton.disabled = false;
            loadingIndicator.style.display = 'none';
            loadingIndicator.textContent = 'Περιμένετε...';
            userInput.focus(); // Now this should work as userInput is guaranteed to exist
        }
    }

    // Allow sending message with Enter key
    userInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });

    // Add event listener to the button programmatically
    sendButton.addEventListener('click', sendMessage);

    // Initial focus on input field
    userInput.focus();

}); // End of DOMContentLoaded listener