import os
from pathlib import Path


def require_tmpfs(mountinfo, root):
    mounts = []
    for line in mountinfo.splitlines():
        location, filesystem = line.split(" - ", 1)
        if location.split()[4] == str(root):
            mounts.append(filesystem.split()[0])
    if mounts != ["tmpfs"]:
        raise RuntimeError("Worker storage must be a fresh tmpfs; refusing jobs")


def main():
    root = Path("/var/lib/vyamoh-ci")
    require_tmpfs(Path("/proc/self/mountinfo").read_text(), root)
    storage = os.statvfs(root)
    if storage.f_blocks * storage.f_frsize > 1024**3:
        raise RuntimeError("Worker storage exceeds its 1 GiB limit")
    for name in ("home", "tmp", "toolcache"):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    os.environ["ACTIONS_RUNNER_INPUT_JITCONFIG"] = (
        Path(os.environ.pop("CREDENTIALS_DIRECTORY")) / "jit").read_text()
    os.chdir(root)
    os.execv("bin/Runner.Listener", ["Runner.Listener", "run"])


if __name__ == "__main__":
    main()
