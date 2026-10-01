import json
import os
from pathlib import Path
import subprocess


def main():
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
