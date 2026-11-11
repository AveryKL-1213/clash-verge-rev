#!/usr/bin/env python3
"""Read-only macOS gateway inspection and private, explicit-destination backups."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone


APP_ID = "io.github.clash-verge-rev.clash-verge-rev"
SERVICE_ID = APP_ID + ".service"
DEFAULT_APP = Path("/Applications/Clash Verge.app")
DEFAULT_CONFIG = Path.home() / "Library/Application Support" / APP_ID


def run_readonly(argv, timeout=20):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"command": argv, "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": argv, "exit_code": None, "error": str(error)}


def private_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def validate_output(value, sources):
    output = Path(value).expanduser()
    if not output.is_absolute():
        raise ValueError("--output must be an absolute path to a new directory")
    output = output.resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("--output must not exist; its parent directory must already exist")
    if any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise ValueError("backups must be outside Git worktrees")
    for source in sources:
        resolved = source.resolve()
        if output == resolved or resolved in output.parents:
            raise ValueError("the output directory must not be inside a backup source")
    return output


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_directory(source, destination):
    result = run_readonly([
        "/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
        str(source), str(destination),
    ], timeout=900)
    if result.get("exit_code") != 0:
        raise RuntimeError("archive creation failed for " + source.name)
    destination.chmod(0o600)
    return {"file": destination.name, "sha256": file_digest(destination),
            "bytes": destination.stat().st_size}


def app_identity(app):
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    return {key: info.get(key) for key in (
        "CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleVersion")}


def inspect(app, config):
    result = {
        "app_exists": app.is_dir(), "config_exists": config.is_dir(),
        "service_plist_exists": Path("/Library/LaunchDaemons", SERVICE_ID + ".plist").exists(),
        "note": "Inspection does not test client traffic or change service/network state.",
    }
    if app.is_dir():
        result["app"] = app_identity(app)
    for key in ("net.inet.ip.forwarding", "net.inet6.ip6.forwarding", "net.inet.ip.redirect"):
        value = run_readonly(["/usr/sbin/sysctl", "-n", key])
        result[key] = value["stdout"].strip() if value.get("exit_code") == 0 else "unavailable"
    print(json.dumps(result, ensure_ascii=False, indent=2))


def network_snapshot(output, sudo_pf=False):
    commands = {
        "os": ["/usr/bin/sw_vers"],
        "architecture": ["/usr/bin/uname", "-m"],
        "forwarding": ["/usr/sbin/sysctl", "net.inet.ip.forwarding",
                       "net.inet6.ip6.forwarding", "net.inet.ip.redirect"],
        "routes_ipv4": ["/usr/sbin/netstat", "-rn", "-f", "inet"],
        "routes_ipv6": ["/usr/sbin/netstat", "-rn", "-f", "inet6"],
        "interfaces": ["/sbin/ifconfig"],
        "dns": ["/usr/sbin/scutil", "--dns"],
        "proxy": ["/usr/sbin/scutil", "--proxy"],
        "service": ["/bin/launchctl", "print", "system/" + SERVICE_ID],
        "pf_status": ["/sbin/pfctl", "-s", "info"],
        "pf_rules": ["/sbin/pfctl", "-s", "rules"],
        "pf_nat": ["/sbin/pfctl", "-s", "nat"],
        "pf_anchors": ["/sbin/pfctl", "-s", "Anchors"],
        "pf_anchor_rules": ["/sbin/pfctl", "-a", "*", "-s", "rules"],
        "pf_anchor_nat": ["/sbin/pfctl", "-a", "*", "-s", "nat"],
    }
    if sudo_pf:
        for name, argv in commands.items():
            if name.startswith("pf_"):
                commands[name] = ["/usr/bin/sudo", "-n", *argv]
    results = {name: run_readonly(argv) for name, argv in commands.items()}
    services = run_readonly(["/usr/sbin/networksetup", "-listallnetworkservices"])
    results["network_services"] = services
    service_details = []
    if services.get("exit_code") == 0:
        for line in services["stdout"].splitlines()[1:]:
            name = line.lstrip("*").strip()
            if not name:
                continue
            details = {"name": name, "disabled": line.startswith("*")}
            for operation in ("-getinfo", "-getdnsservers", "-getwebproxy",
                              "-getsecurewebproxy", "-getsocksfirewallproxy",
                              "-getautoproxyurl", "-getproxybypassdomains"):
                details[operation] = run_readonly(["/usr/sbin/networksetup", operation, name])
            service_details.append(details)
    results["per_network_service"] = service_details
    private_json(output / "network-state.json", results)
    unavailable = [name for name, result in results.items()
                   if isinstance(result, dict) and result.get("exit_code") != 0]
    for details in service_details:
        unavailable.extend(details["name"] + ":" + key for key, value in details.items()
                           if isinstance(value, dict) and value.get("exit_code") != 0)
    return unavailable


def configuration_snapshot(output):
    directory = output / "system-config"
    directory.mkdir(mode=0o700)
    sources = [Path("/etc/pf.conf"),
               Path("/Library/LaunchDaemons", SERVICE_ID + ".plist"),
               Path.home() / "Library/LaunchAgents" / (APP_ID + ".plist")]
    anchors = Path("/etc/pf.anchors")
    if anchors.is_dir():
        sources.extend(p for p in anchors.rglob("*") if p.is_file() and not p.is_symlink())
    copied = []
    errors = []
    for index, source in enumerate(sources):
        if not source.is_file():
            continue
        destination = directory / (str(index) + "-" + source.name)
        try:
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
            copied.append({"source": str(source), "copy": str(destination.relative_to(output)),
                           "sha256": file_digest(destination)})
        except OSError as error:
            errors.append({"source": str(source), "error": str(error)})
    private_json(output / "system-config-index.json", {"files": copied, "errors": errors})
    return len(errors)


def backup(args):
    app = args.app.expanduser().resolve()
    config = args.config.expanduser().resolve()
    if not (app / "Contents/Info.plist").is_file() or not config.is_dir():
        raise ValueError("the app bundle and configuration directory must both exist")
    output = validate_output(args.output, [app, config])
    output.mkdir(mode=0o700)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(),
                "app": app_identity(app), "app_source": str(app),
                "config_source": str(config), "archives": [], "complete": False,
                "warning": "Private, unencrypted backup; do not commit, upload, or share it.",
                "consistency": "Live snapshot: files can change while the app/core is running."}
    try:
        manifest["archives"].append(archive_directory(app, output / "installed-app.zip"))
        manifest["archives"].append(archive_directory(config, output / "application-config.zip"))
        manifest["unavailable_diagnostics"] = network_snapshot(output, args.sudo_pf)
        manifest["system_config_copy_errors"] = configuration_snapshot(output)
        manifest["complete"] = True
    finally:
        private_json(output / "manifest.json", manifest)
    print("Private backup written to: " + str(output))
    print("Archives complete; unavailable diagnostics: " + str(len(manifest["unavailable_diagnostics"])))
    print("Keep this directory private and encrypt it before copying to shared/cloud storage.")
    print("No app, service, route, DNS, proxy, forwarding, or PF setting was changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("inspect", "backup"):
        command = sub.add_parser(operation)
        command.add_argument("--app", type=Path, default=DEFAULT_APP)
        command.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        if operation == "backup":
            command.add_argument("--output", required=True,
                                 help="Absolute path to a new private directory outside Git")
            command.add_argument("--sudo-pf", action="store_true",
                                 help="Use sudo -n only for read-only PF status/rules; never prompt")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("this script supports macOS only")
    os.umask(0o077)
    try:
        if args.operation == "inspect":
            inspect(args.app.expanduser(), args.config.expanduser())
        else:
            backup(args)
    except (OSError, ValueError, RuntimeError, plistlib.InvalidFileException) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
