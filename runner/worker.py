import os
from pathlib import Path


root = Path("/var/lib/vyamoh-ci")
for name in ("home", "tmp", "toolcache"):
    (root / name).mkdir(mode=0o700, exist_ok=True)
os.environ["ACTIONS_RUNNER_INPUT_JITCONFIG"] = (
    Path(os.environ.pop("CREDENTIALS_DIRECTORY")) / "jit").read_text()
os.chdir(root / "runner")
os.execv("bin/Runner.Listener", ["Runner.Listener", "run"])
