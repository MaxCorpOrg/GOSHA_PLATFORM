"""Verify this patch on copied, exact candidate files, never on an installed product.

Run with a Python environment containing aiohttp. This script does not install
packages, access the network, run the physics engine or change the source tree.
"""
from datetime import datetime, timezone
from importlib.metadata import version
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-studio", required=True, type=Path,
                        help="Read-only source: candidate platform/motion_studio directory")
    parser.add_argument("--output", required=True, type=Path, help="New isolated result directory")
    args = parser.parse_args()
    packet = Path(__file__).resolve().parent
    manifest = json.loads((packet / "source_manifest.json").read_text())
    patch = packet / "motion-physics-body-limit.patch"
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == manifest["patch_sha256"], "patch changed"
    aiohttp_version = version("aiohttp")  # Missing dependency is a refusal, never a passing test.
    source = args.source_studio.resolve(strict=True)
    contents = {}
    for name, expected in manifest["expected_github_blobs"].items():
        data = (source / name).read_bytes()
        assert blob(data) == expected, "Source mismatch, do not apply blindly: " + name
        contents[name] = data
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {"started_at": datetime.now(timezone.utc).isoformat(),
               "candidate_commit": manifest["candidate_commit"],
               "patch_sha256": manifest["patch_sha256"],
               "python": sys.version.split()[0], "aiohttp": aiohttp_version,
               "source_blobs": manifest["expected_github_blobs"], "results": [],
               "scope": "Copied files, synthetic loopback HTTP, engine readiness and worker mocked. No real engine, worker subprocess, USB, robot, phone, live product, service or source-tree change."}
    clean_env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    for label in ("baseline", "corrected"):
        studio = output / label / "platform/motion_studio"
        for name, data in contents.items():
            dest = studio / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for package in ("bridge", "physics"):
            (studio / package / "__init__.py").write_text("# Isolated fixture: no hardware imports.\n")
    for check in (True, False):
        command = ["git", "apply"] + (["--check"] if check else []) + [str(patch)]
        applied = subprocess.run(command, cwd=output / "corrected", env=clean_env,
                                 capture_output=True, text=True, timeout=10)
        if applied.returncode:
            raise RuntimeError("Exact patch application failed: " + applied.stderr)
    corrected = output / "corrected/platform/motion_studio"
    assert hashlib.sha256((corrected / "bridge/physics_server.py").read_bytes()).hexdigest() == manifest["fixed_server_sha256"]
    shutil.copyfile(corrected / "tests/test_physics_body_limit.py",
                    output / "baseline/platform/motion_studio/tests/test_physics_body_limit.py")
    selections = {
        "baseline": ["test_physics_body_limit.PhysicsBodyLimitTests.test_chunked_limit_plus_one"],
        "corrected": ["test_physics_body_limit", "test_physics_server",
                      "test_physics_model.PhysicsModelTests.test_validation_does_not_modify_the_authoring_document",
                      "test_physics_model.PhysicsModelTests.test_bad_versions_units_angles_times_and_parameters_are_rejected",
                      "test_physics_model.PhysicsModelTests.test_mjcf_has_free_base_six_cad_axes_correct_mass_and_bounded_drives"],
    }
    logs = {}
    for label, selected in selections.items():
        studio = output / label / "platform/motion_studio"
        home, tmp = output / (label + "-home"), output / (label + "-tmp")
        home.mkdir(); tmp.mkdir()
        env = dict(clean_env, HOME=str(home), TMPDIR=str(tmp),
                   PYTHONPATH=str(studio) + os.pathsep + str(studio / "tests"))
        started = datetime.now(timezone.utc).isoformat()
        # Keep the venv interpreter path. Resolving its symlink would lose the venv.
        result = subprocess.run([sys.executable, "-m", "unittest", "-v", *selected],
                                cwd=studio, env=env, capture_output=True, text=True, timeout=90)
        logs[label] = result.stdout + result.stderr
        (output / (label + ".txt")).write_text(logs[label])
        receipt["results"].append({"label": label, "exit_code": result.returncode,
                                   "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat()})
    receipt["baseline_expected_assertion"] = (receipt["results"][0]["exit_code"] != 0
        and "FAIL: test_chunked_limit_plus_one" in logs["baseline"]
        and "202 != 413" in logs["baseline"] and "ERROR:" not in logs["baseline"])
    receipt["corrected_21_tests_passed"] = (receipt["results"][1]["exit_code"] == 0
        and "Ran 21 tests" in logs["corrected"] and "\nOK\n" in logs["corrected"])
    receipt["source_unchanged"] = all((source / name).read_bytes() == data for name, data in contents.items())
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["pass"] = all(receipt[key] for key in ("baseline_expected_assertion", "corrected_21_tests_passed", "source_unchanged"))
    (output / "RECEIPT.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
