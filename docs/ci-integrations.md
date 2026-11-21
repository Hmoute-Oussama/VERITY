# CI integrations

## GitHub Actions

```yaml
name: VERITY evidence

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  evidence:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: python -m pip install -e .
      - run: verity contract check
      - run: verity snapshot --json
```

For a pull request, grant `contents: read` only unless the workflow intentionally
writes comments or updates generated files.

## Exit codes

- `0`: required contracts satisfied or command allowed.
- `1`: verification, contract, or evidence failure.
- `2`: invalid input or blocked action.
