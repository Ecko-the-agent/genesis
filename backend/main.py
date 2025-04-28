# backend/main.py
import functions_framework
import os
import json
import logging
import re # Import regular expressions module
from functools import wraps
from flask import Flask, request, jsonify, make_response # Removed Response import (for SSE)

# --- Import Project Modules ---
# Ensure config is imported first if it configures logging
import config
from . import gcp_ops
from . import github_api
from . import git_ops
from . import llm_interface
from . import plan_executor
from . import firestore_ops

# --- Basic Logging Setup ---
# Assumes config.py already configured logging
logger = logging.getLogger(__name__)

# --- Flask App for Routing ---
app = Flask(__name__)

# ==============================================================================
# Security Middleware / Decorator
# ==============================================================================
# Fetch the secret ONCE during initialization or first request (cached)
_SHARED_SECRET_CACHE = None
def get_shared_secret():
    """Gets the shared secret from env vars, caching it."""
    global _SHARED_SECRET_CACHE
    if _SHARED_SECRET_CACHE is None: # Only check env var once
         secret_value = os.environ.get(config.ECKO_SHARED_SECRET_ENV_VAR)
         if not secret_value:
              logger.critical(f"CRITICAL SECURITY RISK: {config.ECKO_SHARED_SECRET_ENV_VAR} is NOT SET!")
              _SHARED_SECRET_CACHE = False # Mark as checked but not found
         else:
              logger.info(f"{config.ECKO_SHARED_SECRET_ENV_VAR} found in environment.")
              _SHARED_SECRET_CACHE = secret_value # Store the actual secret
    # Return the cached value (which might be False or the actual secret string)
    return _SHARED_SECRET_CACHE if _SHARED_SECRET_CACHE else None

def require_auth(f):
    """Decorator to check for the shared secret in the custom header."""
    @wraps(f) # Preserves original function metadata
    def decorated_function(*args, **kwargs):
        shared_secret = get_shared_secret()
        # If secret is not configured on the server, deny access
        if not shared_secret:
             logger.error("Access denied: Shared secret not configured on server.")
             # Use error_response which returns a Flask Response object
             # ===> Confirmation: error_response used for 500 <===
             return error_response("Server Configuration Error", 500)

        # ===> Confirmation: Checks request.headers.get(config.AUTH_HEADER_NAME) and shared_secret <===
        auth_header = request.headers.get(config.AUTH_HEADER_NAME)
        # Securely compare the provided header with the cached secret
        if not auth_header or not isinstance(auth_header, str) or auth_header != shared_secret:
            logger.warning(f"Unauthorized access attempt to protected endpoint: {request.path} from {request.remote_addr}.")
            # Use error_response which returns a Flask Response object
            # ===> Confirmation: error_response used for 403 <===
            return error_response("Unauthorized", 403) # Forbidden

        logger.debug(f"Authorized access to {request.path}")
        return f(*args, **kwargs) # Proceed to the original function
    return decorated_function

# ==============================================================================
# CORS Handling
# ==============================================================================
def _build_cors_preflight():
    """Builds response for CORS OPTIONS requests."""
    resp = make_response('', 204)
    # Use the configured ALLOWED_ORIGIN
    allowed_origin = config.ALLOWED_ORIGIN or '*' # Fallback to '*' ONLY if not set (should not happen due to validation)
    resp.headers.update({
        'Access-Control-Allow-Origin': allowed_origin,
        'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
        'Access-Control-Allow-Headers': f'Content-Type, {config.AUTH_HEADER_NAME}', # Allow custom header
        'Access-Control-Max-Age': '3600',
        'Access-Control-Allow-Credentials': 'true' # Needed if frontend sends credentials
    })
    return resp

def _corsify(response):
    """Adds CORS headers to actual API responses."""
    # Ensure response is a Flask Response object before adding headers
    if not isinstance(response, app.response_class):
         # Handle tuples like (body_dict, status_code)
         if isinstance(response, tuple) and len(response) == 2:
              body, code = response
              # If body isn't already JSONified (e.g., from error_response), jsonify it
              if not isinstance(body, (str, bytes)) and not hasattr(body, 'headers'):
                   response = make_response(jsonify(body), code)
              else: # Assume body is already suitable (like from error_response)
                  response = make_response(body, code)
         else: # Default to jsonifying the data with status 200 if not specified
             response = make_response(jsonify(response), 200)

    # Use the configured ALLOWED_ORIGIN
    allowed_origin = config.ALLOWED_ORIGIN or '*' # Fallback shouldn't be needed
    response.headers['Access-Control-Allow-Origin'] = allowed_origin
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

