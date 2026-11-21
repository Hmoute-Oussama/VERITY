# VERITY integration matrix

These examples test VERITY against realistic project shapes. They are
self-contained fixtures and intentionally avoid external network services.

## Public repositories

| Example | Repository |
|---|---|
| FastAPI auth | https://github.com/Hmoute-Oussama/VERITY-integration-fastapi-auth |
| Flask app | https://github.com/Hmoute-Oussama/VERITY-integration-flask-app |
| Next.js app | https://github.com/Hmoute-Oussama/VERITY-integration-nextjs-app |
| Docker Compose | https://github.com/Hmoute-Oussama/VERITY-integration-docker-compose |
| Monorepo | https://github.com/Hmoute-Oussama/VERITY-integration-monorepo |
| TypeScript library | https://github.com/Hmoute-Oussama/VERITY-integration-typescript-library |
| Java service | https://github.com/Hmoute-Oussama/VERITY-integration-java-service |
| Go service | https://github.com/Hmoute-Oussama/VERITY-integration-go-service |
| Contradictory change | https://github.com/Hmoute-Oussama/VERITY-integration-contradicted-change |
| Stale evidence | https://github.com/Hmoute-Oussama/VERITY-integration-stale-evidence |
| Dependency change | [Matrix fixture](./dependency-change) |

## Complete end-to-end examples

- [Python / FastAPI](e2e-python)
- [JavaScript / package](e2e-javascript)
- [Docker / Compose](e2e-docker)
- [Monorepo](e2e-monorepo)

| Example | Primary behavior |
|---|---|
| `fastapi-auth` | Route, dependency, and test contracts |
| `flask-app` | Flask/Python application verification |
| `nextjs-app` | Package script and route contracts |
| `docker-compose` | Compose prerequisites and configuration |
| `monorepo` | Multiple packages and shared evidence |
| `typescript-library` | TypeScript package and source symbols |
| `java-service` | Java source and build declarations |
| `go-service` | Go package and source declarations |
| `contradicted-change` | A deliberately unmet required contract |
| `stale-evidence` | Evidence invalidated after dependency changes |
| `dependency-change` | Dependency declarations that change over time |

Run the complete matrix:

```bash
python examples/run_matrix.py
```

Run VERITY in one example:

```bash
verity --root examples/fastapi-auth status --json
verity --root examples/fastapi-auth contract check --json
```

The matrix has two expected outcomes:

- `expected_pass`: required contracts must pass.
- `expected_block`: a contradiction or stale state must remain observable.

Do not add the example `.verity/verity.db` to Git.

## GitHub Actions

The `Integration matrix` workflow runs every fixture on Linux, Windows, and macOS.
It also invokes the complete matrix through pre-commit.

## Pre-commit

Install hooks after enabling an environment with VERITY:

```bash
python -m pip install -e ".[dev]"
pre-commit install
pre-commit run --all-files
```

The repository's local hook runs `python examples/run_matrix.py`. Projects using
VERITY in another repository can instead install the native evidence hook:

```bash
verity hook install
```
