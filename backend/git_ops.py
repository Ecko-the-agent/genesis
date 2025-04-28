# backend/git_ops.py
import git # GitPython library
import os
import logging
import shutil # For robust directory removal
import tempfile # For creating temporary directories
from pathlib import Path # For easier path manipulation
import config # Use centralized config

logger = logging.getLogger(__name__)

class GitRepo:
    """
    Context manager for handling a temporary Git repository clone.
    Ensures cleanup even if errors occur during operations.
    Uses shallow clone for efficiency.
    """
    def __init__(self, pat):
        """
        Initializes the GitRepo context manager.

        Args:
            pat (str): The GitHub Personal Access Token (PAT).
        """
        if not pat:
            raise ValueError("GitHub PAT is required to initialize GitRepo.")
        self._pat = pat
        # Create a unique temporary directory path upon instantiation
        self._repo_path_obj = Path(tempfile.mkdtemp(prefix="ecko_git_"))
        self._repo = None # GitPython Repo object, initialized in __enter__
        self._host = "github.com" # Assuming GitHub.com
        logger.info(f"Initialized GitRepo context for temp path: {self._repo_path_obj}")

    def __enter__(self):
        """
        Clones the repository (shallowly) when entering the 'with' block.
        Configures git author details.

        Returns:
            GitRepo: The instance itself.

        Raises:
            ConnectionError: If cloning fails due to network, PAT, or repo issues.
            Exception: For other unexpected errors during setup.
        """
        repo_url = config.GITHUB_REPO_URL_TEMPLATE.format(
            pat=self._pat,
            owner=config.GITHUB_REPO_OWNER,
            repo=config.GITHUB_REPO_NAME,
            host=self._host
        )
        # Mask PAT only for logging, not the actual URL used for cloning
        masked_url = repo_url.replace(self._pat, "***PAT***")

        try:
            logger.info(f"Cloning repository {masked_url} (shallow, branch: {config.GITHUB_MAIN_BRANCH}) into {self._repo_path_obj}...")
            # Clone the specific branch defined in config using SHALLOW CLONE (depth=1)
            self._repo = git.Repo.clone_from(
                repo_url,
                self._repo_path_obj,
                branch=config.GITHUB_MAIN_BRANCH,
                depth=1 # Perform a shallow clone
            )
            logger.info(f"Repository cloned successfully (shallow) to {self.path}")

            # Configure author details for subsequent commits within this context
            with self._repo.config_writer() as cw:
                cw.set_value("user", "name", config.COMMIT_AUTHOR_NAME).release()
                cw.set_value("user", "email", config.COMMIT_AUTHOR_EMAIL).release()
            logger.info(f"Git author configured as '{config.COMMIT_AUTHOR_NAME} <{config.COMMIT_AUTHOR_EMAIL}>'")

            return self # Return the instance for use in the 'with' block

        except git.GitCommandError as e:
            logger.error(f"Git clone command failed for {masked_url}: {e}", exc_info=True)
            stderr_output = str(getattr(e, 'stderr', 'No stderr available')).strip()
            self._cleanup() # Attempt cleanup before raising
            raise ConnectionError(f"Failed to clone repository. Check URL, PAT permissions, and branch name. Error: {e}. Stderr: {stderr_output}")
        except Exception as e:
            logger.error(f"Unexpected error during git clone setup: {e}", exc_info=True)
            self._cleanup() # Attempt cleanup
            raise # Re-raise the original exception

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Cleans up the temporary repository directory when exiting the 'with' block.
        """
        self._cleanup()

    def _cleanup(self):
        """Safely removes the temporary repository directory."""
        if self._repo_path_obj and self._repo_path_obj.exists():
            path_str = str(self._repo_path_obj) # Get path before resetting instance var
            logger.info(f"Cleaning up temporary repository directory: {path_str}")
            try:
                shutil.rmtree(path_str, ignore_errors=True)
                logger.info(f"Successfully cleaned up {path_str}.")
            except Exception as e:
                logger.error(f"Error during cleanup of {path_str}: {e}", exc_info=True)
        self._repo_path_obj = None
        self._repo = None

    @property
    def path(self):
        """Returns the absolute path to the temporary repository clone."""
        if not self._repo_path_obj:
            raise RuntimeError("Repository path is not available (outside context or clone failed).")
        return str(self._repo_path_obj)

    @property
    def git_repo(self):
        """Returns the GitPython Repo object."""
        if not self._repo:
             raise RuntimeError("Git repository object is not available (outside context or clone failed).")
        return self._repo

    def list_files(self):
        """
        Lists all files tracked by Git in the repository using 'git ls-files'.
        Returns paths in POSIX format, sorted alphabetically.

        Returns:
            tuple: (list of relative file paths, None) on success,
                   (None, error message string) on failure.
        """
        if not self.git_repo: return None, "Repository object is not available."
        logger.info(f"Listing tracked files in repository at {self.path} using 'git ls-files'")
        try:
            # Execute 'git ls-files' command via GitPython
            tracked_files_raw = self.git_repo.git.ls_files().splitlines()

            # Ensure paths are in POSIX format (though ls-files usually outputs this)
            posix_paths = [Path(p).as_posix() for p in tracked_files_raw]

            posix_paths.sort() # Ensure consistent order
            logger.info(f"Found {len(posix_paths)} tracked files.")
            return posix_paths, None
        except git.GitCommandError as e:
             stderr_output = str(getattr(e, 'stderr', 'N/A')).strip()
             logger.error(f"Git command error during ls-files: {e}. Stderr: {stderr_output}", exc_info=True)
             return None, f"Error listing tracked files: {e}. Stderr: {stderr_output}"
        except Exception as e:
            logger.error(f"Error listing tracked files in {self.path}: {e}", exc_info=True)
            return None, f"Error listing tracked files: {e}"

    def read_file(self, relative_path_str):
        """
        Reads the content of a specific file within the repository clone.
        Includes path traversal checks.

        Args:
            relative_path_str (str): The relative path to the file from the repo root.

        Returns:
            tuple: (file content string, None) on success,
                   (None, error message string) on failure (e.g., not found, read error, security).
        """
        if not self.path: return None, "Repository path is not available."
        try:
            repo_root_resolved = Path(self.path).resolve()
            # Ensure relative_path_str is treated as relative
            full_path = (repo_root_resolved / relative_path_str).resolve()

            # --- Path Traversal Check ---
            if not full_path.is_relative_to(repo_root_resolved):
                raise ValueError(f"Security risk: Path traversal attempt for '{relative_path_str}'")

            logger.debug(f"Reading file: {relative_path_str} (resolved: {full_path})")
            if not full_path.is_file():
                 # Check if it exists in the git index even if not on disk (less likely with shallow clone)
                 try:
                      self.git_repo.git.show(f'HEAD:{relative_path_str}')
                      # If it exists in HEAD but not locally, log a warning
                      logger.warning(f"File '{relative_path_str}' exists in HEAD but not in local checkout (possibly due to sparse checkout or clone issue). Cannot read.")
                      return None, f"File not found locally: {relative_path_str}"
                 except git.GitCommandError:
                      # It doesn't exist in HEAD either
                      return None, f"File not found: {relative_path_str}"


            # Read the file content using UTF-8 encoding
            content = full_path.read_text(encoding='utf-8')
            return content, None
        except FileNotFoundError: # Should be caught above, but as fallback
            logger.warning(f"File not found during read (fallback): {relative_path_str}")
            return None, f"File not found: {relative_path_str}"
        except ValueError as ve: # Catch security error specifically
             logger.error(f"Security error reading file '{relative_path_str}': {ve}")
             return None, str(ve)
        except Exception as e:
            logger.error(f"Error reading file '{relative_path_str}': {e}", exc_info=True)
            return None, f"Error reading file '{relative_path_str}': {e}"

    def apply_changes(self, changes_map):
        """
        Writes the provided new content to the specified files in the local clone.
        Handles file creation if the path doesn't exist. Includes path traversal checks.

        Args:
            changes_map (dict): Dictionary mapping relative file paths (str)
                                to their new full content (str).

        Returns:
            tuple: (list of successfully written relative file paths,
                    list of error message strings).
        """
        if not self.path: return [], ["Repository path is not available."]
        applied_files = []
        errors = []
        logger.info(f"Applying changes to {len(changes_map)} files locally.")
        repo_root_resolved = Path(self.path).resolve()

        for rel_path, new_content in changes_map.items():
            if new_content is None:
                logger.warning(f"Skipping apply for '{rel_path}' due to None content.")
                errors.append(f"Invalid content (None) provided for '{rel_path}'")
                continue
            try:
                # Resolve and validate path
                # Ensure rel_path is treated as relative
                full_path = (repo_root_resolved / rel_path).resolve()

                # --- Path Traversal Check ---
                if not full_path.is_relative_to(repo_root_resolved):
                     raise ValueError(f"Security risk: Write attempt outside repo for '{rel_path}'")

                logger.info(f"Writing changes to file: {rel_path}")
                # Ensure parent directory exists before writing
                full_path.parent.mkdir(parents=True, exist_ok=True)
                # Write content, enforcing LF line endings for consistency
                full_path.write_text(new_content, encoding='utf-8', newline='\n')
                # Ensure path uses POSIX separators for adding to commit list
                applied_files.append(Path(rel_path).as_posix())
                # logger.debug(f"Successfully wrote {len(new_content)} bytes to {rel_path}")

            except ValueError as ve: # Catch security error
                 logger.error(f"Security error applying changes: {ve}")
                 errors.append(str(ve))
            except Exception as e:
                logger.error(f"Error writing file '{rel_path}': {e}", exc_info=True)
                errors.append(f"Write error for '{rel_path}': {e}")

        logger.info(f"Finished applying changes. Applied: {len(applied_files)}, Errors: {len(errors)}.")
        return applied_files, errors

    def commit_and_push(self, files_to_commit, commit_message):
        """
        Stages the specified files, commits them, and pushes to the remote main branch.

        Args:
            files_to_commit (list): List of relative file paths (str, POSIX format) to stage and commit.
            commit_message (str): The commit message.

        Returns:
            tuple: (bool indicating success, str message detailing outcome).
        """
        if not self.git_repo: return False, "Repository object is not available."
        if not files_to_commit:
            logger.info("No files provided to commit_and_push. Nothing to do.")
            return True, "No files specified to commit."

        try:
            logger.info(f"Staging {len(files_to_commit)} files: {files_to_commit}")
            # Stage files relative to the repository root using POSIX paths
            self.git_repo.index.add(files_to_commit)

            # Check if staging resulted in actual changes compared to HEAD
            # Use --quiet to avoid output, check exit code or diff content
            if not self.git_repo.index.diff("HEAD"):
                 # Check working tree changes as well, in case add didn't pick up something unexpected
                 if not self.git_repo.is_dirty(index=False, working_tree=True, untracked_files=False):
                     logger.info("Staging complete, but no effective changes detected compared to HEAD. Commit skipped.")
                     return True, "No effective file changes detected."
                 else:
                      logger.warning("Index diff is empty, but working tree is dirty. Proceeding with commit.")


            # Commit the staged changes (author already configured)
            logger.info(f"Committing {len(files_to_commit)} files with message: '{commit_message}'")
            self.git_repo.index.commit(commit_message)

            # Push the commit to the remote repository
            logger.info(f"Pushing commit to origin/{config.GITHUB_MAIN_BRANCH}...")
            origin = self.git_repo.remote(name='origin')
            push_info_list = origin.push(refspec=f'{config.GITHUB_MAIN_BRANCH}:{config.GITHUB_MAIN_BRANCH}')

            # Validate push results carefully
            push_errors = []
            push_summaries = []
            for info in push_info_list:
                 summary_log = f"Push summary for ref '{info.remote_ref_string or ''}': {info.summary} (Flags: {info.flags})"
                 push_summaries.append(summary_log)
                 # Check for specific error flags
                 if info.flags & (git.PushInfo.ERROR | git.PushInfo.REJECTED | git.PushInfo.REMOTE_FAILURE):
                     error_summary = f"Push Error/Rejection: {info.summary} (Flags: {info.flags})"
                     logger.error(error_summary)
                     push_errors.append(error_summary)
                 else:
                     logger.info(summary_log) # Log success summaries too

            if push_errors:
                 # Construct a user-friendly error message
                 error_detail = "Push failed: " + "; ".join(push_errors)
                 full_error_summary_lower = error_detail.lower()
                 if "non-fast-forward" in full_error_summary_lower:
                      error_detail += " (Hint: Remote branch has changes. Manual intervention might be needed.)"
                 elif "permission denied" in full_error_summary_lower or "authentication failed" in full_error_summary_lower:
                      error_detail += " (Hint: Check repository permissions or PAT validity/scopes.)"
                 elif "could not resolve host" in full_error_summary_lower:
                      error_detail += " (Hint: Network connectivity issue?)"
                 # Avoid resetting automatically, too risky
                 logger.error(f"Push command failed. Errors: {push_errors}")
                 return False, error_detail

            logger.info("Commit successfully pushed.")
            return True, "Changes committed and pushed successfully."

        except git.GitCommandError as e:
            stderr_output = str(getattr(e, 'stderr', 'N/A')).strip()
            logger.error(f"Git command error during commit/push: {e}. Stderr: {stderr_output}", exc_info=True)
            return False, f"Git command failed: {e}. Stderr: {stderr_output}"
        except Exception as e:
            logger.error(f"Unexpected error during commit/push: {e}", exc_info=True)
            return False, f"Unexpected error during commit/push: {e}"