from pathlib import Path
import socket


relative = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
assert int((cgroup / "memory.max").read_text()) <= 1024**3
assert int((cgroup / "memory.swap.max").read_text()) <= 512 * 1024**2
quota, period = map(int, (cgroup / "cpu.max").read_text().split())
assert quota <= period
for address in ["127.0.0.1", "169.254.169.254", "10.0.0.1", "100.100.100.100"]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(2)
        try:
            connection.sendto(b"test", (address, 80))
        except PermissionError:
            continue
        raise AssertionError("Network policy did not reject " + address)
print("Resource limits and denied private networks verified")
