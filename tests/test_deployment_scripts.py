"""Exercise deployment ordering and failure boundaries without touching a server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/synthetic/onw@sha256:" + "a" * 64
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux deployment scripts")

FAKE_DOCKER = r"""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["TRACE"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
failure = os.environ.get("FAILURE", "")
if args[0] == "ps":
    print("existing")
elif args[0] == "inspect":
    if "Mounts" in args[2]:
        for name, path in [("data", ".open-net-worth"), ("codex", ".codex"),
                           ("claude", ".claude"), ("opencode", ".local/share/opencode")]:
            print(f"open-net-worth-{name} /home/onw/{path}")
    elif "Labels" in args[2]:
        print("other-project" if failure == "project" else "open-net-worth")
    else:
        print("previous-image")
elif "config" in args and "json" in args:
    env = {"ONW_DATABASE_URL": "postgresql://synthetic"} if failure == "config" else {}
    print(json.dumps({"services": {"app": {"environment": env}}}))
elif (failure == "pull" and "pull" in args or
      failure == "backup" and args[0] == "run" or
      failure == "health" and "up" in args):
    sys.exit(1)
"""


def run_deploy(tmp_path, failure=""):
    executable = tmp_path / "docker"
    executable.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER)
    executable.chmod(0o700)
    (tmp_path / ".env").write_text("AUTH_PASSWORD=synthetic\n")
    (tmp_path / "compose.next.yml").write_text("services: {}\n")
    (tmp_path / "current-image").write_text("old-healthy-image\n")
    trace = tmp_path / "trace"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/deploy-server.sh"), str(tmp_path), IMAGE],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TRACE": str(trace),
            "FAILURE": failure,
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, [json.loads(line) for line in trace.read_text().splitlines()]


def test_snapshot_finishes_before_new_image_can_write(tmp_path):
    result, calls = run_deploy(tmp_path)
    assert result.returncode == 0
    stop = next(i for i, args in enumerate(calls) if args[0] == "stop")
    snapshots = [i for i, args in enumerate(calls) if args[0] == "run"]
    start = next(i for i, args in enumerate(calls) if "up" in args)
    assert len(snapshots) == 4
    assert stop < min(snapshots) < max(snapshots) < start
    assert len(list((tmp_path / "backups").glob("*.tgz"))) == 4
    assert (tmp_path / "current-image").read_text().strip() == IMAGE
    assert (tmp_path / "previous-image").read_text().strip() == "previous-image"
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["config", "project", "pull"])
def test_preflight_failure_never_stops_existing_app(tmp_path, failure):
    result, calls = run_deploy(tmp_path, failure)
    assert result.returncode != 0
    assert not any(args[0] == "stop" or "up" in args for args in calls)
    assert (tmp_path / "current-image").read_text().strip() == "old-healthy-image"


def test_failed_snapshot_cannot_start_migrations(tmp_path):
    result, calls = run_deploy(tmp_path, "backup")
    assert result.returncode != 0
    assert any(args[0] == "stop" for args in calls)
    assert not any("up" in args for args in calls)
    assert (tmp_path / "backup-status").read_text().strip() == "snapshotting"


def test_unhealthy_deploy_preserves_last_healthy_digest_and_snapshot(tmp_path):
    result, calls = run_deploy(tmp_path, "health")
    assert result.returncode != 0
    assert any("--wait" in args for args in calls)
    assert (tmp_path / "backup-status").read_text().startswith("complete ")
    assert (tmp_path / "current-image").read_text().strip() == "old-healthy-image"
    assert (tmp_path / "desired-image").read_text().strip() == IMAGE
    assert not any("previous-image" in args for args in calls if "up" in args)


def test_ssh_failure_does_not_publish_connection_diagnostics(tmp_path):
    fake = tmp_path / "scp"
    fake.write_text('#!/bin/sh\necho "deployment.invalid private-diagnostic" >&2\nexit 1\n')
    fake.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DEPLOY_HOST": "deployment.invalid",
        "DEPLOY_USER": "deployer",
        "DEPLOY_PATH": "/srv/synthetic",
        "DEPLOY_SSH_KEY": "synthetic-key",
        "DEPLOY_KNOWN_HOSTS": "synthetic-host-key",
        "GITHUB_REPOSITORY": "synthetic/onw",
        "IMAGE_DIGEST": "sha256:" + "a" * 64,
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/deploy-via-ssh.sh")],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "transfer failed" in result.stdout
    assert "deployment.invalid" not in result.stdout + result.stderr
    assert "private-diagnostic" not in result.stdout + result.stderr
    assert "synthetic-key" not in result.stdout + result.stderr
