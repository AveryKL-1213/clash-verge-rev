#!/usr/bin/env python3
"""Read-only verification of a local macOS installation against release provenance."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys


APP_ID = "io.github.clash-verge-rev.clash-verge-rev"
SERVICE_ID = APP_ID + ".service"
DEFAULT_APP = Path("/Applications/Clash Verge.app")
# Reviewed service v2.7.7: src/channel.rs, src/core/paths.rs,
# src/bin/install_service.rs, and src/core/manager.rs.
DEFAULT_HELPER = Path("/Library/PrivilegedHelperTools") / (
    SERVICE_ID + ".bundle/Contents/MacOS/clash-verge-service")
RUNTIME_ROOT = Path("/var/run/clash-verge-service")
SERVICE_EXECUTABLE = "Contents/Resources/resources/clash-verge-service"
EXPECTED_EXECUTABLES = {
    "Contents/MacOS/clash-verge",
    "Contents/MacOS/verge-mihomo",
    "Contents/MacOS/verge-mihomo-alpha",
    SERVICE_EXECUTABLE,
    SERVICE_EXECUTABLE + "-install",
    SERVICE_EXECUTABLE + "-uninstall",
}


def load_provenance(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
        raise ValueError("provenance is unexpectedly large")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    executables = value.get("executables")
    if not isinstance(executables, dict) or set(executables) != EXPECTED_EXECUTABLES:
        raise ValueError("provenance must name the six reviewed bundle executables exactly")
    if any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
           for digest in executables.values()):
        raise ValueError("provenance contains an invalid SHA-256")
    version = value.get("version", "unknown")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,128}", version):
        raise ValueError("provenance has an invalid version label")
    return {"version": version, "executables": {
        name: digest.lower() for name, digest in executables.items()}}


def unavailable(error):
    if isinstance(error, FileNotFoundError):
        return {"status": "missing"}
    if isinstance(error, PermissionError):
        return {"status": "permission_limited"}
    return {"status": "unavailable", "errno": getattr(error, "errno", None)}


def metadata(path):
    try:
        info = path.lstat()
    except OSError as error:
        return unavailable(error)
    kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
            "socket" if stat.S_ISSOCK(info.st_mode) else
            "directory" if stat.S_ISDIR(info.st_mode) else
            "regular_file" if stat.S_ISREG(info.st_mode) else "other")
    return {"status": "present", "kind": kind, "uid": info.st_uid,
            "gid": info.st_gid, "mode": format(stat.S_IMODE(info.st_mode), "04o")}


def hash_executable(path):
    details = metadata(path)
    if details["status"] != "present":
        return details
    if details["kind"] != "regular_file":
        return {**details, "status": "unexpected_file_type"}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                return {**details, "status": "unexpected_file_type"}
            if before.st_size > 512 * 1024 * 1024:
                return {**details, "status": "unexpected_file_size"}
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)):
            return {**details, "status": "changed_during_read"}
        details.update(uid=after.st_uid, gid=after.st_gid, mode=format(stat.S_IMODE(after.st_mode), "04o"))
        if not (after.st_mode & 0o111):
            return {**details, "status": "not_executable", "sha256": digest.hexdigest()}
        return {**details, "sha256": digest.hexdigest()}
    except OSError as error:
        return {**details, **unavailable(error)}


def bundle_executable(app, relative):
    # Reject redirects through a user-created symlink inside the app bundle.
    current = app
    for part in (None, *Path(relative).parts[:-1]):
        if part is not None:
            current /= part
        details = metadata(current)
        if details["status"] != "present":
            return details
        if details["kind"] != "directory":
            return {**details, "status": "unexpected_parent_type"}
    return hash_executable(app / relative)


def parse_launchctl(returncode, output, error_output, expected_helper):
    if returncode != 0:
        error = error_output.lower()
        if "permission" in error or "not permitted" in error:
            return {"status": "permission_limited"}
        if "could not find service" in error or "not found" in error:
            return {"status": "not_loaded"}
        return {"status": "unavailable", "exit_code": returncode}
    state = re.search(r"^\s*state = ([a-zA-Z -]{1,40})$", output, re.MULTILINE)
    pid = re.search(r"^\s*pid = ([0-9]+)$", output, re.MULTILINE)
    program = re.search(r"^\s*program = (.+)$", output, re.MULTILINE)
    return {"status": "read", "state": state.group(1) if state else "unknown",
            "pid": int(pid.group(1)) if pid else None,
            "program_matches_expected_helper": bool(program and program.group(1) == str(expected_helper))}


def inspect_launchd(expected_helper):
    # Never execute the helper, including with a supposed --version option.
    try:
        result = subprocess.run(["/bin/launchctl", "print", "system/" + SERVICE_ID],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}
    # Raw output may contain environment values. Only the fixed fields above escape.
    return parse_launchctl(result.returncode, result.stdout, result.stderr, expected_helper)


def inspect_core_ipc(runtime_root, uid):
    directory = runtime_root / "users" / str(uid)
    socket = directory / "verge-mihomo.sock"
    parent = metadata(directory)
    entry = metadata(socket)
    parent["private_for_current_user"] = (
        parent.get("kind") == "directory" and parent.get("uid") == uid and parent.get("mode") == "0700")
    entry["private_for_current_user"] = (
        entry.get("kind") == "socket" and entry.get("uid") == uid and entry.get("mode") == "0600")
    return {"path": str(socket), "directory": parent, "socket": entry,
            "private": parent["private_for_current_user"] and entry["private_for_current_user"],
            "api": "not queried; no configuration or authentication secret is read"}


def verify(provenance, app=DEFAULT_APP, helper=DEFAULT_HELPER, runtime_root=RUNTIME_ROOT,
           uid=None, launchd_reader=inspect_launchd):
    uid = os.getuid() if uid is None else uid
    executables = []
    for relative, expected in sorted(provenance["executables"].items()):
        details = bundle_executable(app, relative)
        if details["status"] == "present":
            details["status"] = "match" if details["sha256"] == expected else "hash_mismatch"
        executables.append({"path": relative, **details})
    installed = hash_executable(helper)
    expected = provenance["executables"][SERVICE_EXECUTABLE]
    bundled = next(item for item in executables if item["path"] == SERVICE_EXECUTABLE)
    installed["matches_provenance"] = installed.get("sha256") == expected
    installed["matches_bundle"] = (installed.get("sha256") == bundled.get("sha256")
                                   if installed.get("sha256") and bundled.get("sha256") else None)
    installed["root_owned_not_group_or_world_writable"] = (
        installed.get("uid") == 0 and not (int(installed.get("mode", "7777"), 8) & 0o022))
    launchd = launchd_reader(helper)
    ipc = inspect_core_ipc(runtime_root, uid)
    ready = (all(item["status"] == "match" for item in executables)
             and installed["status"] == "present"
             and installed["matches_provenance"] and installed["matches_bundle"]
             and installed["root_owned_not_group_or_world_writable"]
             and launchd.get("state") == "running" and launchd.get("program_matches_expected_helper")
             and ipc["private"])
    return {"status": "checks_passed" if ready else "not_verified",
            "expected_release": provenance["version"], "app": str(app),
            "executables": executables, "helper": {"path": str(helper), **installed},
            "launchd": {"label": SERVICE_ID, **launchd}, "current_user_uid": uid,
            "core_ipc": ipc,
            "limits": ["A missing or different old installation is a reported finding, not a script crash.",
                       "Permission-limited checks remain unverified; this script never invokes sudo.",
                       "Matching files and socket permissions do not prove the running core version or gateway/TUN traffic."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provenance", type=Path, help="Final artifact/provenance.json")
    parser.add_argument("--app", type=Path, default=DEFAULT_APP,
                        help="Bundle to inspect; defaults to the installed production app")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("this installation verifier supports macOS only")
    try:
        provenance = load_provenance(args.provenance.expanduser())
        result = verify(provenance, app=args.app.expanduser())
    except (OSError, ValueError, TypeError, AttributeError):
        print(json.dumps({"status": "input_error", "reason": "provenance is unreadable or invalid; no private content emitted"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "checks_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
