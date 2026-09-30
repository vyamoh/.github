from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release


def ref(version, sha="previous"):
    return {"ref": "refs/tags/" + version, "object": {"type": "commit", "sha": sha}}


class ReleaseTests(unittest.TestCase):
    def test_first_release_creates_version_and_channel(self):
        self.assertEqual(release.release_plan("v1.0.0", "head", []), {
            "version": "v1.0.0", "channel": "v1", "sha": "head",
            "create_version": True, "create_channel": True})

    def test_retry_repairs_channel_without_rewriting_version(self):
        plan = release.release_plan("v1.0.1", "head", [ref("v1.0.1", "head"), ref("v1")])
        self.assertFalse(plan["create_version"])
        self.assertFalse(plan["create_channel"])

    def test_existing_version_cannot_move(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            release.release_plan("v1.0.1", "head", [ref("v1.0.1")])

    def test_channel_cannot_downgrade(self):
        with self.assertRaisesRegex(ValueError, "backwards"):
            release.release_plan("v1.1.0", "head", [ref("v1.2.0")])

    def test_major_release_leaves_old_channel_alone(self):
        plan = release.release_plan("v2.0.0", "head", [ref("v1.8.2"), ref("v1")])
        self.assertEqual(plan["channel"], "v2")
        self.assertTrue(plan["create_channel"])

    def test_nonstable_and_invalid_versions_are_rejected(self):
        for version in ["1.0.0", "v0.1.0", "v1", "v1.0.0-rc.1", "v1.01.0", "v1.0.0\n"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.version_tuple(version)


if __name__ == "__main__":
    unittest.main()
