"""Parley must not know any case or supplier by name: every rule is general."""

import re
from pathlib import Path

import pytest

from parley.dossier import load_package

SRC = Path(__file__).resolve().parents[1] / "parley"
FILES = [p for p in SRC.glob("*.py")]
PATTERNS = [
    re.compile(r"case:[A-Z]\d{2}"),
    re.compile(r"(?<![\w-])[PDHE]\d{2}\b"),
    re.compile(r"\bsup:[\w-]+"),
    re.compile(r"\bpart:[\w-]+"),
    re.compile(r"\b7\d{4}\b"),
    re.compile(r"DEMO-[A-Z]\d"),
]
GENERIC = {
    "gmbh",
    "giesserei",
    "gießerei",
    "werk",
    "technik",
    "service",
    "systeme",
    "industrie",
    "precision",
    "machining",
    "components",
    "castings",
    "elektromotory",
    "strojírna",
    "odlewnia",
    "feinmechanik",
}


def source():
    return {p.name: p.read_text(encoding="utf-8") for p in FILES}


def test_no_case_or_supplier_ids_in_code():
    hits = [
        (name, pat.pattern, m.group(0))
        for name, text in source().items()
        for pat in PATTERNS
        for m in pat.finditer(text)
    ]
    assert not hits, hits


def test_no_supplier_names_in_code():
    pkg = load_package()
    if pkg is None:
        pytest.skip("no data package")
    words = set()
    for row in pkg.query("SELECT name FROM suppliers", limit=1000):
        for w in re.findall(r"[\w-]{6,}", row["name"]):
            if w.lower() not in GENERIC:
                words.add(w)
    text = "\n".join(source().values())
    hits = sorted(w for w in words if re.search(rf"\b{re.escape(w)}\b", text))
    assert not hits, hits
