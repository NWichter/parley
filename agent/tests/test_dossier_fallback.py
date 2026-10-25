from parley import runner


class _Journal:
    def __init__(self):
        self.rows = []

    def log(self, kind, **kw):
        self.rows.append((kind, kw))


def _bare(case, package):
    r = runner.CaseRunner.__new__(runner.CaseRunner)
    r.case, r.package, r.journal = case, package, _Journal()
    return r


def test_dossier_error_falls_back_to_the_plain_profile(monkeypatch):
    calls = []

    class Flaky:
        def __init__(self, case, package):
            calls.append(package)
            if package is not None:
                raise RuntimeError("sqlite busy")
            self.plain = True

    monkeypatch.setattr(runner, "Dossier", Flaky)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    r = _bare({"suppliers": []}, object())
    d = r.build_dossier()
    assert d.plain and calls[-1] is None and len(calls) == 3
    assert [k for k, _ in r.journal.rows] == ["dossier_error", "dossier_error"]


def test_dossier_retry_recovers_with_the_package(monkeypatch):
    calls = []
    pkg = object()

    class Once:
        def __init__(self, case, package):
            calls.append(package)
            if len(calls) == 1:
                raise RuntimeError("transient")
            self.package = package

    monkeypatch.setattr(runner, "Dossier", Once)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    assert _bare({"suppliers": []}, pkg).build_dossier().package is pkg
