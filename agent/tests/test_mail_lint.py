"""The mail lint: our prose changes, the data block never does.

The bank part plays a persona subset (`MAIL_LINT_PERSONAS`) with the current code and lints every
buyer e-mail; `MAIL_LINT_RUN=<eval/results/ts>` lints an existing run instead (`MAIL_LINT_AGENT` picks
the agent label)."""

import itertools
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import supplier_msg

from parley import writer
from parley.analyst import read_reply
from parley.dossier import Claim
from parley.policy import Decision

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
import audit_mails  # noqa: E402

FLAGS = ("OPENINGS", "LEADIN", "GREETING")
SHEETS = [
    {"unit_price": 48.1, "payment_days": 14, "contract_months": 24, "effective_date": "2027-02-01"},
    {"unit_price": 48.4, "payment_days": 30, "contract_months": 24, "effective_date": "2027-02-01"},
    {"unit_price": 48.9, "payment_days": 30, "contract_months": 12, "effective_date": "2027-04-01"},
]
CLAIMS = [
    Claim("index", ["idx:S-1@2025-09", "idx:S-1@2026-09"], "The S-1 index changed -9.1% from 2025-09 to 2026-09 (idx:S-1@2025-09, idx:S-1@2026-09).", 8, None, "index", "sup:T1"),
    Claim("otd", ["sc:T1@2026Q3"], "Your on-time delivery averaged 88.2% over the last two scorecard quarters (sc:T1@2026Q3).", 6, None, "scorecard", "sup:T1"),
]
LINES = [
    {"breakdown": True, "volume": (4000, 24), "volume_ref": "fc:T-1@2026-09", "pay_on_time": True},
    {"volume": (4000, 12)},
    {"cash": True, "ask_floor": True},
    {"mirror": True},
    {},
]
SUPPLIER_BODIES = [
    "Good day,\nour terms stand.\nKind regards,\nDemo Gießerei GmbH",
    "Hello,\nwe need to clarify this internally and will come back to you.\nBest regards\nAnna Berger\nSales",
    "Hello,\nwhich figures do you base this on? Please provide the data reference.\nKind regards\nJan Novak\nExport",
]


def _flags(monkeypatch, on):
    for f in FLAGS:
        monkeypatch.setenv(f"PARLEY_{f}", "1" if on else "0")


def _variants(case):
    for lines, n_sheets, n_claims, body, n, moved in itertools.product(LINES, (1, 2, 3), (0, 1, 2), SUPPLIER_BODIES, (0, 1, 4), (False, True)):
        if lines.get("mirror") and n_sheets > 1:
            continue
        d = Decision("send", "sup:T1", sheets=SHEETS[:n_sheets], claims=CLAIMS[:n_claims], lines=dict(lines),
                     metrics={"moved": moved, "conceded": moved, "moves_left": 9 - n})  # fmt: skip
        msg = {**supplier_msg("sup:T1", n, SHEETS[0]), "body": body}
        ctx = {"n": n, "prev_openings": ["Thank you for your reply."], "history": [msg], "key": "case:T01|sup:T1"}
        yield d, msg, read_reply(msg), n == 0, ctx


def test_data_block_is_byte_identical_with_and_without_the_prose_flags(case, monkeypatch):
    count = 0
    for d, msg, reading, first, ctx in _variants(case):
        _flags(monkeypatch, False)
        old, _ = writer.compose(case, d, msg, reading, first)
        _flags(monkeypatch, True)
        new, _ = writer.compose(case, d, msg, reading, first, ctx=ctx)
        assert audit_mails.data_block(new["body"]) == audit_mails.data_block(old["body"])
        assert new["term_sheets"] == old["term_sheets"] and new["refs"] == old["refs"]
        assert new["subject"] == old["subject"]
        assert audit_mails.split_mail(new["body"])[4] == audit_mails.split_mail(old["body"])[4]
        count += 1
    assert count > 300


