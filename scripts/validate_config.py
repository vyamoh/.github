import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
for directory in [root, root / "policy", root / "workflow-templates"]:
    for path in directory.glob("*.json"):
        json.loads(path.read_text())

base = json.loads((root / "renovate-config.json").read_text())
assert base["minimumReleaseAge"] == "7 days"
assert base["internalChecksFilter"] == "strict"
assert base["vulnerabilityAlerts"]["minimumReleaseAge"] is None
assert base["rangeStrategy"] == "replace"
assert base["automerge"] is True
assert base["automergeType"] == "pr"
assert base["automergeStrategy"] == "rebase"
assert base["platformAutomerge"] is False
assert base["rebaseWhen"] == "auto"
for path in (root / "workflow-templates").glob("*.properties.json"):
    definition = json.loads(path.read_text())
    assert definition["name"] and definition["description"]
    workflow = path.with_name(path.name.replace(".properties.json", ".yml"))
    assert workflow.exists(), workflow
print("Shared configuration validated")