# ==============================================================================
# Error Response Helper
# ==============================================================================
def error_response(message, status_code):
    """Creates a standard JSON error response object (Flask Response)."""
    logger.error(f"Returning Error ({status_code}): {message}")
    # Ensure CORS is applied even to error responses
    response = make_response(jsonify({"error": message}), status_code)
    return _corsify(response) # Apply CORS headers here

# ==============================================================================
# Core Logic Handlers (Called by Routes)
# ==============================================================================

def _handle_modification_request(modification_request):
    """Orchestrates the code modification process."""
    logger.info(f"--- Handling Modification Request: {modification_request} ---")
    # ===> Confirmation: firestore_ops used for logging <===
    firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Processing modification: '{modification_request[:100]}...'")

    # ===> Confirmation: gcp_ops.get_cleaned_github_pat used <===
    pat, err_pat = gcp_ops.get_cleaned_github_pat()
    if err_pat:
        msg = f"PAT Error: {err_pat}"; firestore_ops.add_to_conversation_history(config.AGENT_NAME, msg)
        return {"error": msg}, 500

    response_data = {}
    status_code = 500
    try:
        # ===> Confirmation: git_ops.GitRepo context manager used <===
        with git_ops.GitRepo(pat) as repo_ctx:
            # ===> Confirmation: repo_ctx.list_files used <===
            files, err_list = repo_ctx.list_files()
            if err_list: raise RuntimeError(f"List files failed: {err_list}")

            # Read content for all tracked files
            content = {}
            read_errors = []
            for f in files:
                 # ===> Confirmation: repo_ctx.read_file used <===
                 file_content, err_read = repo_ctx.read_file(f)
                 if err_read:
                      read_errors.append(f"Error reading {f}: {err_read}")
                      content[f] = None # Mark as unreadable
                 else:
                      content[f] = file_content # Store {path: content_string}

            if read_errors:
                 logger.warning(f"Encountered errors reading some files: {read_errors}")
                 firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Warning: Could not read some files: {'; '.join(read_errors)}")

            logger.info(f"Read content/status for {len(content)} tracked files.")
            # Filter out None values before passing to LLM if necessary, or let LLM know
            readable_content = {k: v for k, v in content.items() if v is not None}


            # ===> Confirmation: llm_interface.generate_modification_plan called with readable_content <===
            firestore_ops.add_to_conversation_history(config.AGENT_NAME, "Generating modification plan...")
            plan, err_plan = llm_interface.generate_modification_plan(modification_request, readable_content)
            if err_plan: raise RuntimeError(f"Plan generation failed: {err_plan}")
            if not plan:
                msg = "AI determined no changes needed or plan was empty/invalid."; firestore_ops.add_to_conversation_history(config.AGENT_NAME, msg)
                return {"response": msg, "modification_status": "No Action"}, 200

            # ===> Confirmation: plan_executor.execute_plan called with original 'content' <===
            firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Validating and preparing plan ({len(plan)} ops)...")
            changes_map, exec_warnings_errors = plan_executor.execute_plan(plan, content) # Pass original content (with potential None values)
            if exec_warnings_errors:
                 logger.warning(f"Plan Execution Warnings/Errors: {exec_warnings_errors}")
                 firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Plan Exec Warnings: {'; '.join(exec_warnings_errors)}")
            if not changes_map:
                 msg = f"Plan execution yielded no valid changes to apply. Issues: {'; '.join(exec_warnings_errors)}"
                 firestore_ops.add_to_conversation_history(config.AGENT_NAME, msg)
                 return {"error": msg, "modification_status": "Execution Failed"}, 400

            # ===> Confirmation: repo_ctx.apply_changes called <===
            firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Applying changes to {len(changes_map)} files...")
            applied, err_apply = repo_ctx.apply_changes(changes_map)
            if err_apply: raise RuntimeError(f"Failed applying changes: {'; '.join(err_apply)}")
            if not applied: raise RuntimeError("Apply changes step wrote no files unexpectedly.")
            firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Applied locally: {applied}")

            # ===> Confirmation: repo_ctx.commit_and_push called <===
            commit_msg = f"{config.AGENT_NAME}: {modification_request[:100]}" # Use Agent name from config
            firestore_ops.add_to_conversation_history(config.AGENT_NAME,"Committing & pushing...")
            success, push_msg = repo_ctx.commit_and_push(applied, commit_msg)
            final_status = "Success" if success else "Push Failed"
            msg = f"Result: {push_msg}"
            all_warnings = exec_warnings_errors + (read_errors if read_errors else [])
            if all_warnings:
                msg += f"\nWarnings during process: {'; '.join(all_warnings)}"

            response_data = {"response": msg, "modification_status": final_status, "modified_files": applied}
            status_code = 200 if success else 500 # Internal Server Error if push fails

    except (ValueError, ConnectionError, RuntimeError, git_ops.git.GitCommandError) as e:
        logger.error(f"Modification Process Error: {e}", exc_info=True)
        msg = f"Modification Process Error: {e}"
        response_data = {"error": msg, "modification_status": "Failed"}
        status_code = 500 # Use 500 for internal processing errors
    except Exception as e:
        logger.exception("--- Unhandled Modification Exception ---")
        msg = "An unexpected internal error occurred during modification."
        response_data = {"error": msg, "modification_status": "Failed"}
        status_code = 500
    finally:
        # Context manager handles cleanup
        logger.info(f"--- Finished Modification Request ---")
        final_message = response_data.get("response", response_data.get("error", "Modification process ended."))
        firestore_ops.add_to_conversation_history(config.AGENT_NAME, final_message)

    return response_data, status_code


