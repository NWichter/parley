import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import lib, merge
for f in sorted((lib.HERE / "out" / "adjud").glob("*.json")):
    sid = "sup:" + f.stem.split("_")[1]
    a = json.loads(f.read_text(encoding="utf-8"))
    res = merge.cleaned(sid)
    for d in merge.disputes(sid, res):
        c = a["choices"].get(d["id"], {})
        ks = {m: (res[m][0][d["field"]]["key"] if d["kind"] == "scalar" else (d["item"] in res[m][0][d["field"]])) for m in res}
        pick = a["labels"].get(d["id"], {}).get(c.get("choice"), c.get("choice"))
        print(sid, d["id"], ks, "->", pick, "|", c.get("reason"))
