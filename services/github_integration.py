from __future__ import annotations

import base64
import difflib
import json
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import jwt
from github import Auth, Github


PROMPTDRIFT_MANAGED_MARKER = "<!-- promptdrift:managed-comment -->"
DRIFTGUARD_MANAGED_MARKER = "<!-- driftguard:managed-comment -->"
PROMPTDRIFT_ESCALATION_LABEL = "promptdrift: escalate-before-merge"
LEGACY_DRIFTGUARD_ESCALATION_LABEL = "driftguard: escalate-before-merge"
DRIFTGUARD_ESCALATION_LABEL = "vipari: escalate-before-merge"
DRIFTGUARD_ESCALATION_LABEL_COLOR = "B60205"
DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION = "Vipari recommends escalation before merge"
LEGACY_ESCALATION_LABELS = (PROMPTDRIFT_ESCALATION_LABEL, LEGACY_DRIFTGUARD_ESCALATION_LABEL)
JWT_ISSUED_AT_SKEW_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60


@dataclass(frozen=True)
class GithubReactionRecord:
    reaction_id: str
    content: str
    user_id: str | None
    user_login: str | None
    created_at: float | None
    target_kind: str
    target_id: int


def _resolve_private_key_path(private_key_path: str) -> Path:
    candidate = Path(private_key_path).expanduser()
    if candidate.is_absolute():
        return candidate

    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    project_root_candidate = (Path(__file__).resolve().parent.parent / candidate).resolve()
    if project_root_candidate.exists():
        return project_root_candidate

    return cwd_candidate


def _load_private_key(private_key_path: str, private_key: str | None = None) -> str:
    if private_key:
        return private_key.replace("\\n", "\n")

    if not private_key_path:
        raise RuntimeError("A GitHub App private key path or inline private key must be configured.")

    resolved_private_key_path = _resolve_private_key_path(private_key_path)
    with open(resolved_private_key_path, "r", encoding="utf-8") as file_handle:
        return file_handle.read()


def generate_jwt(app_id: str, private_key_path: str, private_key: str | None = None) -> str:
    resolved_private_key = _load_private_key(private_key_path, private_key)
    now = int(time.time())
    payload = {
        "iat": now - JWT_ISSUED_AT_SKEW_SECONDS,
        "exp": now + JWT_LIFETIME_SECONDS,
        "iss": str(app_id),
    }
    token = jwt.encode(payload, resolved_private_key, algorithm="RS256")
    return token.decode("utf-8") if isinstance(token, bytes) else token


def get_installation_token(jwt_token: str, installation_id: int) -> str:
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {jwt_token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req) as response:
        data = json.load(response)
    token = data.get("token")
    if not token:
        raise RuntimeError("GitHub installation token response did not include a token.")
    return token


def fetch_pr_diff(repo_full: str, pr_number: int, token: str) -> str:
    diff_url = f"https://api.github.com/repos/{repo_full}/pulls/{pr_number}"
    req = urllib.request.Request(diff_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3.diff")
    with urllib.request.urlopen(req) as response:
        return response.read().decode("utf-8")


def fetch_compare_diff(repo_full: str, base_sha: str, head_sha: str, token: str) -> str:
    diff_url = f"https://api.github.com/repos/{repo_full}/compare/{base_sha}...{head_sha}"
    req = urllib.request.Request(diff_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.v3.diff")
    with urllib.request.urlopen(req) as response:
        return response.read().decode("utf-8")


def fetch_commit_pair_diff(repo_full: str, base_sha: str, head_sha: str, token: str) -> str:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)

    base_tree = repo.get_git_tree(base_sha, recursive=True)
    head_tree = repo.get_git_tree(head_sha, recursive=True)
    base_blobs = {entry.path: entry.sha for entry in base_tree.tree if entry.type == "blob"}
    head_blobs = {entry.path: entry.sha for entry in head_tree.tree if entry.type == "blob"}

    changed_paths = sorted(path for path in set(base_blobs) | set(head_blobs) if base_blobs.get(path) != head_blobs.get(path))
    rendered_diffs = []

    for path in changed_paths:
        previous_sha = base_blobs.get(path)
        current_sha = head_blobs.get(path)
        previous_text = _decode_blob_text(repo, previous_sha) if previous_sha else None
        current_text = _decode_blob_text(repo, current_sha) if current_sha else None
        rendered = _render_unified_diff(path, previous_text, current_text)
        if rendered:
            rendered_diffs.append(rendered)

    return "\n".join(rendered_diffs)


def _decode_blob_text(repo: object, blob_sha: str) -> str:
    blob = repo.get_git_blob(blob_sha)
    return base64.b64decode(blob.content).decode("utf-8", errors="replace")


def _render_unified_diff(path: str, previous_text: str | None, current_text: str | None) -> str:
    previous_exists = previous_text is not None
    current_exists = current_text is not None
    previous_lines = [] if previous_text is None else previous_text.splitlines()
    current_lines = [] if current_text is None else current_text.splitlines()

    if previous_exists and current_exists and previous_lines == current_lines:
        return ""

    header_lines = [f"diff --git a/{path} b/{path}"]
    if not previous_exists:
        header_lines.append("new file mode 100644")
    elif not current_exists:
        header_lines.append("deleted file mode 100644")

    diff_lines = list(
        difflib.unified_diff(
            previous_lines,
            current_lines,
            fromfile="/dev/null" if not previous_exists else f"a/{path}",
            tofile="/dev/null" if not current_exists else f"b/{path}",
            lineterm="",
        )
    )
    return "\n".join(header_lines + diff_lines)


def fetch_file_content(repo_full: str, file_path: str, token: str, *, ref: str) -> str:
    encoded_path = urllib.parse.quote(file_path, safe="/")
    encoded_ref = urllib.parse.quote(ref, safe="")
    url = f"https://api.github.com/repos/{repo_full}/contents/{encoded_path}?ref={encoded_ref}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github.raw")
    with urllib.request.urlopen(req) as response:
        return response.read().decode("utf-8", errors="replace")


