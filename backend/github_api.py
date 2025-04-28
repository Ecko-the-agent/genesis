# backend/github_api.py
import requests
import logging
import json
import io        # Added for in-memory bytes handling
import zipfile   # Added for zip file processing
import config    # Use centralized config
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

def _make_request(method, endpoint, pat, data=None, params=None, allow_redirects=True, stream=False, timeout=20):
    """Internal helper to make GitHub API requests."""
    if not pat: raise ValueError("GitHub PAT is required.")
    if not config.GITHUB_REPO_OWNER or not config.GITHUB_REPO_NAME: raise ValueError("GitHub repository config missing.")

    # Use urljoin correctly for repo-specific endpoints
    base_api_url = config.GITHUB_API_BASE_URL
    repo_specific_base = urljoin(base_api_url, f"/repos/{config.GITHUB_REPO_OWNER}/{config.GITHUB_REPO_NAME}/")
    # Determine if the endpoint is already repo-specific or needs the base
    if endpoint.startswith(f"/repos/{config.GITHUB_REPO_OWNER}/{config.GITHUB_REPO_NAME}/"):
        url = urljoin(base_api_url, endpoint) # Endpoint is already absolute-like
    else:
        url = urljoin(repo_specific_base, endpoint.lstrip('/')) # Make relative endpoint repo-specific

    headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"Bearer {pat}", "X-GitHub-Api-Version": "2022-11-28"}

    logger.info(f"GitHub API Request: {method.upper()} {url}")
    try:
        response = requests.request(
            method.upper(), url, headers=headers, json=data, params=params,
            timeout=timeout, allow_redirects=allow_redirects, stream=stream
        )
        logger.info(f"GitHub API Response Status: {response.status_code} for {url}")
        if 'X-RateLimit-Remaining' in response.headers: logger.debug(f"Rate Limit Remaining: {response.headers['X-RateLimit-Remaining']}")

        # Handle non-2xx/302 errors first (unless streaming, check after reading)
        if not stream and not response.ok and not (response.status_code == 302 and not allow_redirects):
            response.raise_for_status() # Will raise HTTPError

        # Handle successful responses
        if stream: # If streaming, return the response object directly for processing
             if response.ok:
                 return response, None
             else:
                 # Try to get error details even for stream failures
                 error_body = response.text; error_detail = error_body or response.reason
                 try: error_json = response.json(); message = error_json.get("message", "Unknown API error"); error_detail = f"{message} ({json.dumps(error_json.get('errors', []))})"
                 except json.JSONDecodeError: pass
                 logger.error(f"GitHub API Stream HTTP error: {response.status_code} - {error_detail}")
                 return None, f"GitHub API Stream Error ({response.status_code}): {error_detail}"

        if response.status_code == 204: return {}, None # No Content
        if response.status_code == 302 and not allow_redirects: # Log download redirect
             redirect_url = response.headers.get('Location')
             if not redirect_url: raise ValueError("API returned 302 redirect without Location header.")
             return {"redirect_url": redirect_url}, None
        # Assume JSON for other successful responses (200, 201, etc.)
        return response.json(), None

    except requests.exceptions.HTTPError as e:
        error_body = e.response.text; error_detail = error_body or e.response.reason
        try: error_json = e.response.json(); message = error_json.get("message", "Unknown API error"); error_detail = f"{message} ({json.dumps(error_json.get('errors', []))})"
        except json.JSONDecodeError: pass
        logger.error(f"GitHub API HTTP error: {e.response.status_code} - {error_detail}")
        return None, f"GitHub API Error ({e.response.status_code}): {error_detail}"
    except requests.exceptions.RequestException as e:
        logger.error(f"GitHub API connection error: {e}", exc_info=True)
        return None, f"GitHub API connection error: {e}"
    except Exception as e:
        logger.error(f"Unexpected GitHub API error during request: {e}", exc_info=True)
        return None, f"Unexpected GitHub API error: {e}"

def trigger_workflow_dispatch(pat, workflow_filename, ref=config.GITHUB_MAIN_BRANCH):
    """Triggers a workflow_dispatch event."""
    endpoint = f"actions/workflows/{workflow_filename}/dispatches"
    payload = {"ref": ref}
    data, error = _make_request("POST", endpoint, pat, data=payload)
    if error:
        if "404" in error: return False, f"Workflow '{workflow_filename}' not found or dispatch disabled."
        return False, f"Failed trigger: {error}"
    # Successful trigger returns 204 No Content, which _make_request maps to {}
    elif isinstance(data, dict):
        return True, f"Workflow '{workflow_filename}' triggered successfully."
    else: return False, "Unexpected trigger response format."

def get_latest_workflow_run(pat, workflow_filename, branch=config.GITHUB_MAIN_BRANCH):
    """Gets details of the latest workflow run."""
    endpoint = f"actions/workflows/{workflow_filename}/runs"
    params = {"branch": branch, "per_page": 1}
    data, error = _make_request("GET", endpoint, pat, params=params)
    if error: return None, f"Failed get runs: {error}"
    runs = data.get("workflow_runs", [])
    if not runs: return None, "No runs found for this workflow and branch."
    logger.info(f"Found latest run ID {runs[0].get('id')} for workflow {workflow_filename}")
    return runs[0], None # Return latest run object

