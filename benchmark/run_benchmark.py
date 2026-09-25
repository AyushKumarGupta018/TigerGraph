"""Benchmark runner for the HHGOA case pack.

Reads case_pack.csv from DATA_DIR (five sample cases ship in-repo;
point DATA_DIR at the extracted HHGOA_IEEE folder for the official 20),
runs the full investigation for each, and writes cases/<case_id>.json
in exactly the submission schema.

Run with:  python -m benchmark.run_benchmark
"""
import json
from pathlib import Path

import pandas as pd

from agent.config import settings
from agent.investigator import Investigator


def main() -> None:
    pack_path = Path(settings.data_dir) / "case_pack.csv"
    out_dir = Path("cases")  # the submission folder name is fixed by the spec
    out_dir.mkdir(parents=True, exist_ok=True)

    pack = pd.read_csv(pack_path, dtype=str)
    investigator = Investigator()

    print(f"Running {len(pack)} case(s) from {pack_path}\n")
    for _, row in pack.iterrows():
        answer, _case = investigator.run_case(row.to_dict())
        (out_dir / f"{answer['case_id']}.json").write_text(json.dumps(answer, indent=2, default=str))

        c = answer["case"]
        finals = ", ".join(x["action"] for x in answer["next_best_actions"]["final"])
        print(f"  {answer['case_id']}: verdict={c['verdict']:<11} p={c['fraud_probability']:<5} "
              f"pattern={c['pattern']:<28} exposure=${c['exposure_usd']:<9} "
              f"sar={'yes' if answer['sar']['file'] else 'no '} | final: {finals}")

    print(f"\nAnswer files written to {out_dir.resolve()}")


if __name__ == "__main__":
    main()
