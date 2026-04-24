import io
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services.github_provisioning import build_github_app_install_url, get_live_github_install_url, sync_installation_repositories


def test_build_github_app_install_url_includes_state():
    url = build_github_app_install_url(app_slug="driftguard-app", state="12")
    assert url == "https://github.com/apps/driftguard-app/installations/new?state=12"


def test_get_live_github_install_url_resolves_slug_from_github_api():
    with patch("services.github_provisioning.generate_jwt", return_value="jwt"), patch(
        "services.github_provisioning.urllib.request.urlopen",
        return_value=io.StringIO(json.dumps({"slug": "driftguard-app"})),
    ):
        url = get_live_github_install_url("123", "private-key.pem", "inline-key", state="5")

    assert url == "https://github.com/apps/driftguard-app/installations/new?state=5"


def test_sync_installation_repositories_returns_installation_and_repos():
    responses = [
        io.StringIO(json.dumps({"target_type": "Organization", "account": {"login": "doria90", "type": "Organization", "id": 77}})),
        io.StringIO(json.dumps({"repositories": [{"id": 1, "full_name": "doria90/dummyAI", "default_branch": "main", "private": True}]})),
    ]

    def _urlopen(_request):
        return responses.pop(0)

    with patch("services.github_provisioning.generate_jwt", return_value="jwt"), patch(
        "services.github_provisioning.get_installation_token", return_value="installation-token"
    ), patch("services.github_provisioning.urllib.request.urlopen", side_effect=_urlopen):
        installation, repos = sync_installation_repositories(
            app_id="123",
            private_key_path="private-key.pem",
            private_key="inline-key",
            installation_id=999,
        )

    assert installation["account"]["login"] == "doria90"
    assert repos[0]["repo_full"] == "doria90/dummyAI"