"""Run VERITY integration fixtures and validate their expected outcomes."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from verity.contracts import check_contracts, load_contracts
from verity.engine import Engine


ROOT = Path(__file__).resolve().parent


def run_example(path: Path) -> dict:
    with TemporaryDirectory(prefix=f"verity-{path.name}-") as temp:
        copy = Path(temp) / path.name
        shutil.copytree(path, copy)
        engine = Engine(copy)
        try:
            contracts = load_contracts(copy)
            if path.name == "stale-evidence":
                check_contracts(engine, contracts)
                source = copy / "source.py"
                source.write_text(
                    'def current():\n    return "changed-after-verification"\n',
                    encoding="utf-8",
                )
                engine.invalidate_stale()
                results = []
            else:
                results = check_contracts(engine, contracts)
            status = engine.status()
            if path.name == "stale-evidence":
                contract_status = engine.repo.claims()[0].status if engine.repo.claims() else "stale"
                contract_results = [{"id": "source_file", "status": contract_status, "met": False}]
            else:
                contract_results = [
                    {
                        "id": result.contract.id,
                        "status": result.status,
                        "met": result.met,
                    }
                    for result in results
                ]
            return {
                "name": path.name,
                "expected": (path / "expected.txt").read_text(encoding="utf-8").strip(),
                "overall": status.overall,
                "contracts": contract_results,
            }
        finally:
            engine.repo.close()


def main() -> int:
    examples = sorted(path for path in ROOT.iterdir() if path.is_dir() and not path.name.startswith("."))
    report = [run_example(path) for path in examples]
    failures: list[str] = []
    for item in report:
        if item["expected"] == "pass" and item["overall"] not in {"verified", "no-claims"}:
            failures.append(f"{item['name']}: expected pass, got {item['overall']}")
        if item["expected"] == "contradicted" and not any(
            contract["status"] == "contradicted" for contract in item["contracts"]
        ):
            failures.append(f"{item['name']}: expected a contradicted contract")
        if item["expected"] == "stale" and item["overall"] != "stale":
            failures.append(f"{item['name']}: expected stale evidence")
    print(json.dumps(report, indent=2))
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