def _handle_logs(log_request_params):
    """Handles 'show logs' requests based on parsed parameters."""
    logger.info(f"Handling Log Request with params: {log_request_params}")
    src = log_request_params.get('source', 'backend_gcf')
    limit = log_request_params.get('limit', 50)
    analyze = log_request_params.get('analyze', False)
    user_query = log_request_params.get('query', '')

    logs_data = None
    err = None
    code = 200

    if src == 'backend_gcf':
        # ===> Confirmation: gcp_ops.get_gcf_logs called <===
        logs_data, err = gcp_ops.get_gcf_logs(limit)
    elif src in ['frontend_deploy', 'backend_deploy']:
        pat, err_pat = gcp_ops.get_cleaned_github_pat()
        if err_pat:
            err = f"PAT Error: {err_pat}"
        else:
            wf = config.FRONTEND_WORKFLOW_FILENAME if src == 'frontend_deploy' else config.BACKEND_WORKFLOW_FILENAME
            # ===> Confirmation: github_api calls for runs/url/content <===
            run, err_run = github_api.get_latest_workflow_run(pat, wf)
            if err_run or not run or 'id' not in run:
                err = err_run or "No latest run found for workflow."
                logs_data = {"status": "not_found", "message": err}
            else:
                run_id = run['id']
                log_info, err_log = github_api.get_workflow_log_url(pat, run_id)

                if err_log:
                    err = f"Could not get log URL: {err_log}"
                    logs_data = {"run_info": run, "status": "error_fetching_logs", "message": err}
                elif log_info and log_info.get('log_archive_url'):
                    archive_url = log_info.get('log_archive_url')
                    logger.info(f"Attempting to download logs from archive: {archive_url}")
                    log_content, dl_err = github_api.download_and_extract_log_content(archive_url, pat)
                    if dl_err:
                         err = f"Failed to download/extract logs: {dl_err}"
                         logs_data = {"run_info": run, "log_archive_url": archive_url, "status": "download_failed", "message": err}
                    elif log_content:
                         logger.info(f"Successfully downloaded and extracted logs for run {run_id}.")
                         logs_data = log_content # Actual log string
                    else:
                         err = "Log download returned empty content."
                         logs_data = { "run_info": run, "log_archive_url": archive_url, "status": "empty_log", "message": err}
                else:
                    err = "Log download URL not available (workflow might still be processing)."
                    logs_data = {"run_info": run, "status": "processing", "message": err}
    else:
        err = "Invalid log source specified."
        code = 400

    response_body = {}
    if analyze and not err and isinstance(logs_data, (list, str)):
        logger.info(f"Analyzing logs for query: '{user_query}'")
        log_context_for_llm = "\n".join(logs_data) if isinstance(logs_data, list) else logs_data
        # ===> Confirmation: llm_interface.analyze_log_data called <===
        analysis_body, code = llm_interface.analyze_log_data(user_query, log_context_for_llm)
        response_body = analysis_body
        firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Log Analysis: {analysis_body.get('response', analysis_body.get('error', '...'))}")
    elif err:
        response_body["error"] = f"Error fetching logs: {err}"
        if isinstance(logs_data, dict): response_body.update(logs_data)
        code = 500 if code == 200 else code
        firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Error fetching logs: {err}")
    else:
        response_body["logs"] = logs_data
        code = 200
        log_summary = f"Retrieved logs for {src}."
        if isinstance(logs_data, list): log_summary += f" ({len(logs_data)} lines)"
        elif isinstance(logs_data, str): log_summary += f" ({len(logs_data)} chars)"
        elif isinstance(logs_data, dict): log_summary += f" (Status: {logs_data.get('status', '?')})"
        firestore_ops.add_to_conversation_history(config.AGENT_NAME, log_summary)

    return response_body, code


