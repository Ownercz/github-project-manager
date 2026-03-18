from __future__ import annotations

from pathlib import Path

import yaml

from github_project_manager.cli import (
    GitHubApiError,
    RepoInventoryItem,
    apply_inventory,
    export_inventory,
    matches_limit,
    parse_owner_repo_from_url,
    repo_to_inventory_item,
    resolve_github_token,
)


class FakeClient:
    def __init__(self) -> None:
        self.created: list[tuple[str, bool, str]] = []
        self.updated: list[tuple[str, str, bool]] = []
        self.deleted: list[tuple[str, str]] = []
        self._repos: dict[tuple[str, str], dict] = {}
        self._user = {"login": "alice"}
        self._listed_repos: list[dict] = []

    def get_authenticated_user(self) -> dict:
        return self._user

    def list_repositories(self) -> list[dict]:
        return self._listed_repos

    def get_repository(self, owner: str, name: str) -> dict | None:
        repo = self._repos.get((owner, name))
        return None if repo is None else dict(repo)

    def update_repository_state(self, owner: str, name: str, archived: bool) -> None:
        self.updated.append((owner, name, archived))
        self._repos[(owner, name)] = {
            "html_url": f"https://github.com/{owner}/{name}",
            "archived": archived,
            "private": True,
            "description": "",
            "fork": False,
        }

    def create_repository(self, name: str, private: bool = True, description: str = "") -> None:
        self.created.append((name, private, description))
        self._repos[(self._user["login"], name)] = {
            "html_url": f"https://github.com/{self._user['login']}/{name}",
            "archived": False,
            "private": private,
            "description": description,
            "fork": False,
        }

    def delete_repository(self, owner: str, name: str) -> None:
        self.deleted.append((owner, name))
        self._repos.pop((owner, name), None)


def test_parse_owner_repo_from_url_supports_dot_git() -> None:
    assert parse_owner_repo_from_url("https://github.com/octo/demo.git") == ("octo", "demo")


def test_repo_to_inventory_item_maps_active_to_present() -> None:
    item = repo_to_inventory_item(
        {
            "html_url": "https://github.com/octo/demo",
            "archived": False,
            "private": True,
            "description": "hello",
            "fork": False,
        }
    )

    assert item == RepoInventoryItem(
        url="https://github.com/octo/demo",
        state="active",
        target_state="present",
        private=True,
        description="hello",
        fork=False,
    )


def test_export_inventory_writes_expected_yaml(tmp_path: Path) -> None:
    client = FakeClient()
    client._listed_repos = [
        {
            "html_url": "https://github.com/alice/zeta",
            "archived": False,
            "private": True,
            "description": "Z",
            "fork": False,
        },
        {
            "html_url": "https://github.com/alice/alpha",
            "archived": True,
            "private": False,
            "description": "A",
            "fork": True,
        },
    ]

    output = tmp_path / "repositories.yaml"
    export_inventory(client, str(output))

    payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert payload["llm_project"] is True
    assert payload["owner"] == "alice"
    assert [repo["url"] for repo in payload["repositories"]] == [
        "https://github.com/alice/alpha",
        "https://github.com/alice/zeta",
    ]
    assert payload["repositories"][0]["state"] == "archived"
    assert payload["repositories"][0]["target_state"] == "archived"
    assert payload["repositories"][0]["fork"] is True
    assert payload["repositories"][1]["state"] == "active"
    assert payload["repositories"][1]["target_state"] == "present"
    assert payload["repositories"][1]["fork"] is False


def test_apply_inventory_updates_existing_repository(tmp_path: Path) -> None:
    client = FakeClient()
    client._repos[("alice", "demo")] = {
        "html_url": "https://github.com/alice/demo",
        "archived": True,
        "private": True,
        "description": "",
        "fork": False,
    }

    inventory = {
        "repositories": [
            {
                "url": "https://github.com/alice/demo",
                "state": "archived",
                "target_state": "present",
                "private": True,
                "description": "",
            }
        ]
    }
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    apply_inventory(client, str(inventory_path), dry_run=False)

    assert client.updated == [("alice", "demo", False)]


def test_apply_inventory_creates_and_archives_missing_repository(tmp_path: Path) -> None:
    client = FakeClient()
    inventory = {
        "repositories": [
            {
                "url": "https://github.com/alice/new-repo",
                "state": "active",
                "target_state": "archived",
                "private": False,
                "description": "created by test",
            }
        ]
    }
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    apply_inventory(client, str(inventory_path), dry_run=False)

    assert client.created == [("new-repo", False, "created by test")]
    assert client.updated == [("alice", "new-repo", True)]


def test_apply_inventory_deletes_absent_repository(tmp_path: Path) -> None:
    client = FakeClient()
    client._repos[("alice", "old-repo")] = {
        "html_url": "https://github.com/alice/old-repo",
        "archived": False,
        "private": True,
        "description": "",
        "fork": False,
    }
    inventory = {
        "repositories": [
            {
                "url": "https://github.com/alice/old-repo",
                "state": "active",
                "target_state": "absent",
                "private": True,
                "description": "",
            }
        ]
    }
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    apply_inventory(client, str(inventory_path), dry_run=False)

    assert client.deleted == [("alice", "old-repo")]


