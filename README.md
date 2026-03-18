# GitHub Project Manager (LLM Project)

> **LLM PROJECT NOTICE**
>
> This repository and initial implementation were generated with LLM assistance.

Python tool for managing GitHub repositories from a YAML inventory.

## Features

- Authenticate to GitHub using a token.
- Export all owned repositories.
- Save YAML inventory with:
  - `url`
  - `state` (`active` / `archived`)
  - `target_state` (`present` / `archived` / `absent`)
- Apply `target_state` back to GitHub:
  - `present` = repository exists and is not archived
  - `archived` = repository exists and is archived
  - `absent` = repository is deleted

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Authentication

Create a GitHub personal access token and store it in `.github-token` (recommended):

```bash
echo "<your_token_here>" > .github-token
```

By default, the CLI reads token sources in this order:

1. `--token`
2. `.github-token` in the current working directory
3. `GITHUB_TOKEN` environment variable

If you prefer environment variables, export it:

```bash
export GITHUB_TOKEN="<your_token_here>"
```

Needed permissions:

- Repository read/write (private + public)
- Delete repository permission if you plan to use `absent`

## Usage

### 1) Export inventory

```bash
gh-repo-state export --output repositories.yaml
```

### 2) Edit target states

Open `repositories.yaml` and change only `target_state` where needed.

### 3) Dry-run changes

```bash
gh-repo-state apply --input repositories.yaml --dry-run
```

### 4) Apply changes

```bash
gh-repo-state apply --input repositories.yaml
```

## YAML structure

```yaml
llm_project: true
owner: your-github-username
generated_at: 2026-03-16T12:34:56+00:00
repositories:
  - url: https://github.com/your-github-username/repo-one
    state: active
    target_state: present
    private: true
    description: Sample repository
  - url: https://github.com/your-github-username/repo-two
    state: archived
    target_state: archived
    private: false
    description: Archived example
```

## State rules

- `state` is the observed GitHub state: `active` or `archived`
- `target_state` is the desired state:
  - `present` = repository should exist and be active
  - `archived` = repository should exist and be archived
  - `absent` = repository should be deleted

For exported active repositories, `target_state` defaults to `present`.
For exported archived repositories, `target_state` defaults to `archived`.

## Safety notes

- `absent` permanently deletes repositories from GitHub.
- Always run `--dry-run` before a real apply.
- Test first against a non-critical GitHub account.

## Project files

- `src/github_project_manager/cli.py` - main CLI implementation
- `docs/USAGE.md` - detailed usage workflow
- `TODO.md` - backlog and completed work
- `LLM_PROJECT.md` - LLM project declaration