def _handle_deploy(target):
    """Handles 'deploy' requests for a specific target."""
    logger.info(f"Handling Deploy Request for target: {target}")
    wf = None
    if target == 'backend': wf = config.BACKEND_WORKFLOW_FILENAME
    elif target == 'frontend': wf = config.FRONTEND_WORKFLOW_FILENAME
    else:
        msg = "Invalid deployment target specified."; firestore_ops.add_to_conversation_history(config.AGENT_NAME, msg) # Corrected add_to...
        return {"error": msg}, 400

    pat, err_pat = gcp_ops.get_cleaned_github_pat()
    if err_pat:
        msg = f"PAT Error: {err_pat}"; firestore_ops.add_to_conversation_history(config.AGENT_NAME, msg) # Corrected add_to...
        return {"error": msg}, 500

    # ===> Confirmation: github_api.trigger_workflow_dispatch called <===
    success, msg = github_api.trigger_workflow_dispatch(pat, wf)
    firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"Deploy trigger ({target}): {msg}") # Corrected add_to...
    status_code = 202 if success else 500 # 202 Accepted
    return {"message": msg, "deployment_trigger_status": "Success" if success else "Failed"}, status_code


def _handle_status(target):
    """Handles 'status' requests for a specific target."""
    logger.info(f"Handling Status Request for target: {target}")
    wf = None
    if target == 'backend': wf = config.BACKEND_WORKFLOW_FILENAME
    elif target == 'frontend': wf = config.FRONTEND_WORKFLOW_FILENAME
    else: return {"error": "Invalid status target specified."}, 400

    pat, err_pat = gcp_ops.get_cleaned_github_pat()
    if err_pat: return {"error": f"PAT Error: {err_pat}"}, 500

    # ===> Confirmation: github_api.get_latest_workflow_run called <===
    status_info, err_msg = github_api.get_latest_workflow_run(pat, wf)
    response = {}
    code = 200
    if err_msg:
        response["error"] = f"Error fetching status for {target}: {err_msg}"
        code = 500
        response["status_details"] = {"status": "error", "error_message": err_msg}
    elif status_info:
        response["message"] = f"{target} status: {status_info.get('status')} ({status_info.get('conclusion', 'N/A')})"
        response["status_details"] = status_info
    else:
        response["message"] = f"No runs found for {target} workflow."
        response["status_details"] = {"status": "not_found"}
        code = 404

    return response, code

# ==============================================================================
# Flask Routes (Now ALL require auth)
# ==============================================================================
# ===> Confirmation: @require_auth applied to all routes below <===