def get_repo_default_branch(repo_full: str, token: str) -> str:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    return repo.default_branch


def list_repository_files(repo_full: str, token: str, *, ref: str | None = None) -> list[str]:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    tree_ref = ref or repo.default_branch
    tree = repo.get_git_tree(tree_ref, recursive=True)
    return sorted(entry.path for entry in tree.tree if entry.type == "blob")


def list_file_commits(repo_full: str, file_path: str, token: str, *, branch: str | None = None, limit: int = 25) -> list[str]:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    commits = repo.get_commits(path=file_path, sha=branch)
    commit_shas: list[str] = []
    for index, commit in enumerate(commits):
        if index >= limit:
            break
        commit_shas.append(commit.sha)
    return commit_shas


def upsert_pr_comment(repo_full: str, pr_number: int, token: str, body: str, *, existing_comment_id: int | None = None) -> int:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    managed_body = _build_managed_comment_body(body)
    existing_comment = None

    if existing_comment_id is not None:
        for comment in pr.get_issue_comments():
            if comment.id != existing_comment_id:
                continue
            existing_comment = comment
            break

    if existing_comment is not None:
        existing_comment.edit(managed_body)
        return existing_comment.id

    created_comment = pr.create_issue_comment(managed_body)
    return created_comment.id


def create_pr_review(repo_full: str, pr_number: int, token: str, body: str, *, event: str) -> int:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    managed_body = _build_managed_comment_body(body)
    created_review = pr.create_review(body=managed_body, event=event)
    return created_review.id


def list_pr_comment_reactions(repo_full: str, pr_number: int, token: str, *, comment_id: int) -> list[GithubReactionRecord]:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    for comment in pr.get_issue_comments():
        if comment.id != comment_id:
            continue
        return [_reaction_record_from_github(reaction, target_kind="issue_comment", target_id=comment_id) for reaction in comment.get_reactions()]
    return []


def list_pr_review_reactions(repo_full: str, pr_number: int, token: str, *, review_id: int) -> list[GithubReactionRecord]:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    for review in pr.get_reviews():
        if review.id != review_id:
            continue
        return [_reaction_record_from_github(reaction, target_kind="review", target_id=review_id) for reaction in review.get_reactions()]
    return []


def ensure_pr_label(
    repo_full: str,
    pr_number: int,
    token: str,
    *,
    label_name: str = DRIFTGUARD_ESCALATION_LABEL,
    label_color: str = DRIFTGUARD_ESCALATION_LABEL_COLOR,
    label_description: str = DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION,
) -> bool:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    issue = repo.get_issue(number=pr_number)

    repo_labels = {label.name for label in repo.get_labels()}
    if label_name not in repo_labels:
        repo.create_label(label_name, label_color, label_description)

    issue_labels = {label.name for label in issue.get_labels()}
    if label_name in issue_labels:
        return False

    legacy_issue_labels = [candidate for candidate in LEGACY_ESCALATION_LABELS if candidate in issue_labels]
    if legacy_issue_labels:
        issue.remove_from_labels(*legacy_issue_labels)

    issue.add_to_labels(label_name)
    return True


def remove_pr_label(
    repo_full: str,
    pr_number: int,
    token: str,
    *,
    label_name: str = DRIFTGUARD_ESCALATION_LABEL,
) -> bool:
    github_client = Github(auth=Auth.Token(token))
    repo = github_client.get_repo(repo_full)
    issue = repo.get_issue(number=pr_number)

    issue_labels = {label.name for label in issue.get_labels()}
    matching_labels = [candidate for candidate in (label_name, *LEGACY_ESCALATION_LABELS) if candidate in issue_labels]
    if not matching_labels:
        return False

    issue.remove_from_labels(*matching_labels)
    return True


def sync_pr_label(
    repo_full: str,
    pr_number: int,
    token: str,
    *,
    should_have_label: bool,
    label_name: str = DRIFTGUARD_ESCALATION_LABEL,
    label_color: str = DRIFTGUARD_ESCALATION_LABEL_COLOR,
    label_description: str = DRIFTGUARD_ESCALATION_LABEL_DESCRIPTION,
) -> bool:
    if should_have_label:
        return ensure_pr_label(
            repo_full,
            pr_number,
            token,
            label_name=label_name,
            label_color=label_color,
            label_description=label_description,
        )
    return remove_pr_label(
        repo_full,
        pr_number,
        token,
        label_name=label_name,
    )


def _build_managed_comment_body(body: str) -> str:
    for marker in (DRIFTGUARD_MANAGED_MARKER, PROMPTDRIFT_MANAGED_MARKER):
        if body.startswith(marker):
            return body.replace(marker, DRIFTGUARD_MANAGED_MARKER, 1)
    return f"{DRIFTGUARD_MANAGED_MARKER}\n{body}"


def _reaction_record_from_github(reaction: object, *, target_kind: str, target_id: int) -> GithubReactionRecord:
    user = getattr(reaction, "user", None)
    created_at = getattr(reaction, "created_at", None)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_value = created_at.timestamp()
    else:
        created_at_value = None
    return GithubReactionRecord(
        reaction_id=str(getattr(reaction, "id", "")),
        content=str(getattr(reaction, "content", "")),
        user_id=(str(getattr(user, "id", "")) or None),
        user_login=(str(getattr(user, "login", "")) or None),
        created_at=created_at_value,
        target_kind=target_kind,
        target_id=target_id,
    )
