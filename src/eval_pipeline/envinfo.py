"""Capture of the machine + backend a document was generated on.

Everything is best-effort: a probe that fails yields an empty string, never
an exception — provenance should not be able to break generation.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import secrets
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

# Machines are recorded under an opaque, stable id rather than their real
# hostname, which would otherwise reach published reports. The id is keyed so
# it cannot be brute-forced back to a short dictionary-word hostname; the key
# lives outside version control, so the same box keeps one id across runs.
SECRET_FILE = Path(__file__).resolve().parents[2] / ".machine_secret"
MACHINE_ID_LEN = 12


@dataclass
class EnvInfo:
    hostname: str
    os: str
    os_version: str
    arch: str
    cpu: str
    gpu: str
    backend: str
    backend_version: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        gpu = f", {self.gpu}" if self.gpu else ""
        return f"{self.hostname}: {self.os} {self.os_version} ({self.arch}{gpu})"


def machine_secret() -> bytes:
    """The local pseudonymization key, created on first use.

    ``EVAL_MACHINE_SECRET`` overrides the file, which is useful when several
    checkouts must agree on the same machine ids.
    """
    env = os.environ.get("EVAL_MACHINE_SECRET")
    if env:
        return env.encode()
    try:
        return SECRET_FILE.read_text().strip().encode()
    except OSError:
        pass
    key = secrets.token_hex(32)
    SECRET_FILE.write_text(key + "\n")
    SECRET_FILE.chmod(0o600)
    return key.encode()


def pseudonymize_hostname(raw: str) -> str:
    """Opaque, stable machine id for a real hostname."""
    if not raw:
        return ""
    return hmac.new(machine_secret(), raw.encode(),
                    hashlib.sha256).hexdigest()[:MACHINE_ID_LEN]


def _run(*cmd: str) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _os_info() -> tuple[str, str]:
    system = platform.system()
    if system == "Darwin":
        return "macOS", platform.mac_ver()[0]
    if system == "Linux":
        pretty = ""
        try:
            for line in open("/etc/os-release"):
                if line.startswith("PRETTY_NAME="):
                    pretty = line.split("=", 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
        version = f"{pretty} ({platform.release()})" if pretty else platform.release()
        return "Linux", version
    return system, platform.release()


def _cpu_name() -> str:
    if platform.system() == "Darwin":
        return _run("sysctl", "-n", "machdep.cpu.brand_string")
    try:
        for line in open("/proc/cpuinfo"):
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _gpu_name(cpu: str) -> str:
    out = _run("nvidia-smi", "--query-gpu=name,memory.total",
               "--format=csv,noheader")
    if out:
        name, _, mem = out.splitlines()[0].partition(",")
        mem = mem.strip()
        return f"{name.strip()} ({mem})" if mem else name.strip()
    if platform.system() == "Darwin" and cpu.startswith("Apple"):
        return f"{cpu} GPU (unified memory)"
    return ""


def collect(backend: str, backend_version: str = "") -> EnvInfo:
    os_name, os_version = _os_info()
    cpu = _cpu_name()
    return EnvInfo(
        hostname=pseudonymize_hostname(socket.gethostname()),
        os=os_name,
        os_version=os_version,
        arch=platform.machine(),
        cpu=cpu,
        gpu=_gpu_name(cpu),
        backend=backend,
        backend_version=backend_version,
    )