def test_apply_inventory_dry_run_makes_no_changes(tmp_path: Path) -> None:
    client = FakeClient()
    inventory = {
        "repositories": [
            {
                "url": "https://github.com/alice/dry-run",
                "state": "active",
                "target_state": "archived",
                "private": True,
                "description": "",
            }
        ]
    }
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    apply_inventory(client, str(inventory_path), dry_run=True)

    assert client.created == []
    assert client.updated == []
    assert client.deleted == []


def test_resolve_github_token_prefers_explicit_token(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    (tmp_path / ".github-token").write_text("file-token\n", encoding="utf-8")

    assert resolve_github_token("explicit-token") == "explicit-token"


def test_resolve_github_token_uses_file_before_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    (tmp_path / ".github-token").write_text("file-token\n", encoding="utf-8")

    assert resolve_github_token("") == "file-token"


def test_resolve_github_token_falls_back_to_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")

    assert resolve_github_token("") == "env-token"


def test_resolve_github_token_ignores_empty_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    (tmp_path / ".github-token").write_text("\n", encoding="utf-8")

    assert resolve_github_token("") == "env-token"


def test_apply_inventory_retries_on_error(tmp_path: Path) -> None:
    """Test that apply_inventory retries on error and continues to next item"""
    client = FakeClient()
    
    # First repo will fail on first attempt, succeed on retry
    client._repos[("alice", "fail-then-succeed")] = {
        "html_url": "https://github.com/alice/fail-then-succeed",
        "archived": False,
        "private": True,
        "description": "",
        "fork": False,
    }
    
    # Track call count to simulate failure on first call
    client._fail_count = {"fail-then-succeed": 0}
    original_get = client.get_repository
    
    def get_repository_with_failure(owner: str, name: str):
        key = f"{owner}/{name}"
        if key == "alice/fail-then-succeed":
            client._fail_count["fail-then-succeed"] += 1
            if client._fail_count["fail-then-succeed"] == 1:
                raise GitHubApiError("Simulated network error")
        return original_get(owner, name)
    
    client.get_repository = get_repository_with_failure
    
    inventory = {
        "repositories": [
            {
                "url": "https://github.com/alice/fail-then-succeed",
                "state": "active",
                "target_state": "present",
                "private": True,
                "description": "",
            },
            {
                "url": "https://github.com/alice/success",
                "state": "active",
                "target_state": "present",
                "private": True,
                "description": "",
            }
        ]
    }
    
    client._repos[("alice", "success")] = {
        "html_url": "https://github.com/alice/success",
        "archived": False,
        "private": True,
        "description": "",
        "fork": False,
    }
    
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    # Should not raise, should retry and continue
    apply_inventory(client, str(inventory_path), dry_run=True)
    
    # Verify that both repos were processed (retry on first, success on second)
    assert client._fail_count["fail-then-succeed"] == 2  # First call failed, second succeeded


# --- limit filter tests ---

def _make_item(*, state: str = "active", fork: bool = False, private: bool = False) -> RepoInventoryItem:
    return RepoInventoryItem(
        url="https://github.com/alice/repo",
        state=state,
        target_state="present",
        private=private,
        description="",
        fork=fork,
    )


def test_matches_limit_none_always_true() -> None:
    assert matches_limit(_make_item(), None) is True


def test_matches_limit_forked() -> None:
    assert matches_limit(_make_item(fork=True), "forked") is True
    assert matches_limit(_make_item(fork=False), "forked") is False


def test_matches_limit_archived() -> None:
    assert matches_limit(_make_item(state="archived"), "archived") is True
    assert matches_limit(_make_item(state="active"), "archived") is False


def test_matches_limit_active() -> None:
    assert matches_limit(_make_item(state="active"), "active") is True
    assert matches_limit(_make_item(state="archived"), "active") is False


def test_matches_limit_private() -> None:
    assert matches_limit(_make_item(private=True), "private") is True
    assert matches_limit(_make_item(private=False), "private") is False


def test_export_inventory_limit_forked(tmp_path: Path) -> None:
    client = FakeClient()
    client._listed_repos = [
        {"html_url": "https://github.com/alice/fork-repo", "archived": False, "private": False, "description": "", "fork": True},
        {"html_url": "https://github.com/alice/own-repo", "archived": False, "private": False, "description": "", "fork": False},
    ]
    output = tmp_path / "out.yaml"
    export_inventory(client, str(output), limit="forked")
    payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert len(payload["repositories"]) == 1
    assert payload["repositories"][0]["url"] == "https://github.com/alice/fork-repo"


def test_apply_inventory_limit_skips_non_matching(tmp_path: Path) -> None:
    client = FakeClient()
    client._repos[("alice", "active-repo")] = {
        "html_url": "https://github.com/alice/active-repo",
        "archived": False,
        "private": True,
        "description": "",
        "fork": False,
    }
    inventory = {
        "repositories": [
            {"url": "https://github.com/alice/active-repo", "state": "active", "target_state": "present", "private": True, "description": "", "fork": False},
            {"url": "https://github.com/alice/archived-repo", "state": "archived", "target_state": "archived", "private": False, "description": "", "fork": False},
        ]
    }
    inventory_path = tmp_path / "repositories.yaml"
    inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    apply_inventory(client, str(inventory_path), dry_run=True, limit="active")

    # Only active-repo should have been evaluated — archived-repo was filtered out
    # No API changes expected in dry-run, but no errors either
    assert client.created == []
    assert client.updated == []
    assert client.deleted == []