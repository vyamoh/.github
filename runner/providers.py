import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


class ApiError(RuntimeError):
    def __init__(self, status):
        super().__init__(f"Provider request failed (HTTP {status})")
        self.status = status


def request(url, token=None, body=None, method=None):
    headers = {"Accept": "application/json", "User-Agent": "vyamoh-ci-router",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code) from None
    except urllib.error.URLError:
        raise RuntimeError("Provider connection failed") from None


def load_secrets():
    credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "infisical.env"
    values = {}
    for line in credential.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    domain = values["INFISICAL_DOMAIN"]
    if domain != "https://app.infisical.com":
        raise ValueError("Unexpected Infisical host")
    auth = request(domain + "/api/v1/auth/universal-auth/login", body={
        "clientId": values["INFISICAL_CLIENT_ID"],
        "clientSecret": values["INFISICAL_CLIENT_SECRET"]})
    if not 0 < auth["expiresIn"] <= 3600:
        raise ValueError("Infisical token lifetime exceeds one hour")
    query = urllib.parse.urlencode({
        "projectId": values["INFISICAL_PROJECT_ID"],
        "environment": values["INFISICAL_ENVIRONMENT"],
        "secretPath": values["INFISICAL_SECRET_PATH"],
        "includeImports": "false", "recursive": "false", "expandSecretReferences": "false"})
    secrets = request(domain + "/api/v4/secrets?" + query, auth["accessToken"])["secrets"]
    wanted = {"GITHUB_APP_PRIVATE_KEY", "BLACKSMITH_ORG_TOKEN"}
    result = {s["secretKey"]: s["secretValue"] for s in secrets if s["secretKey"] in wanted}
    if set(result) != wanted or not all(result.values()):
        raise ValueError("Required provider credentials are missing")
    return result


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


class GitHub:
    def __init__(self, config, key):
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 540, "iss": str(config["app_id"])}
        message = encoded(b'{"alg":"RS256","typ":"JWT"}') + b"." + encoded(json.dumps(payload).encode())
        with tempfile.NamedTemporaryFile(mode="w", prefix="github-key-") as pem:
            pem.write(key)
            pem.flush()
            signed = subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-sign", pem.name],
                                    input=message, capture_output=True, check=True).stdout
        jwt = (message + b"." + encoded(signed)).decode()
        response = request(f"https://api.github.com/app/installations/{config['installation_id']}/access_tokens",
                           jwt, body={})
        self.token = response["token"]
        self.org = config["organization"]

    def call(self, path, body=None, method=None):
        return request("https://api.github.com/" + path, self.token, body, method)

    def pages(self, path, key):
        results = []
        for page in range(1, 101):
            result = self.call(f"{path}?per_page=100&page={page}")[key]
            results.extend(result)
            if len(result) < 100:
                return results
        raise ValueError("Pagination limit reached")

    def variable(self, name):
        try:
            return self.call(f"orgs/{self.org}/actions/variables/{name}")["value"]
        except ApiError as exc:
            if exc.status != 404:
                raise
            return None

    def publish(self, state):
        name = "CI_ROUTING_STATE"
        path = f"orgs/{self.org}/actions/variables"
        body = {"name": name, "value": json.dumps(state, separators=(",", ":")), "visibility": "private"}
        if self.variable(name) is None:
            self.call(path, body, "POST")
        else:
            self.call(path + "/" + name, body, "PATCH")


def blacksmith_usage(config, token, start, end):
    env = {"PATH": "/usr/bin:/bin", "HOME": "/var/lib/vyamoh-ci-router",
           "BLACKSMITH_DISABLE_AUTO_UPDATE": "1", "BLACKSMITH_ADMIN_KEY": token,
           "BLACKSMITH_ORG": config["organization"]}
    result = subprocess.run([
        "/opt/vyamoh-ci/bin/blacksmith", "usage", "--format", "json",
        "--start-time", start.isoformat().replace("+00:00", "Z"),
        "--end-time", end.isoformat().replace("+00:00", "Z")],
        env=env, capture_output=True, timeout=45, check=True)
    return json.loads(result.stdout)
