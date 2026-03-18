from __future__ import annotations

from pathlib import Path

import yaml

from github_project_manager.cli import (
    RepoInventoryItem,
    apply_inventory,
    export_inventory,
    parse_owner_repo_from_url,
    repo_to_inventory_item,
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
        }

    def create_repository(self, name: str, private: bool = True, description: str = "") -> None:
        self.created.append((name, private, description))
        self._repos[(self._user["login"], name)] = {
            "html_url": f"https://github.com/{self._user['login']}/{name}",
            "archived": False,
            "private": private,
            "description": description,
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
        }
    )

    assert item == RepoInventoryItem(
        url="https://github.com/octo/demo",
        state="active",
        target_state="present",
        private=True,
        description="hello",
    )


def test_export_inventory_writes_expected_yaml(tmp_path: Path) -> None:
    client = FakeClient()
    client._listed_repos = [
        {
            "html_url": "https://github.com/alice/zeta",
            "archived": False,
            "private": True,
            "description": "Z",
        },
        {
            "html_url": "https://github.com/alice/alpha",
            "archived": True,
            "private": False,
            "description": "A",
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
    assert payload["repositories"][1]["state"] == "active"
    assert payload["repositories"][1]["target_state"] == "present"


def test_apply_inventory_updates_existing_repository(tmp_path: Path) -> None:
    client = FakeClient()
    client._repos[("alice", "demo")] = {
        "html_url": "https://github.com/alice/demo",
        "archived": True,
        "private": True,
        "description": "",
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