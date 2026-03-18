# Usage Guide

## Command overview

- Export inventory:
  - `gh-repo-state export --output repositories.yaml`
- Apply inventory in preview mode:
  - `gh-repo-state apply --input repositories.yaml --dry-run`
- Apply inventory for real:
  - `gh-repo-state apply --input repositories.yaml`

## State mapping

### `state` (observed from GitHub)

- `active`
- `archived`

### `target_state` (desired state)

- `present` → repo should exist and be active (not archived)
- `archived` → repo should exist and be archived
- `absent` → repo should be deleted

## Apply behavior

For each repository record:

1. Parse owner and repo name from `url`.
2. Read current state from GitHub.
3. Execute operation by `target_state`:
   - `present`: create if missing, unarchive if archived
   - `archived`: create if missing, archive
   - `absent`: delete if exists

## Cross-owner behavior

If an inventory item belongs to a different owner than the authenticated user:

- Existing repositories can be updated or deleted if the token has access.
- Missing repositories are skipped because the tool only creates repositories for the authenticated user.

## Example workflow

1. Export current repositories.
2. Review the generated YAML.
3. Change `target_state` values.
4. Run a dry-run.
5. Apply the changes.
6. Export again and verify the final state.

## Testing

Run the test suite with:

```bash
pytest
```
