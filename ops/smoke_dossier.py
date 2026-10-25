"""Smoke test of Parley's dossier on a few suppliers after the Sunday release. Runs inside the agent's
venv (`uv run --project agent python ops/smoke_dossier.py sup:70340 sup:70520 sup:70550`): builds a
one-supplier case from its active parts, then checks the profile, the claims (every cited ID must
be a known fact), the cached persona, structured profile fields if present, and the release rows."""

import json
import sys

from parley.dossier import ROOT, Dossier, load_package
from parley.runner import load_personas


def main(sups):
    pkg = load_package()
    if pkg is None:
        sys.exit("no data package")
    release = getattr(pkg, "release", None)
    print(f"package {pkg.root}  release {release or 'none'}  facts {len(pkg.facts)}")
    personas = load_personas()
    fields_path = ROOT / "cache" / "profile_fields.json"
    fields = (
        json.loads(fields_path.read_text(encoding="utf-8"))
        if fields_path.is_file()
        else None
    )
    bad = 0
    for sid in sups:
        parts = [
            r["part_id"]
            for r in pkg.query(
                "SELECT part_id FROM sourcing WHERE supplier_id = ? AND status = 'active'",
                (sid,),
            )
        ]
        case = {
            "case_id": f"smoke:{sid}",
            "suppliers": [sid],
            "parts": parts[:2],
            "refs": [],
            "buyer": {},
            "facts": {},
        }
        problems = []
        try:
            d = Dossier(case, pkg)
            p = d.profiles[sid]
            claims = d.claims(sid)
            unknown = sorted({r for c in claims for r in c.refs if not d.known(r)})
            news = d.news(sid)
            rel_rows = [
                r
                for t in ("news", "documents")
                for r in d.release_rows(t)
                if r.get("supplier_id") == sid
            ]
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {sid}: dossier raised {e!r}")
            bad += 1
            continue
        if not p.name:
            problems.append("no name")
        if not claims:
            problems.append("no claims")
        if unknown:
            problems.append(f"unknown cited IDs {unknown[:5]}")
        if sid not in personas:
            problems.append("no cached persona")
        if fields is not None and sid not in fields:
            problems.append("not in profile_fields.json")
        status = "FAIL" if problems else "PASS"
        bad += bool(problems)
        print(f"  [{status}] {sid} {p.name}: parts {len(parts)}, claims {len(claims)}, news {len(news)}, "
              f"release rows {len(rel_rows)}, persona {'yes' if sid in personas else 'no'}"
              + (f", fields {'yes' if sid in fields else 'no'}" if fields is not None else "")
              + (f"  -- {'; '.join(problems)}" if problems else ""))  # fmt: skip
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:] or ["sup:70340", "sup:70520", "sup:70550"])
