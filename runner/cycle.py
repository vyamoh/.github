import json
import os
from pathlib import Path
import subprocess
import shutil


def main():
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(memory["MemAvailable"].split()[0]) * 1024
    free = min(shutil.disk_usage(path).free for path in ("/var/lib", "/var/log"))
    print(f"Host available RAM={available} free disk={free}", flush=True)
    if available < 1024**3 or free < 5 * 1024**3:
        raise RuntimeError("Host resource reserve is low; deferring worker registration")
    subprocess.run(["systemctl", "start", "vyamoh-ci-broker.service"], check=True)
    source = Path("/var/lib/vyamoh-ci-router/jit.json")
    jit = json.loads(source.read_text())["jit"]
    source.unlink()
    if not isinstance(jit, str) or not 100 < len(jit) < 100000:
        raise ValueError("Invalid runner configuration")
    credential = Path("/run/vyamoh-ci-cycle/jit")
    credential.write_text(jit)
    os.chmod(credential, 0o600)
    try:
        subprocess.run(["systemctl", "start", "--wait", "vyamoh-ci-worker.service"], check=True)
    finally:
        subprocess.run(["systemctl", "stop", "vyamoh-ci-worker.service"], check=True)
        credential.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
