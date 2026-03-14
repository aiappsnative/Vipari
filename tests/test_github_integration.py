import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from services import github_integration
from services.github_integration import _resolve_private_key_path, generate_jwt


def test_resolve_private_key_path_prefers_cwd_when_relative_file_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    key_path = tmp_path / "test-key.pem"
    key_path.write_text("dummy", encoding="utf-8")

    resolved = _resolve_private_key_path("test-key.pem")

    assert resolved == key_path.resolve()


def test_resolve_private_key_path_falls_back_to_project_root_relative_path(tmp_path, monkeypatch):
    project_root = Path(__file__).resolve().parent.parent
    workspace_key_path = project_root.parent / "test-relative-key.pem"
    workspace_key_path.write_text("dummy", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    try:
        resolved = _resolve_private_key_path("../test-relative-key.pem")
        assert resolved == workspace_key_path.resolve()
    finally:
        workspace_key_path.unlink(missing_ok=True)


def test_generate_jwt_uses_safe_expiration_window(tmp_path, monkeypatch):
    key_path = tmp_path / "test-key.pem"
    key_path.write_text("dummy-private-key", encoding="utf-8")

    captured = {}

    def fake_encode(payload, private_key, algorithm):
        captured['payload'] = payload
        captured['private_key'] = private_key
        captured['algorithm'] = algorithm
        return 'encoded-token'

    monkeypatch.setattr(github_integration.time, 'time', lambda: 1_700_000_000)
    monkeypatch.setattr(github_integration.jwt, 'encode', fake_encode)

    token = generate_jwt('2963335', str(key_path))

    assert token == 'encoded-token'
    assert captured['private_key'] == 'dummy-private-key'
    assert captured['algorithm'] == 'RS256'
    assert captured['payload']['iss'] == '2963335'
    assert captured['payload']['iat'] == 1_700_000_000 - github_integration.JWT_ISSUED_AT_SKEW_SECONDS
    assert captured['payload']['exp'] == 1_700_000_000 + github_integration.JWT_LIFETIME_SECONDS
    assert github_integration.JWT_LIFETIME_SECONDS < 10 * 60
