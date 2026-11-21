# Plugin SDK

Plugins are declarative by default. VERITY will not execute plugin Python merely
from a project manifest.

## Manifest

Create `.verity/plugins.yaml`:

```yaml
plugins:
  openapi_policy:
    name: OpenAPI policy
    package: verity-openapi
    verifier: openapi_policy
    minimum_api: "1.0"
    trust_level: community
    verified: false
```

Validate it against `schemas/plugins.schema.json`.

## Safety requirements

- A plugin must declare supported VERITY API and verifier IDs.
- A plugin must declare filesystem, network, process, and environment needs.
- A plugin must be signed or reviewed before `verified: true`.
- A plugin must not receive secrets unless explicitly requested.
- A plugin must return VERITY evidence fields and dependencies.
- A plugin must tolerate missing and malicious inputs.
- A plugin must document confidence and false-positive behavior.

## Trust levels

| Level | Meaning |
|---|---|
| community | Unreviewed community integration |
| verified | Tests and security review exist |
| maintainer | Maintained by VERITY maintainers |