@app.route('/ecko', methods=['POST', 'OPTIONS'])
@require_auth
def ecko_chat_route():
    """Handles chat and commands (requires auth)."""
    if request.method == 'OPTIONS': return _build_cors_preflight()

    body, code = {"error": "Request failed"}, 500
    try:
        req_json = request.get_json(silent=True)
        if not req_json or 'message' not in req_json:
            return error_response("Missing 'message' in request body", 400)

        msg = req_json['message'].strip()
        logger.info(f"/ecko authenticated request: '{msg[:100]}...'")
        firestore_ops.add_to_conversation_history("User", msg)

        # ===> Confirmation: Regex uses prefixes from config <===
        modify_match = re.match(rf"{config.MODIFY_CODE_PREFIX.replace(':','').strip()}\s*:(.*)", msg, re.IGNORECASE | re.DOTALL)
        legacy_modify_match = re.match(rf"{config.LEGACY_MODIFY_PREFIX.replace(':','').strip()}\s*:(.*)", msg, re.IGNORECASE | re.DOTALL)
        log_match = re.match(rf"{config.LOG_QUERY_PREFIX.replace(':','').strip()}\s*:(.*)", msg, re.IGNORECASE | re.DOTALL)
        deploy_match = re.match(rf"{config.DEPLOY_PREFIX.replace(':','').strip()}\s*:(.*)", msg, re.IGNORECASE | re.DOTALL)
        status_match = re.match(rf"{config.STATUS_PREFIX.replace(':','').strip()}\s*:(.*)", msg, re.IGNORECASE | re.DOTALL)

        # ===> Confirmation: Handlers called correctly in if/elif <===
        if modify_match:
            command_details = modify_match.group(1).strip()
            body, code = _handle_modification_request(command_details)
        elif legacy_modify_match:
             command_details = legacy_modify_match.group(1).strip()
             body, code = _handle_modification_request(command_details)
        elif log_match:
            log_query = log_match.group(1).strip()
            params = {'query': log_query, 'source': 'backend_gcf', 'limit': 50, 'analyze': False}
            if "frontend deploy" in log_query.lower(): params['source'] = "frontend_deploy"
            elif "backend deploy" in log_query.lower(): params['source'] = "backend_deploy"
            elif "gcf" in log_query.lower(): params['source'] = "backend_gcf"
            limit_re = re.search(r'limit=(\d+)', log_query, re.IGNORECASE)
            if limit_re: params['limit'] = min(max(1, int(limit_re.group(1))), 200)
            if "analyze" in log_query.lower(): params['analyze'] = True
            body, code = _handle_logs(params)
        elif deploy_match:
            deploy_target_str = deploy_match.group(1).strip().lower()
            target = 'backend' if 'backend' in deploy_target_str else 'frontend' if 'frontend' in deploy_target_str else None
            if target: body, code = _handle_deploy(target)
            else: body, code = {"error": "Deploy target unclear ('backend' or 'frontend')."}, 400
        elif status_match:
            status_target_str = status_match.group(1).strip().lower()
            target = 'backend' if 'backend' in status_target_str else 'frontend' if 'frontend' in status_target_str else None
            if target: body, code = _handle_status(target)
            else: body, code = {"error": "Status target unclear ('backend' or 'frontend')."}, 400
        else:
            # Normal Chat - generate response
            body, code = llm_interface.generate_chat_response(firestore_ops.get_conversation_history(), msg)
            if code == 200 and "response" in body:
                 firestore_ops.add_to_conversation_history(config.AGENT_NAME, body.get("response", "(empty AI response)"))
            elif "error" in body:
                 firestore_ops.add_to_conversation_history(config.AGENT_NAME, f"AI Error: {body['error']}")

    except Exception as e:
        logger.exception("Unhandled /ecko error")
        body, code = {"error": "Internal server error."}, 500
        firestore_ops.add_to_conversation_history("System", f"ERROR processing request: {e}")

    if not isinstance(body, dict):
        logger.warning(f"Handler for /ecko returned non-dict type: {type(body)}. Wrapping.")
        body = {"response": str(body)}

    return _corsify(make_response(jsonify(body), code))


@app.route('/list_files', methods=['GET', 'OPTIONS'])
@require_auth
def list_files_route():
    if request.method == 'OPTIONS': return _build_cors_preflight()
    pat, err_pat = gcp_ops.get_cleaned_github_pat()
    if err_pat: return error_response(f"PAT Error: {err_pat}", 500)
    body, code = {"error": "Failed list files"}, 500
    try:
        with git_ops.GitRepo(pat) as repo_ctx:
            files, err_list = repo_ctx.list_files()
            if err_list:
                body, code = {"error": err_list}, 500
            else:
                body, code = {"files": files}, 200
    except (ValueError, ConnectionError, RuntimeError, git_ops.git.GitCommandError) as e:
        logger.error(f"List files error: {e}", exc_info=True)
        body, code = {"error": f"Error listing files: {e}"}, 500
    except Exception as e:
        logger.exception("Unhandled list_files error")
        body, code = {"error": f"Unexpected list files error: {e}"}, 500
    return _corsify(make_response(jsonify(body), code))


