#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || exit 1
[[ $(id -u vyamoh-ci) == 997 && $(id -g vyamoh-ci) == 987 ]] || exit 1
[[ $(stat -c '%U:%G:%a' /etc/vyamoh-ci-router/infisical.env) == root:root:600 ]] || exit 1
[[ $(stat -c '%U:%G:%a' /etc/vyamoh-ci-router) == root:root:700 ]] || exit 1

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
install -d -m 0755 -o root -g root /opt/vyamoh-ci /opt/vyamoh-ci/bin /opt/vyamoh-ci/runner
install -d -m 0700 -o root -g root /etc/vyamoh-ci-router
install -d -m 0700 -o vyamoh-ci-router -g vyamoh-ci-router /var/lib/vyamoh-ci-router
install -d -m 0700 -o vyamoh-ci -g vyamoh-ci /var/lib/vyamoh-ci

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  ca-certificates curl git jq python3 python3-venv openssl libicu74 libssl3t64 zlib1g libkrb5-3 liblttng-ust1t64

temporary=$(mktemp -d /opt/vyamoh-ci/download.XXXXXXXX)
trap 'rm -rf -- "$temporary"' EXIT
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  https://github.com/actions/runner/releases/download/v2.337.0/actions-runner-linux-x64-2.337.0.tar.gz \
  -o "$temporary/runner.tgz"
printf '%s  %s\n' 70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613 "$temporary/runner.tgz" | sha256sum --check
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  https://clireleases.blacksmith.sh/cli/latest/linux/amd64/blacksmith \
  -o "$temporary/blacksmith"
printf '%s  %s\n' cb81f90fc4035a0c6b9338e9ccd17d6aaae6eda020665cd51c635b228e6a7598 "$temporary/blacksmith" | sha256sum --check

systemctl stop vyamoh-ci-router.timer vyamoh-ci-cycle.service vyamoh-ci-worker.service 2>/dev/null || true
systemctl stop vyamoh-ci-router.service vyamoh-ci-broker.service 2>/dev/null || true
install -m 0755 -o root -g root "$temporary/blacksmith" /opt/vyamoh-ci/bin/blacksmith
mkdir "$temporary/runner-dist"
tar --extract --gzip --file "$temporary/runner.tgz" --directory "$temporary/runner-dist" --no-same-owner
rm -rf /opt/vyamoh-ci/runner-dist.previous
if [[ -d /opt/vyamoh-ci/runner-dist ]]; then
  mv /opt/vyamoh-ci/runner-dist /opt/vyamoh-ci/runner-dist.previous
fi
mv "$temporary/runner-dist" /opt/vyamoh-ci/runner-dist
chmod -R go-w /opt/vyamoh-ci/runner-dist
install -m 0644 -o root -g root "$source_dir/"*.py /opt/vyamoh-ci/runner/
install -m 0644 -o root -g root "$source_dir/config.json" /opt/vyamoh-ci/config.json
install -m 0644 -o root -g root "$source_dir/systemd/"* /etc/systemd/system/

install -d -m 0755 /etc/systemd/system/vyamoh-ci-worker.service.d
ip -j address show | python3 -c '
import ipaddress, json, sys
from pathlib import Path
addresses = []
for interface in json.load(sys.stdin):
    for info in interface["addr_info"]:
        address = ipaddress.ip_address(info["local"])
        if not address.is_loopback:
            addresses.append(str(address))
Path("/etc/systemd/system/vyamoh-ci-worker.service.d/host-addresses.conf").write_text(
    "[Service]\n" + "".join("IPAddressDeny=" + value + "\n" for value in addresses))
'
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/vyamoh-ci-{router,broker,worker,cycle}.service /etc/systemd/system/vyamoh-ci-router.timer
systemctl start vyamoh-ci-router.service
systemctl enable --now vyamoh-ci-router.timer vyamoh-ci-cycle.service
systemctl is-active vyamoh-ci-router.timer vyamoh-ci-cycle.service
echo 'Controller and runner lifecycle enabled. Worker registration and live smoke verification are next.'