def test_flags_off_is_the_old_wording(case, monkeypatch):
    _flags(monkeypatch, False)
    d = Decision("send", "sup:T1", sheets=SHEETS[:2], metrics={"moved": True})
    msg = {**supplier_msg("sup:T1", 1, SHEETS[0]), "body": SUPPLIER_BODIES[0]}
    body = writer.compose(case, d, msg, read_reply(msg), False)[0]["body"]
    assert "Thank you for your reply and for the step you have taken; we appreciate it." in body
    assert "Here are 2 packages that would work for us; please tell us which one suits you:" in body


def test_openings_never_repeat_in_a_thread(case, monkeypatch):
    _flags(monkeypatch, True)
    for moved in (False, True):
        used = []
        for n in range(10):
            d = Decision("send", "sup:T1", sheets=SHEETS[:2], metrics={"moved": moved, "conceded": moved, "moves_left": 10 - n})
            msg = {**supplier_msg("sup:T1", n, SHEETS[0]), "body": SUPPLIER_BODIES[0]}
            ctx = {"n": n, "prev_openings": list(used), "history": [msg], "key": "case:T01|sup:T1"}
            body = writer.compose(case, d, msg, read_reply(msg), n == 0, ctx=ctx)[0]["body"]
            used.append(writer.opening_of(body))
        assert len(set(used)) == len(used)


def test_greeting_takes_the_name_from_an_earlier_mail(case, monkeypatch):
    _flags(monkeypatch, True)
    named = {**supplier_msg("sup:T1", 0, SHEETS[0]), "body": SUPPLIER_BODIES[1]}
    plain = {**supplier_msg("sup:T1", 1, SHEETS[0]), "body": SUPPLIER_BODIES[0]}
    d = Decision("send", "sup:T1", sheets=SHEETS[:1], metrics={"moves_left": 8})
    body = writer.compose(case, d, plain, read_reply(plain), False, ctx={"n": 1, "history": [named, plain]})[0]["body"]
    assert body.startswith("Dear Anna Berger,")


def test_lint_catches_the_old_problems(tmp_path):
    t = tmp_path / "transcripts"
    t.mkdir()
    sup = "### SUPPLIER [open]\n```\nHello,\nok.\nKind regards\nAnna Berger\nSales\n```\n- sheet: {\"unit_price\": 10.0}\n\n"
    bad = ("### BUYER\n```\nDear Sir or Madam,\n\nThank you for your reply. Please confirm today.\n\n"
           "On this basis we propose: EUR 9.00 per unit.\n\nWhat would you need from us to move further?\n\n"
           f"{writer.SIGN_OFF}\n```\n- sheet: {{\"unit_price\": 9.0}}\n\n")  # fmt: skip
    (t / "x__holder__E01__s0.md").write_text("# x\n\n## sup:1 (holder)\n\n" + sup + bad + sup + bad, encoding="utf-8")
    rules = {r for r, _ in audit_mails.lint(tmp_path)["x__holder__E01__s0"]}
    assert {"greeting", "banned", "repeated_opening"} <= rules


def _bank_run():
    given = os.environ.get("MAIL_LINT_RUN")
    if given:
        return Path(given), os.environ.get("MAIL_LINT_AGENT", "lint")
    if not shutil.which("uv"):
        pytest.skip("uv not found")
    personas = os.environ.get("MAIL_LINT_PERSONAS", "holder announced_steps dup_punisher floor_stop").split()
    cmd = ["uv", "run", str(ROOT / "eval" / "bank.py"), "run", "--no-baseline", "--parallel", "10", "--personas", *personas,
           "--agent", f"lint=uv run --project {ROOT / 'agent'} parley play {{case}} --no-llm"]  # fmt: skip
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    if "no data package" in (p.stdout + p.stderr):
        pytest.skip("no data package for the bank")
    m = re.search(r"results in (.+)$", p.stdout, re.M)
    assert p.returncode == 0 and m, p.stdout[-2000:] + p.stderr[-2000:]
    return Path(m.group(1).strip()), "lint"


def test_every_bank_mail_passes_the_lint():
    run, agent = _bank_run()
    assert audit_mails.mail_count(run, agent) > 50
    fails = audit_mails.lint(run, agent)
    assert not fails, "\n".join(f"{g}: {r}: {d}" for g, fs in sorted(fails.items())[:30] for r, d in fs[:3])
