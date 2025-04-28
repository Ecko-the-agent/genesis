// frontend/ecko_script.js

document.addEventListener('DOMContentLoaded', () => {
    // --- CONFIGURATION ---
    // This URL will be replaced by the CI/CD pipeline during frontend deployment
    const ECKO_BACKEND_BASE_URL = '__BACKEND_URL_PLACEHOLDER__'; // <<< DO NOT CHANGE MANUALLY HERE
    // --- NO ACCESS_PASSWORD HERE - Authentication is handled server-side ---
    // Header name for API calls (as defined in backend/config.py)
    // ===> Confirmation: AUTH_HEADER_NAME matches config <===
    const AUTH_HEADER_NAME = 'X-Ecko-Auth'; // Should match backend

    // --- State ---
    // Use sessionStorage to remember authentication *during the session* only.
    let isAuthenticated = sessionStorage.getItem('eckoAuthenticated') === 'true';
    // Store the *actual* secret entered by the user ONLY in this variable,
    // needed for sending the header. Cleared on session end/failure.
    // ===> Confirmation: sessionAuthSecret stores password for session <===
    let sessionAuthSecret = null;
    // SSE connection placeholder
    let eventSource = null;

    // --- DOM Element References (Assigned in assignElements) ---
    let chatbox, userInput, sendButton, loadingChat, fileExplorer, fileContentDisplayCode,
        fileLoading, currentFileIndicator, logViewerCode, logLoading, logSourceSelect,
        logLimitInput, fetchLogsBtn, deploymentStatusDiv, deployBackendBtn, deployFrontendBtn,
        refreshDeployStatusBtn, backendStatusSpan, frontendStatusSpan, backendUrlLink,
        frontendUrlLink, deployLoading, refreshFilesBtn, passwordOverlay, passwordInput,
        passwordSubmitBtn, passwordError, mainContainer, fileContentDisplay;

    // --- Helper Functions ---
    // const logger = (msg, type = 'info') => console[type](`[EckoFE][${type.toUpperCase()}] ${msg}`); // <--- ΠΑΛΙΑ ΕΚΔΟΣΗ ΜΕ ΤΟ ΣΦΑΛΜΑ

    // --- ΔΙΟΡΘΩΜΕΝΗ ΕΚΔΟΣΗ ---
    const logger = (msg, type = 'info') => {
        const formattedMsg = `[EckoFE][${type.toUpperCase()}] ${msg}`;
        switch (type.toLowerCase()) {
            case 'error':
                console.error(formattedMsg);
                break;
            case 'warn':
            case 'warning': // Επιτρέπει και το 'warning'
                console.warn(formattedMsg);
                break;
            case 'info':
                console.info(formattedMsg); // Χρησιμοποιεί info για 'info'
                break;
            case 'debug':
                console.debug(formattedMsg); // Χρησιμοποιεί debug για 'debug'
                break;
            default:
                // Για όλους τους άλλους τύπους (π.χ., 'api', 'auth', 'init', 'sse') χρησιμοποιεί console.log
                console.log(formattedMsg);
        }
    };
    // --- ΤΕΛΟΣ ΔΙΟΡΘΩΜΕΝΗΣ ΕΚΔΟΣΗΣ ---

    const showLoading = (el, msg = 'Loading...') => { if (el) { el.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${msg}`; el.style.display = 'flex'; } };
    const hideLoading = (el) => { if (el) el.style.display = 'none'; };
    const scrollToBottom = (el) => { if (el) el.scrollTop = el.scrollHeight; };

    const addChatMessage = (sender, message, type = 'normal') => {
        if (!chatbox) return;
        const p = document.createElement('p');
        p.dataset.sender = sender; p.dataset.type = type;
        p.style.whiteSpace = 'pre-wrap'; // Preserve formatting
        const strong = document.createElement('strong');
        strong.textContent = sender;
        p.appendChild(strong);
        // ===> Confirmation: Appends message content using createTextNode <===
        p.appendChild(document.createTextNode(` ${message}`)); // Add space after sender
        chatbox.appendChild(p); scrollToBottom(chatbox);
    };

    // --- Authentication ---
    function handlePasswordSubmit() {
        const enteredPassword = passwordInput.value;
        passwordError.textContent = ''; passwordError.style.display = 'none';

        if (!enteredPassword) {
            passwordError.textContent = 'Παρακαλώ εισάγετε τον κωδικό.';
            passwordError.style.display = 'block'; return;
        }

        // ===> Confirmation: NO client-side password comparison exists <===
        logger('Password entered. Attempting to authenticate session.', 'auth');

        // Store the entered secret for this session's API calls
        sessionAuthSecret = enteredPassword;
        // Set session flag
        // ===> Confirmation: sessionStorage flag set <===
        sessionStorage.setItem('eckoAuthenticated', 'true');
        isAuthenticated = true;

        // Hide prompt, show main app
        passwordOverlay.style.display = 'none';
        mainContainer.style.display = 'flex'; // Show the main app
        initializeAppUI(); // Initialize UI requiring authentication
        userInput.focus();
        addChatMessage('System', 'Κωδικός συνεδρίας ορίστηκε. Πραγματοποιείται προσπάθεια σύνδεσης...', 'info');

        // Optional: Make a test API call (e.g., list_files) immediately to verify the secret
        // This provides instant feedback to the user if the secret is wrong.
        testAuthentication();
    }

    async function testAuthentication() {
         try {
             logger("Performing initial authentication test call...", "auth");
             // Use a simple read-only API endpoint like list_files
             await callEckoApi(API_ENDPOINTS.listFiles);
             addChatMessage('System', 'Έλεγχος ταυτότητας επιτυχής. Σύνδεση ενεργή.', 'success');
         } catch (error) {
             // callEckoApi will handle clearing auth state on 403 Forbidden
             // Add a message indicating the failure
             addChatMessage('System', `Αποτυχία ελέγχου ταυτότητας: ${error.message}`, 'error');
             logger(`Authentication test failed: ${error.message}`, 'error');
             // The UI might already be hidden by callEckoApi's error handling if 403 occurred
         }
     }


    function checkSession() {
        // ===> Confirmation: Password prompt shown if not authenticated <===
        const hasSessionFlag = sessionStorage.getItem('eckoAuthenticated') === 'true';

        if (hasSessionFlag && !sessionAuthSecret) {
             // Flag exists but script restarted, need secret re-entry
             logger("Session flag exists, but session secret missing (likely page reload). Prompting.", "auth");
             passwordOverlay.querySelector('p').textContent += ' (Απαιτείται για τη συνεδρία)';
             passwordOverlay.style.display = 'flex';
             passwordInput.focus();
             return false; // Needs password entry
        } else if (!hasSessionFlag) {
            logger("No active session flag. Displaying password prompt.", "auth");
            passwordOverlay.style.display = 'flex';
            passwordInput.focus();
            return false; // Needs password entry
        }
        // If we reach here, flag exists and secret might be populated (unlikely on load)
        // The real check happens in API calls. We assume if the flag is set, the user *might* have a valid secret.
        // Re-authentication will be forced on API failure (e.g., 403).
        logger("Session flag found or not required yet. Proceeding; API calls will validate.", "auth");
        // Tentatively show main container if flag is present, but hide immediately if test call fails
        passwordOverlay.style.display = 'none';
        mainContainer.style.display = 'flex';
        initializeAppUI(); // Initialize UI elements
        testAuthentication(); // Test the stored potential secret immediately
        return true; // Tentatively proceed
    }

    function clearAuthentication() {
         logger("Clearing authentication state.", "auth");
         sessionStorage.removeItem('eckoAuthenticated');
         sessionAuthSecret = null;
         isAuthenticated = false;
         // Stop SSE if running
         if (eventSource) { eventSource.close(); eventSource = null; logger("SSE connection closed.", "sse"); }
         // Hide main content, show prompt
         mainContainer.style.display = 'none';
         passwordOverlay.style.display = 'flex';
         passwordInput.value = ''; // Clear password field
         passwordError.textContent = ''; // Clear previous errors
         passwordError.style.display = 'none';
         passwordInput.focus();
         // Clear sensitive UI elements
         if (fileExplorer) fileExplorer.innerHTML = '(Authentication Required)';
         if (fileContentDisplayCode) fileContentDisplayCode.textContent = '(Authentication Required)';
         if (logViewerCode) logViewerCode.textContent = '(Authentication Required)';
         if (currentFileIndicator) currentFileIndicator.textContent = '';
    }

    // --- API Call Wrapper ---
    async function callEckoApi(endpoint, method = 'GET', body = null) {
        // ===> Confirmation: Placeholder URL check exists <===
        if (ECKO_BACKEND_BASE_URL === '__BACKEND_URL_PLACEHOLDER__') {
             const configErrorMsg = "Σφάλμα Ρύθμισης Frontend: Το URL του Backend δεν έχει οριστεί (placeholder). Εκτελέστε ξανά το deploy του frontend.";
             addChatMessage("System", configErrorMsg, "error");
             logger(configErrorMsg, "error");
             throw new Error("Backend URL not configured.");
        }
         if (!ECKO_BACKEND_BASE_URL) { // General check
             const configErrorMsg = "Σφάλμα Ρύθμισης Frontend: Το URL του Backend λείπει.";
             addChatMessage("System", configErrorMsg, "error");
             logger(configErrorMsg, "error");
             throw new Error("Backend URL not configured!");
         }


        // ===> Confirmation: Authentication check happens first <===
        if (!isAuthenticated || !sessionAuthSecret) {
            const authErrorMsg = "Απαιτείται έγκυρος κωδικός πρόσβασης για αυτή την ενέργεια.";
            logger(authErrorMsg, "error");
            addChatMessage("System", authErrorMsg, "error");
            clearAuthentication(); // Clear state and show prompt
            throw new Error("Authentication required.");
        }

        const url = `${ECKO_BACKEND_BASE_URL}${endpoint}`;
        const options = { method: method.toUpperCase(), headers: { 'Content-Type': 'application/json' }, mode: 'cors' };

        // ===> Confirmation: Auth header added AFTER check, using AUTH_HEADER_NAME <===
        options.headers[AUTH_HEADER_NAME] = sessionAuthSecret;

        if (body && options.method !== 'GET') options.body = JSON.stringify(body);

        logger(`API Call: ${options.method} ${url} (Auth Header Sent: Yes)`, 'api');
        showLoading(deployLoading, `Calling ${endpoint}...`); // Use a generic indicator

        try {
            const response = await fetch(url, options);
            let responseData = null;
            let rawResponseText = null; // Store raw text for potential error messages

            // Try reading text first to handle non-JSON errors better
            try {
                 rawResponseText = await response.text();
                 // Now try parsing as JSON
                 responseData = JSON.parse(rawResponseText);
            } catch (e) {
                 // JSON parsing failed, use raw text if available
                 logger(`Failed to parse JSON response or read text: ${e}`, 'warn');
                 // Keep rawResponseText for error reporting if response not OK
                 responseData = null; // Ensure responseData is null if parsing fails
            }


            logger(`API Response: ${response.status}`, 'api');

            if (!response.ok) {
                 // Construct error message using JSON error first, then raw text, then statusText
                 let errorMsg = `API Error (${response.status})`;
                 if (responseData?.error) { errorMsg += `: ${responseData.error}`; }
                 else if (rawResponseText) { errorMsg += `: ${rawResponseText.substring(0, 200)}${rawResponseText.length > 200 ? '...' : ''}`; } // Use raw text if available
                 else { errorMsg += `: ${response.statusText}`; } // Fallback

                 // ===> Confirmation: clearAuthentication called on 403/401 <===
                 if (response.status === 403) { // Forbidden (likely wrong secret)
                    errorMsg += " (Unauthorized - Λανθασμένος κωδικός ή πρόβλημα ρύθμισης backend)";
                    addChatMessage("System", "Λανθασμένος κωδικός ή πρόβλημα διακομιστή.", "error");
                    clearAuthentication(); // Force re-login
                 } else if (response.status === 401) { // Unauthorized (less likely with direct secret, but possible)
                      errorMsg += " (Unauthorized)";
                      addChatMessage("System", "Σφάλμα αυθεντικοποίησης.", "error");
                      clearAuthentication();
                 }
                 // Add generic message for other errors
                 else if (!errorMsg.includes("Unauthorized")) { // Avoid duplicating auth messages
                     addChatMessage('System', `API Error (${response.status}): ${responseData?.error || rawResponseText || response.statusText}`, 'error');
                 }

                 throw new Error(errorMsg); // Throw structured error message
            }

            // If response was OK, but parsing failed earlier, return status info
            if (response.ok && responseData === null && rawResponseText !== null) {
                 logger("API call OK, but response was not valid JSON. Returning status.", "info");
                 return { _raw: rawResponseText, _status: response.status };
            }

            return responseData; // Return parsed JSON or the status object

        } catch (error) {
            // Error already logged/added to chat in most cases above
            logger(`API Call Failed Catch: ${error.message}`, 'error');
            // Re-throw for caller's specific handling if needed
            throw error;
        } finally {
            hideLoading(deployLoading);
        }
    }

    // --- Chat Functionality ---
    async function sendChatMessage() {
        const message = userInput.value.trim(); if (!message) return;
        addChatMessage('Εσύ', message); userInput.value = ''; userInput.style.height = 'auto';
        sendButton.disabled = true; showLoading(loadingChat, 'Processing...');
        try {
            // Chat endpoint ALSO requires auth header now
            const data = await callEckoApi('/ecko', 'POST', { message });
            if (data?.response) addChatMessage('Ecko', data.response);
            else if (data?.error) addChatMessage('System', `Ecko Error: ${data.error}`, 'error');
            // Check if the response itself indicates an issue, even if status was 2xx
            else if (data?._status && data._status >= 400) {
                 addChatMessage('System', `Ecko returned error: ${data._raw || 'Unknown error'}`, 'error');
            }
             else addChatMessage('System', 'Ecko returned empty/unexpected response.', 'warn');

            // Display action feedback
            if(data?.modification_status) {
                const statusType = data.modification_status.toLowerCase().includes("success") ? 'success' : 'error';
                addChatMessage('System', `Modification: ${data.modification_status} (${data.details || 'No details'})`, statusType);
                if (statusType === 'success' && isAuthenticated) fetchFileList(); // Refresh file list on success
            }
             if(data?.deployment_trigger_status) addChatMessage('System', `Deploy Trigger: ${data.deployment_trigger_status}`, data.deployment_trigger_status === 'Success' ? 'success' : 'error');
             // Add other feedback checks if needed (logs, status)

        } catch(e){ /* Handled by callEckoApi */ }
        finally { sendButton.disabled = false; hideLoading(loadingChat); userInput.focus(); }
    }
    function adjustTextareaHeight() { userInput.style.height = 'auto'; userInput.style.height = `${userInput.scrollHeight}px`; }

    // --- Monitor Panel Functions (All Require Auth Header implicitly via callEckoApi) ---
    const API_ENDPOINTS = { // Define endpoints for clarity
        listFiles: '/list_files',
        getFileContent: '/get_file_content', // Needs ?path=...
        getLogs: '/get_logs', // Needs ?source=...&limit=...[&run_id=...]
        triggerDeploy: '/trigger_deploy', // Needs POST {target: ...}
        deployStatus: '/deployment_status' // Needs ?target=...
    };

    async function fetchFileList() {
        showLoading(fileLoading); fileExplorer.innerHTML = '';
        try {
            const data = await callEckoApi(API_ENDPOINTS.listFiles); // Auth handled by wrapper
            fileExplorer.innerHTML = ''; // Clear loading message
            if (data?.files?.length > 0) {
                const ul = document.createElement('ul');
                data.files.forEach(f => {
                    const li = document.createElement('li'); li.dataset.path = f; li.style.cursor = 'pointer';
                    li.innerHTML = `<i class="fas fa-file"></i> <span>${f}</span>`; // Basic icon
                    li.addEventListener('click', () => fetchFileContent(f));
                    ul.appendChild(li);
                });
                fileExplorer.appendChild(ul);
            } else if (data?.files?.length === 0) { fileExplorer.innerHTML = '<p>(No files found or repo empty)</p>'; }
            else { throw new Error(data?.error || "Invalid file list data received."); }
        } catch (e) { fileExplorer.innerHTML = `<p class="error">Error loading files: ${e.message}</p>`; }
        finally { hideLoading(fileLoading); }
    }

    async function fetchFileContent(filePath) {
        showLoading(document.getElementById('file-content-loading'));
        fileContentDisplayCode.textContent = 'Loading...';
        currentFileIndicator.textContent = `(${filePath})`;
        document.querySelectorAll('#file-explorer li').forEach(li => { li.style.fontWeight = li.dataset.path === filePath ? 'bold' : 'normal'; });
        try {
            const data = await callEckoApi(`${API_ENDPOINTS.getFileContent}?path=${encodeURIComponent(filePath)}`); // Auth handled by wrapper
            if (data?.content !== undefined) {
                fileContentDisplayCode.textContent = data.content;
                const lang = filePath.split('.').pop() || 'plaintext';
                fileContentDisplayCode.parentElement.className = `language-${lang}`; // Set class on <pre>
                // ===> Confirmation: hljs.highlightElement called <===
                if (window.hljs) hljs.highlightElement(fileContentDisplayCode); else logger("Highlight.js not available", "warn");
            } else { throw new Error(data?.error || "File content not found or invalid data."); }
        } catch (e) { fileContentDisplayCode.textContent = `Error loading file: ${e.message}`; }
        finally { hideLoading(document.getElementById('file-content-loading')); }
    }

    async function fetchLogs() {
        showLoading(logLoading); logViewerCode.textContent = '';
        const source = logSourceSelect.value; const limit = logLimitInput.value;
        try {
            const data = await callEckoApi(`${API_ENDPOINTS.getLogs}?source=${source}&limit=${limit}`); // Auth handled by wrapper

            // ===> Confirmation: Handles various log response structures <===
            if (data?.logs) { // Primary field for log content (string or array)
                if (Array.isArray(data.logs)) {
                     logViewerCode.textContent = data.logs.length > 0 ? data.logs.join('\n') : '(No log entries found)';
                } else if (typeof data.logs === 'string') {
                     logViewerCode.textContent = data.logs.trim() ? data.logs : '(Empty log content received)';
                } else {
                     logViewerCode.textContent = '(Unexpected log format in "logs" field)';
                }
            } else if (data?.run_info && data.log_archive_url) { // Structure for GitHub logs download URL (before content download)
                 logViewerCode.innerHTML = `Log URL (pending download): <a href="${data.log_archive_url}" target="_blank">Download Archive</a><br>Run: ${data.run_info.display_title || data.run_info.id} (${data.run_info.status} - ${data.run_info.conclusion || 'N/A'})<br><small>${data.message || 'Awaiting content...'}</small>`;
            } else if (data?.status === 'download_failed' || data?.status === 'empty_log' || data?.status === 'not_found' || data?.status === 'processing') { // Status messages from backend for GH logs
                 logViewerCode.textContent = `Status: ${data.status}\nMessage: ${data.message || 'No details.'}`;
                 if (data.run_info) logViewerCode.textContent += `\nRun: ${data.run_info.id} (${data.run_info.status})`;
                 if (data.log_archive_url) logViewerCode.innerHTML += `<br><small>Archive URL: <a href="${data.log_archive_url}" target="_blank">Link</a></small>`;
            } else if (data?.error) { // General error from API
                 throw new Error(data.error);
            } else { // Unexpected response structure
                 logViewerCode.textContent = '(No log entries or unexpected response format)';
            }
             // Append non-fatal API warnings if present
             if(data?.error && !(data.logs || data.status)) logViewerCode.textContent += `\n\nAPI Warning: ${data.error}`;
        } catch(e){ logViewerCode.textContent = `Error loading logs: ${e.message}`; }
        finally { hideLoading(logLoading); }
    }

    async function triggerDeploy(target) {
        showLoading(deployLoading); deployBackendBtn.disabled = true; deployFrontendBtn.disabled = true;
        try {
            const data = await callEckoApi(API_ENDPOINTS.triggerDeploy, 'POST', { target }); // Auth handled by wrapper
            addChatMessage('System', data?.message || `Trigger request for ${target} sent.`, data?.deployment_trigger_status === 'Success' ? 'success' : 'info');
            setTimeout(() => { fetchDeploymentStatus(target); }, 4000); // Refresh status after delay
        } catch(e){ /* Error handled by callEckoApi */ }
        finally { hideLoading(deployLoading); deployBackendBtn.disabled = false; deployFrontendBtn.disabled = false; }
    }

    async function fetchDeploymentStatus(target) {
        showLoading(deployLoading);
        const statusSpan = target === 'backend' ? backendStatusSpan : frontendStatusSpan;
        const urlLink = target === 'backend' ? backendUrlLink : frontendUrlLink;
        statusSpan.textContent = 'Checking...'; statusSpan.className = 'status-unknown'; urlLink.style.display = 'none';
        try {
            const data = await callEckoApi(`${API_ENDPOINTS.deployStatus}?target=${target}`); // Auth handled by wrapper
            if (data?.status_details) { // Check for the details object
                const s = data.status_details; // s is the run object from github_api.py
                let display = s.status || 'unknown'; let conclusion = s.conclusion;
                let cssClass = `status-${(s.status || 'unknown').toLowerCase()}`;
                if (display === 'completed') {
                     display = conclusion || '?';
                     cssClass += conclusion ? ` conclusion-${conclusion.toLowerCase()}` : '';
                }
                statusSpan.textContent = display; statusSpan.className = cssClass;
                if (s.html_url) { urlLink.href = s.html_url; urlLink.style.display = 'inline'; } // Use html_url from GitHub API response
                else if (s.url) { urlLink.href = s.url; urlLink.style.display = 'inline'; } // Fallback if key name differs
            } else if (data?.status_details?.status === "not_found" || data?.error?.toLowerCase().includes("not found")) { // Check multiple ways for not found
                 statusSpan.textContent = "Not Found"; statusSpan.className = 'status-not_found';
            }
            else { throw new Error(data?.error || "Invalid status data received."); }
        } catch(e){ statusSpan.textContent = 'Error'; statusSpan.className = 'status-error'; }
        finally { hideLoading(deployLoading); }
    }

     // --- SSE Handling (Placeholder/Inactive) ---
     // ===> Action: Added comments explaining SSE status and limitations <===
    function initializeSSE() {
        // NOTE: This SSE implementation is currently INACTIVE/PLACEHOLDER.
        // The standard EventSource API used here DOES NOT support sending custom headers
        // (like the required X-Ecko-Auth). Therefore, the backend would reject the connection.
        // A more complex implementation (e.g., using fetch for the initial connection
        // or a library that supports headers with EventSource) would be needed for SSE authentication.
        if (!isAuthenticated || eventSource) {
            logger("SSE: Not initializing (not authenticated or already initialized).", "sse");
            return;
        }
        const sseUrl = `${ECKO_BACKEND_BASE_URL}/stream`; // Assumes /stream endpoint (likely not active)
        logger(`SSE: Initializing connection to ${sseUrl}... (Auth likely to fail)`, "sse");
        logger("SSE Warning: Standard EventSource does not support custom auth headers. Connection will likely be rejected by the backend.", "warn");

        try {
            eventSource = new EventSource(sseUrl); // This WILL NOT send the required auth header

            eventSource.onopen = () => { logger("SSE connection opened (but likely unauthenticated).", "sse"); };
            eventSource.onerror = (err) => { logger(`SSE error (expected due to auth): ${JSON.stringify(err)}`, "error"); eventSource.close(); eventSource = null; };
            eventSource.onmessage = (event) => { // Generic message handler
                logger(`SSE message received (unexpected): ${event.data}`, "sse");
                try { const data = JSON.parse(event.data); handleSSEMessage(data); } catch (e) { logger(`SSE non-JSON message: ${event.data}`, "warn"); }
            };

        } catch (e) { logger(`SSE initialization failed: ${e}`, "error"); }
    }
    function handleSSEMessage(data) {
         // NOTE: This handler is part of the INACTIVE SSE placeholder.
         logger(`Handling SSE message (Placeholder): ${JSON.stringify(data)}`, "sse");
         // Example logic (would need backend support):
         // if (data.type === 'log') { updateLogView(data.payload); }
         // if (data.type === 'status') { updateDeploymentStatus(data.target, data.payload); }
    }


    // --- Initialization ---
    function assignElements() {
        chatbox = document.getElementById('chatbox');
        userInput = document.getElementById('userInput');
        sendButton = document.getElementById('sendButton');
        loadingChat = document.getElementById('loading-chat');
        fileExplorer = document.getElementById('file-explorer');
        fileContentDisplay = document.getElementById('file-content-display');
        fileContentDisplayCode = fileContentDisplay.querySelector('code');
        // ===> Correction: Use correct loading indicator IDs <===
        fileLoading = document.getElementById('file-loading');
        currentFileIndicator = document.getElementById('current-file-indicator');
        logViewer = document.getElementById('log-viewer');
        logViewerCode = logViewer.querySelector('code');
        // ===> Correction: Use correct loading indicator IDs <===
        logLoading = document.getElementById('log-loading');
        logSourceSelect = document.getElementById('log-source-select');
        logLimitInput = document.getElementById('log-limit-input');
        fetchLogsBtn = document.querySelector('.refresh-button[data-target="logs"]');
        deploymentStatusDiv = document.getElementById('deployment-status');
        deployBackendBtn = document.querySelector('.deploy-button[data-target="backend"]');
        deployFrontendBtn = document.querySelector('.deploy-button[data-target="frontend"]');
        refreshDeployStatusBtn = document.querySelector('.refresh-button[data-target="deploy-status"]');
        backendStatusSpan = document.getElementById('status-backend');
        frontendStatusSpan = document.getElementById('status-frontend');
        backendUrlLink = document.getElementById('url-backend');
        frontendUrlLink = document.getElementById('url-frontend');
        // ===> Correction: Use correct loading indicator IDs <===
        deployLoading = document.getElementById('deploy-loading');
        refreshFilesBtn = document.querySelector('.refresh-button[data-target="files"]');
        passwordOverlay = document.getElementById('password-overlay');
        passwordInput = document.getElementById('passwordInput');
        passwordSubmitBtn = document.getElementById('passwordSubmitBtn');
        passwordError = document.getElementById('passwordError');
        mainContainer = document.querySelector('.main-container');
        logger("DOM Elements assigned.", "init");
    }

    function attachListeners() {
        // Chat Listeners (always active)
        sendButton.addEventListener('click', sendChatMessage);
        userInput.addEventListener('keypress', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); } });
        userInput.addEventListener('input', adjustTextareaHeight);

        // Password Listeners (always active)
        passwordSubmitBtn.addEventListener('click', handlePasswordSubmit);
        passwordInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') handlePasswordSubmit(); });

        logger("Base event listeners attached.", "init");
    }

    function initializeAppUI() {
        // This function is called *after* successful session authentication (secret stored)
        // or tentatively during checkSession if flag exists.
        if (!mainContainer) assignElements(); // Ensure elements are assigned if called early

        // Check authentication state (although API calls will enforce it)
        if (!isAuthenticated) {
            logger("initializeAppUI called but user is not authenticated. Skipping monitor listeners.", "init");
            return;
        }

        // Attach Monitor Listeners (only if not already attached)
        // Add checks to prevent duplicate listeners if initializeAppUI could be called multiple times
        if (refreshFilesBtn && !refreshFilesBtn.dataset.listenerAttached) {
             refreshFilesBtn.addEventListener('click', fetchFileList); refreshFilesBtn.dataset.listenerAttached = 'true';
        }
        if (fetchLogsBtn && !fetchLogsBtn.dataset.listenerAttached) {
             fetchLogsBtn.addEventListener('click', fetchLogs); fetchLogsBtn.dataset.listenerAttached = 'true';
        }
        if (logSourceSelect && !logSourceSelect.dataset.listenerAttached) {
             logSourceSelect.addEventListener('change', fetchLogs); logSourceSelect.dataset.listenerAttached = 'true'; // Fetch on change
        }
         if (logLimitInput && !logLimitInput.dataset.listenerAttached) { // Add listener for limit input change if desired
             logLimitInput.addEventListener('change', fetchLogs); logLimitInput.dataset.listenerAttached = 'true'; // Fetch on change
        }
        if (deployBackendBtn && !deployBackendBtn.dataset.listenerAttached) {
             deployBackendBtn.addEventListener('click', () => triggerDeploy('backend')); deployBackendBtn.dataset.listenerAttached = 'true';
        }
        if (deployFrontendBtn && !deployFrontendBtn.dataset.listenerAttached) {
             deployFrontendBtn.addEventListener('click', () => triggerDeploy('frontend')); deployFrontendBtn.dataset.listenerAttached = 'true';
        }
        if (refreshDeployStatusBtn && !refreshDeployStatusBtn.dataset.listenerAttached) {
             refreshDeployStatusBtn.addEventListener('click', () => { fetchDeploymentStatus('backend'); fetchDeploymentStatus('frontend'); });
             refreshDeployStatusBtn.dataset.listenerAttached = 'true';
        }

        logger("Monitor panel listeners attached.", "init");

        // Load initial monitor data (only if UI is visible and authenticated)
        if (mainContainer.style.display !== 'none' && isAuthenticated) {
             initializeMonitorPanelData();
        }

        // Initialize SSE connection (placeholder - auth will likely fail)
        // initializeSSE(); // Keep commented out unless SSE auth is resolved
    }

    function initializeMonitorPanelData() {
        if (!isAuthenticated) return;
        logger("Loading initial monitor panel data...", "init");
        fetchFileList();
        fetchDeploymentStatus('backend');
        fetchDeploymentStatus('frontend');
        fetchLogs(); // Fetch initial default logs
    }

    function startApp() {
        logger("Initializing Ecko Script...", "init");
        assignElements(); // Assign all potential elements
        attachListeners(); // Attach listeners (incl. password)
        checkSession(); // Check if session exists, show prompt/UI, and test auth
        // initializeAppUI is called by handlePasswordSubmit on success,
        // or by checkSession if session flag exists.
    }

    // --- Start the App ---
    startApp();
}); // End of DOMContentLoaded listener