@app.route('/get_file_content', methods=['GET', 'OPTIONS'])
@require_auth
def get_file_route():
    if request.method == 'OPTIONS': return _build_cors_preflight()
    fpath = request.args.get('path')
    if not fpath: return error_response("Missing 'path' query parameter", 400)

    pat, err_pat = gcp_ops.get_cleaned_github_pat()
    if err_pat: return error_response(f"PAT Error: {err_pat}", 500)

    body, code = {"error": "Failed get content"}, 500
    try:
        with git_ops.GitRepo(pat) as repo_ctx:
            content, err_read = repo_ctx.read_file(fpath)
            if err_read:
                 if "Security risk" in err_read: code = 403
                 elif "not found" in err_read.lower(): code = 404
                 else: code = 500
                 body = {"error": err_read}
            else:
                 body, code = {"content": content}, 200
    except (ValueError, ConnectionError, RuntimeError, git_ops.git.GitCommandError) as e:
        logger.error(f"Get file content error: {e}", exc_info=True)
        if isinstance(e, ValueError) and "Security risk" in str(e): code = 403
        else: code = 500
        body = {"error": f"Error getting file content: {e}"}
    except Exception as e:
        logger.exception("Unhandled get_file_content error")
        body = {"error": f"Unexpected get content error: {e}"}; code = 500
    return _corsify(make_response(jsonify(body), code))


@app.route('/get_logs', methods=['GET', 'OPTIONS'])
@require_auth
def get_logs_route():
    if request.method == 'OPTIONS': return _build_cors_preflight()
    params = {
        'source': request.args.get('source', 'backend_gcf'),
        'limit': min(max(1, request.args.get('limit', default=50, type=int)), 200),
        'analyze': request.args.get('analyze', default=False, type=bool),
        'query': request.args.get('query', '')
    }
    response_body, status_code = _handle_logs(params)
    return _corsify(make_response(jsonify(response_body), status_code))


@app.route('/trigger_deploy', methods=['POST', 'OPTIONS'])
@require_auth
def trigger_deploy_route():
    if request.method == 'OPTIONS': return _build_cors_preflight()
    req_json = request.get_json(silent=True)
    target = req_json.get('target') if req_json else None
    if not target or target not in ['backend', 'frontend']:
         return error_response("Invalid or missing 'target' in request body ('backend' or 'frontend')", 400)
    response_body, status_code = _handle_deploy(target)
    return _corsify(make_response(jsonify(response_body), status_code))


@app.route('/deployment_status', methods=['GET', 'OPTIONS'])
@require_auth
def status_route():
    if request.method == 'OPTIONS': return _build_cors_preflight()
    target = request.args.get('target')
    if not target or target not in ['backend', 'frontend']:
         return error_response("Invalid or missing 'target' query parameter ('backend' or 'frontend')", 400)
    response_body, status_code = _handle_status(target)
    return _corsify(make_response(jsonify(response_body), status_code))

# ==============================================================================
# SSE Route (Removed)
# ==============================================================================
# ===> Action: Removed SSE route and related functions/imports <===

# ==============================================================================
# GCF Entry Point
# ==============================================================================
@functions_framework.http
def ecko_main(request):
    """GCF entry point delegating to Flask app."""
    logger.info(f"Incoming Request: {request.method} {request.path} | From: {request.remote_addr} | Agent: {request.user_agent.string[:50]}...")

    # ===> Confirmation: Special OPTIONS handling <===
    if request.method == 'OPTIONS':
        return _build_cors_preflight()

    # ===> Confirmation: Use Flask app context and dispatch <===
    with app.request_context(request.environ):
        try:
            response = app.full_dispatch_request()
        except Exception as e:
            logger.exception("Unhandled exception during Flask request dispatch.")
            response = error_response("Internal Server Error during request dispatch.", 500)

        # CORS is applied within handlers or error_response
        return response

# --- Local Development Runner ---
# ===> Confirmation: Local runner exists <===
if __name__ == '__main__':
    logger.info("Starting Flask development server for Ecko backend...")
    if not os.environ.get(config.ECKO_SHARED_SECRET_ENV_VAR):
         logger.warning(f"WARNING: Environment variable {config.ECKO_SHARED_SECRET_ENV_VAR} is not set. API endpoints will fail authorization.")
    if not os.environ.get("ALLOWED_ORIGIN"):
         logger.warning("WARNING: Environment variable ALLOWED_ORIGIN is not set. CORS may fail depending on browser.")

    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=True)