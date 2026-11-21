# GitHub Action

VERITY is available as a root composite GitHub Action.

```yaml
- name: Verify evidence contracts
  uses: Hmoute-Oussama/VERITY@v1
  with:
    root: .
    fail-on: contradicted
    snapshot: "true"
```

The action installs the PyPI package, initializes missing local configuration,
validates contracts, and can write a compact snapshot.

Marketplace metadata uses the root `action.yml` with shield branding.
