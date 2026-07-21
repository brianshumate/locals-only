"""Environment capture is best-effort but must always return a full record."""

from eval_pipeline import envinfo


def test_collect_returns_complete_record():
    env = envinfo.collect("llamacpp", backend_version="b42")
    d = env.as_dict()
    assert set(d) == {"hostname", "os", "os_version", "arch", "cpu", "gpu",
                      "backend", "backend_version"}
    assert d["hostname"]
    assert d["os"]
    assert d["arch"]
    assert d["backend"] == "llamacpp"
    assert d["backend_version"] == "b42"


def test_summary_mentions_host_and_os():
    env = envinfo.EnvInfo(hostname="h", os="Linux", os_version="6.1",
                          arch="x86_64", cpu="cpu", gpu="RTX 3090",
                          backend="llamacpp")
    assert "h" in env.summary() and "Linux" in env.summary()
    assert "RTX 3090" in env.summary()


def test_probes_never_raise(monkeypatch):
    def boom(*a, **k):
        raise OSError("no such binary")
    monkeypatch.setattr(envinfo.subprocess, "run", boom)
    env = envinfo.collect("lmstudio")
    assert env.backend == "lmstudio"