def get_workflow_log_url(pat, run_id):
    """Gets the log archive download URL (returns the redirect URL)."""
    if not run_id: return None, "Run ID required."
    endpoint = f"actions/runs/{run_id}/logs"
    # Make request, but expect 302 redirect and *don't* follow it
    data, error = _make_request("GET", endpoint, pat, allow_redirects=False)
    if error:
        # Handle specific errors like 404 or 410 Gone for expired logs
        if "404" in error or "410" in error: return None, f"Logs for run {run_id} not found or expired."
        return None, f"Failed get log URL: {error}"

    # Expect data to contain {'redirect_url': '...'} from _make_request
    redirect_url = data.get("redirect_url") if isinstance(data, dict) else None
    if redirect_url:
         log_info = {
             "log_archive_url": redirect_url,
             "message": f"Log archive download URL ready (Run ID: {run_id})."
         }
         return log_info, None
    else:
        # This might happen if logs are still processing or API response changed
        logger.warning(f"Log URL request for run {run_id} did not return a redirect_url. API response: {data}")
        return None, "Log download URL not found (maybe still processing or API issue?)."


def download_and_extract_log_content(log_archive_url, pat, max_log_size_bytes=500 * 1024):
    """
    Downloads a GitHub Actions log archive (zip) from the URL, extracts log files,
    and returns their combined content.

    Args:
        log_archive_url (str): The pre-signed URL obtained from get_workflow_log_url.
        pat (str): GitHub PAT (may not be strictly needed if URL is pre-signed, but good practice).
        max_log_size_bytes (int): Maximum total size of extracted logs to return.

    Returns:
        tuple: (combined log content string or None, error message string or None)
    """
    if not log_archive_url:
        return None, "Log archive URL is required."

    logger.info(f"Attempting to download log archive from URL...")
    # Use requests directly for the download URL, might not need PAT if pre-signed
    # Add auth header just in case it becomes necessary in some scenarios
    headers = {"Authorization": f"Bearer {pat}"}
    total_extracted_size = 0
    combined_logs = ""

    try:
        # Use stream=True to handle potentially large zip files efficiently
        response = requests.get(log_archive_url, headers=headers, stream=True, timeout=60) # Increased timeout for download
        response.raise_for_status() # Check for download errors (4xx, 5xx)

        logger.info("Log archive downloaded successfully. Extracting...")

        # Process the zip file from the response stream in memory
        with io.BytesIO() as memory_zip:
             # Read stream chunk by chunk into memory buffer
             chunk_size = 8192
             for chunk in response.iter_content(chunk_size=chunk_size):
                 memory_zip.write(chunk)
             memory_zip.seek(0) # Rewind buffer to the beginning

             # Open the zip file from the memory buffer
             with zipfile.ZipFile(memory_zip, 'r') as archive:
                 log_files = []
                 # Find text files (likely logs) within the archive
                 for item in archive.infolist():
                     # Often logs are directly .txt or in numbered folders
                     if not item.is_dir() and item.filename.lower().endswith('.txt'):
                          log_files.append(item.filename)
                     # Sometimes they might be inside step-specific folders without .txt
                     # Example: '1_Setup Job.txt' or 'job_folder/1_step_name.txt'
                     # You might need more sophisticated detection if logs aren't consistently .txt

                 if not log_files:
                      logger.warning(f"No '.txt' log files found in the archive. Archive contents: {archive.namelist()}")
                      # Try reading any file that isn't clearly binary? Risky.
                      # For now, return specific message.
                      return None, "No '.txt' log files found within the downloaded archive."

                 log_files.sort() # Process in a consistent order

                 # Extract and combine content from identified log files
                 for filename in log_files:
                     try:
                         # Read file content as bytes
                         content_bytes = archive.read(filename)
                         # Decode assuming UTF-8 (most common for logs)
                         content_str = content_bytes.decode('utf-8', errors='replace') # Replace errors to avoid crashing

                         # Check size limit before appending
                         if total_extracted_size + len(content_bytes) > max_log_size_bytes:
                              combined_logs += f"\n... [LOG TRUNCATED DUE TO SIZE LIMIT ({max_log_size_bytes} bytes)] ...\n"
                              logger.warning(f"Log content truncated at {max_log_size_bytes} bytes.")
                              break # Stop processing more files

                         # Add a separator/header for clarity if multiple files exist
                         if len(log_files) > 1:
                             combined_logs += f"\n--- Log File: {filename} ---\n"
                         combined_logs += content_str
                         total_extracted_size += len(content_bytes)

                     except Exception as e_read:
                          logger.error(f"Error reading/decoding file '{filename}' from zip: {e_read}")
                          combined_logs += f"\n--- Error reading file: {filename} ({e_read}) ---\n"
                          # Continue to next file

        logger.info(f"Successfully extracted and combined {len(log_files)} log files ({total_extracted_size} bytes).")
        return combined_logs.strip(), None # Return combined content

    except requests.exceptions.RequestException as e_dl:
        logger.error(f"Failed to download log archive: {e_dl}", exc_info=True)
        return None, f"Failed to download log archive: {e_dl}"
    except zipfile.BadZipFile:
        logger.error("Downloaded file is not a valid zip archive.")
        return None, "Downloaded file is not a valid zip archive."
    except Exception as e_zip:
        logger.error(f"Error processing log archive: {e_zip}", exc_info=True)
        return None, f"Error processing log archive: {e_zip}"