from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml
from colorama import Fore, Style, init

init(autoreset=True)

API_BASE = "https://api.github.com"
SUPPORTED_TARGET_STATES = {"present", "archived", "absent"}
SUPPORTED_LIMITS = {"forked", "archived", "active", "private"}
REQUEST_TIMEOUT = 30
DEFAULT_TOKEN_FILE = ".github-token"


class GitHubApiError(RuntimeError):
    """Raised when a GitHub API request fails."""


@dataclass(slots=True)
class RepoInventoryItem:
    url: str
    state: str
    target_state: str
    private: bool
    description: str
    fork: bool


class GitHubClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-project-manager/0.1.0",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{API_BASE}{path}"
        response = self.session.request(method=method, url=url, timeout=REQUEST_TIMEOUT, **kwargs)
        if response.status_code >= 400:
            raise GitHubApiError(
                f"GitHub API error {response.status_code} for {method} {path}: {response.text}"
            )
        return response

    def get_authenticated_user(self) -> dict[str, Any]:
        return self._request("GET", "/user").json()

    def list_repositories(self) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        page = 1

        while True:
            batch = self._request(
                "GET",
                "/user/repos",
                params={"per_page": 100, "page": page, "affiliation": "owner"},
            ).json()
            if not batch:
                break

            repositories.extend(batch)
            if len(batch) < 100:
                break
            page += 1

        return repositories

    def get_repository(self, owner: str, name: str) -> dict[str, Any] | None:
        response = self.session.get(
            f"{API_BASE}/repos/{owner}/{name}",
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise GitHubApiError(
                f"GitHub API error {response.status_code} for GET /repos/{owner}/{name}: {response.text}"
            )
        return response.json()

    def update_repository_state(self, owner: str, name: str, archived: bool) -> None:
        self._request("PATCH", f"/repos/{owner}/{name}", json={"archived": archived})

    def create_repository(self, name: str, private: bool = True, description: str = "") -> None:
        self._request(
            "POST",
            "/user/repos",
            json={"name": name, "private": private, "description": description},
        )

    def delete_repository(self, owner: str, name: str) -> None:
        self._request("DELETE", f"/repos/{owner}/{name}")


def parse_owner_repo_from_url(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(repo_url)
    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"Invalid repository URL: {repo_url}")
    owner = parts[0]
    name = parts[1].removesuffix(".git")
    return owner, name


def repo_to_inventory_item(repo: dict[str, Any]) -> RepoInventoryItem:
    observed_state = "archived" if repo.get("archived") else "active"
    target_state = "archived" if observed_state == "archived" else "present"
    return RepoInventoryItem(
        url=repo["html_url"],
        state=observed_state,
        target_state=target_state,
        private=bool(repo.get("private", True)),
        description=repo.get("description") or "",
        fork=bool(repo.get("fork", False)),
    )


def load_inventory(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_inventory(path: str | Path, payload: dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)


def resolve_github_token(explicit_token: str | None = None) -> str:
    provided = str(explicit_token or "").strip()
    if provided:
        return provided

    token_file = Path.cwd() / DEFAULT_TOKEN_FILE
    if token_file.is_file():
        file_token = token_file.read_text(encoding="utf-8").strip()
        if file_token:
            return file_token

    return str(os.getenv("GITHUB_TOKEN", "")).strip()


def matches_limit(item: RepoInventoryItem, limit: str | None) -> bool:
    """Return True if the item satisfies the given limit filter."""
    if limit is None:
        return True
    if limit == "forked":
        return item.fork
    if limit == "archived":
        return item.state == "archived"
    if limit == "active":
        return item.state == "active"
    if limit == "private":
        return item.private
    return True


def export_inventory(client: GitHubClient, output_path: str, limit: str | None = None) -> None:
    user = client.get_authenticated_user()
    repos = client.list_repositories()
    all_items = sorted((repo_to_inventory_item(repo) for repo in repos), key=lambda item: item.url.lower())
    items = [item for item in all_items if matches_limit(item, limit)]

    payload = {
        "llm_project": True,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "owner": user["login"],
        "repositories": [asdict(item) for item in items],
    }
    save_inventory(output_path, payload)
    suffix = f" (filter: {limit})" if limit else ""
    print(f"Exported {len(items)} repositories to {output_path}{suffix}")


def validate_target_state(url: str, target_state: str) -> str:
    normalized = target_state.strip().lower()
    if normalized not in SUPPORTED_TARGET_STATES:
        raise ValueError(
            f"Unsupported target_state '{target_state}' for {url}. "
            f"Supported values: {sorted(SUPPORTED_TARGET_STATES)}"
        )
    return normalized


def apply_inventory(client: GitHubClient, input_path: str, dry_run: bool, limit: str | None = None) -> None:
    inventory = load_inventory(input_path)
    repositories = inventory.get("repositories", [])
    if not isinstance(repositories, list):
        raise ValueError("Inventory field 'repositories' must be a list")

    authed_owner = client.get_authenticated_user()["login"]

    skipped = 0
    for entry in repositories:
        if not isinstance(entry, dict):
            raise ValueError("Each inventory entry must be an object")

        url = str(entry.get("url") or "").strip()
        if not url:
            raise ValueError("Each inventory entry must include 'url'")

        # Apply limit filter using inventory fields (no API call needed)
        if limit is not None:
            entry_state = str(entry.get("state") or "")
            entry_fork = bool(entry.get("fork", False))
            entry_private = bool(entry.get("private", False))
            if limit == "forked" and not entry_fork:
                skipped += 1
                continue
            if limit == "archived" and entry_state != "archived":
                skipped += 1
                continue
            if limit == "active" and entry_state != "active":
                skipped += 1
                continue
            if limit == "private" and not entry_private:
                skipped += 1
                continue

        target_state = validate_target_state(url, str(entry.get("target_state") or ""))
        owner, name = parse_owner_repo_from_url(url)
        
        # Try operation with retry on error
        for attempt in range(2):
            try:
                existing = client.get_repository(owner, name)

                if target_state == "absent":
                    if existing is None:
                        print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} {owner}/{name}: already absent")
                        break
                    print(f"{Fore.YELLOW}[DELETE]{Style.RESET_ALL} {owner}/{name}")
                    if not dry_run:
                        client.delete_repository(owner, name)
                    break

                desired_archived = target_state == "archived"
                if existing is None:
                    if owner != authed_owner:
                        print(
                            f"{Fore.GREEN}[SKIP]{Style.RESET_ALL} {owner}/{name}: missing and cannot be created under authenticated owner {authed_owner}"
                        )
                        break

                    private = bool(entry.get("private", True))
                    description = str(entry.get("description") or "")
                    print(f"{Fore.YELLOW}[CREATE]{Style.RESET_ALL} {owner}/{name} (private={private}, archived={desired_archived})")
                    if not dry_run:
                        client.create_repository(name=name, private=private, description=description)
                        if desired_archived:
                            client.update_repository_state(owner, name, archived=True)
                    break

                current_archived = bool(existing.get("archived", False))
                if current_archived == desired_archived:
                    print(f"{Fore.GREEN}[OK]{Style.RESET_ALL} {owner}/{name}: already in state '{target_state}'")
                    break

                print(f"{Fore.YELLOW}[UPDATE]{Style.RESET_ALL} {owner}/{name}: archived={desired_archived}")
                if not dry_run:
                    client.update_repository_state(owner, name, archived=desired_archived)
                break
                
            except (GitHubApiError, OSError, ValueError) as exc:
                if attempt == 0:
                    print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {owner}/{name}: {exc}")
                    print(f"{Fore.YELLOW}[RETRY]{Style.RESET_ALL} {owner}/{name}...")
                else:
                    print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {owner}/{name}: {exc} (retry failed, skipping)")

    if skipped:
        print(f"Skipped {skipped} repositories (filter: {limit})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh-repo-state",
        description="Export and apply GitHub repository state via YAML",
    )
    parser.add_argument(
        "--token",
        default="",
        help="GitHub token (overrides .github-token and env GITHUB_TOKEN)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    export_cmd = subparsers.add_parser("export", help="Export repositories to YAML")
    export_cmd.add_argument("--output", default="repositories.yaml", help="Output YAML path")
    export_cmd.add_argument(
        "--limit",
        choices=sorted(SUPPORTED_LIMITS),
        default=None,
        metavar="FILTER",
        help=f"Only include repositories matching filter: {', '.join(sorted(SUPPORTED_LIMITS))}",
    )

    apply_cmd = subparsers.add_parser("apply", help="Apply target_state from YAML")
    apply_cmd.add_argument("--input", default="repositories.yaml", help="Input YAML path")
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview operations without changing GitHub",
    )
    apply_cmd.add_argument(
        "--limit",
        choices=sorted(SUPPORTED_LIMITS),
        default=None,
        metavar="FILTER",
        help=f"Only process repositories matching filter: {', '.join(sorted(SUPPORTED_LIMITS))}",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        token = resolve_github_token(args.token)
        if not token:
            print(
                "Missing GitHub token. Use .github-token, set GITHUB_TOKEN, or pass --token.",
                file=sys.stderr,
            )
            return 2

        client = GitHubClient(token=token)

        if args.command == "export":
            export_inventory(client=client, output_path=args.output, limit=args.limit)
        elif args.command == "apply":
            apply_inventory(client=client, input_path=args.input, dry_run=args.dry_run, limit=args.limit)
        else:
            parser.print_help()
            return 2
    except (GitHubApiError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())