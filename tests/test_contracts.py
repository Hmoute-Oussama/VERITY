from __future__ import annotations

from pathlib import Path

from verity.config import contracts_path
from verity.contracts import Contract, check_contracts, load_contracts, sync_contracts


CONTRACTS = """\
version: 1
task: login
contracts:
  - id: symbol
    claim: authenticate_user exists.
    verifier: code_symbol
    args:
      symbol: authenticate_user
    required: true
  - id: missing
    claim: A missing file exists.
    verifier: file_exists
    args:
      paths: [missing.py]
    required: true
"""


def write_contracts(project: Path, text: str) -> None:
    path = contracts_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_check_contracts_verifies_each_contract(engine):
    write_contracts(engine.root, CONTRACTS)
    contracts = load_contracts(engine.root)

    results = check_contracts(engine, contracts)
    by_id = {result.contract.id: result for result in results}

    assert by_id["symbol"].met is True
    assert by_id["missing"].met is False
    assert by_id["missing"].status != "verified"
    assert "not found" in by_id["missing"].evidence_summary.lower()


def test_contracts_become_required_claims(engine):
    write_contracts(engine.root, CONTRACTS)
    contracts = load_contracts(engine.root)

    claims = sync_contracts(engine, contracts)
    stored = {claim.contract_id: claim for claim in engine.repo.claims()}

    assert set(stored) == {"symbol", "missing"}
    assert all(claim.required for claim in claims)
    assert all(claim.source == "contract" for claim in claims)
    assert stored["symbol"].kind == "existence"


def test_removed_contract_removes_its_claim(engine):
    write_contracts(engine.root, CONTRACTS)
    sync_contracts(engine, load_contracts(engine.root))

    write_contracts(
        engine.root,
        "version: 1\ntask: login\ncontracts:\n"
        "  - id: symbol\n    claim: authenticate_user exists.\n"
        "    verifier: code_symbol\n    args: {symbol: authenticate_user}\n",
    )
    sync_contracts(engine, load_contracts(engine.root))

    contract_ids = {claim.contract_id for claim in engine.repo.claims()}
    assert contract_ids == {"symbol"}


def test_contract_shorthand_for_path_and_command():
    path_contract = Contract.from_dict({"id": "p", "claim": "x", "verifier": "file_exists", "path": "app.py"}, 0)
    command_contract = Contract.from_dict(
        {"id": "c", "claim": "x", "verifier": "command", "command": "pytest -q"}, 1
    )

    assert path_contract.args == {"path": "app.py"}
    assert command_contract.args == {"command": "pytest -q"}
