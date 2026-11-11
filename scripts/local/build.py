#!/usr/bin/env python3
"""Build the local macOS gateway without installing or starting it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "local-build/manifest.json").read_text())
SERVICE = json.loads((ROOT / "local-build/service-source.json").read_text())
TARGET = SPEC["target"]


def digest(path):
    with Path(path).open("rb") as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()


def run(args, cwd=ROOT, env=None, capture=False):
    print("+ " + " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True,
                          text=True, stdout=subprocess.PIPE if capture else None).stdout


def fetch(url, path, sha, headers=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and digest(path) == sha:
        return
    temporary = path.with_name(path.name + ".download")
    try:
        run(["curl", "--fail", "--location", "--retry", "2", "--proto", "=https",
             "--tlsv1.2", *[arg for header in headers for arg in ("--header", header)],
             url, "--output", temporary])
        if digest(temporary) != sha:
            raise RuntimeError(f"Checksum mismatch: {url}; refusing changed input")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def environment(state):
    tool = state / "toolchains"
    rust = tool / "rustup/toolchains" / f"{SPEC['rust']}-{TARGET}"
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("TAURI_", "CARGO_BUILD_", "CARGO_TARGET_", "CARGO_PROFILE_")):
            env.pop(key)
    for key in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                "RUSTC", "RUSTDOC", "GOFLAGS", "GOENV", "GOWORK", "NODE_PATH", "NODE_OPTIONS"):
        env.pop(key, None)
    env.update({
        "PATH": os.pathsep.join(map(str, [tool / "node/bin", tool / "pnpm/node_modules/.bin",
                                           rust / "bin", tool / "go/bin"])) + os.pathsep + env["PATH"],
        "RUSTUP_HOME": str(tool / "rustup"),
        "RUSTUP_TOOLCHAIN": SPEC["rust"],
        "CARGO_HOME": str(state / "cache/cargo"),
        "CARGO_TARGET_DIR": str(state / "target/app"),
        "CARGO_BUILD_JOBS": "4",
        "GOPATH": str(state / "cache/go"),
        "GOCACHE": str(state / "cache/go-build"),
        "GOTOOLCHAIN": "local",
        "CGO_ENABLED": "0",
        "GOOS": "darwin",
        "GOARCH": "arm64",
        "HUSKY": "0",
        "npm_config_cache": str(state / "cache/npm"),
        "npm_config_manage_package_manager_versions": "false",
        "VITE_LOCAL_BUILD": "true",
        "APPLE_SIGNING_IDENTITY": "-",
        "NODE_OPTIONS": "--max-old-space-size=4096",
        "DYLD_LIBRARY_PATH": str(rust / "lib"),
    })
    for key in ("APPLE_CERTIFICATE", "APPLE_CERTIFICATE_PASSWORD", "APPLE_ID", "APPLE_PASSWORD",
                "APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH", "APPLE_TEAM_ID",
                "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"):
        env.pop(key, None)
    return env


def bootstrap(state, env):
    tool = state / "toolchains"
    for name in ("node", "go"):
        spec = SPEC[name]
        archive = tool / "downloads" / f"{name}.tar.gz"
        fetch(spec["url"], archive, spec["sha256"])
        executable = tool / name / "bin" / name
        if not executable.exists():
            (tool / name).mkdir(parents=True, exist_ok=True)
            args = ["tar", "-xzf", archive, "-C", tool / name, "--strip-components=1"]
            run(args)
    rust = tool / "rustup/toolchains" / f"{SPEC['rust']}-{TARGET}/bin/rustc"
    if not rust.exists():
        run(["rustup", "toolchain", "install", SPEC["rust"], "--profile", "minimal",
             "--component", "clippy", "--component", "rustfmt", "--no-self-update"], env=env)
    pnpm = tool / "pnpm/node_modules/.bin/pnpm"
    if not pnpm.exists():
        run(["npm", "install", "--prefix", tool / "pnpm", "--ignore-scripts", "--no-audit",
             "--no-fund", f"pnpm@{SPEC['pnpm']}"], env=env)
    run(["node", tool / "pnpm/node_modules/pnpm/install.js"], env=env)
    check_tools(env)


def check_tools(env):
    for command, wanted in ((["node", "--version"], "v" + SPEC["node"]["version"]),
                            (["pnpm", "--version"], SPEC["pnpm"]),
                            (["rustc", "--version"], "rustc " + SPEC["rust"] + " "),
                            (["go", "version"], "go version go" + SPEC["go"]["version"] + " ")):
        actual = run(command, env=env, capture=True).strip()
        if not (actual == wanted or actual.startswith(wanted)):
            raise RuntimeError(f"Wrong toolchain: {actual}; expected {wanted}")


def source(state, name, spec):
    path = state / "sources" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", spec["tag"], spec["repository"], path])
    actual = run(["git", "rev-parse", "HEAD"], cwd=path, capture=True).strip()
    if actual != spec["commit"]:
        raise RuntimeError(f"Unexpected {name} commit: {actual}")
    patch = ROOT / "local-build" / spec["patch"]
    if digest(patch) != spec["patchSha256"]:
        raise RuntimeError(f"Changed patch: {patch}")
    existing = run(["git", "status", "--porcelain"], cwd=path, capture=True)
    if not existing:
        run(["git", "apply", "--check", patch], cwd=path)
        run(["git", "apply", patch], cwd=path)
    with tempfile.TemporaryDirectory(prefix="source-index-", dir=state) as temporary:
        trees = []
        for name_index in ("expected", "actual"):
            index_env = os.environ | {"GIT_INDEX_FILE": str(Path(temporary) / name_index)}
            run(["git", "read-tree", "HEAD"], cwd=path, env=index_env)
            if name_index == "expected":
                run(["git", "apply", "--cached", patch], cwd=path, env=index_env)
            else:
                run(["git", "add", "-A", "."], cwd=path, env=index_env)
            trees.append(run(["git", "write-tree"], cwd=path, env=index_env, capture=True).strip())
        if trees[0] != trees[1]:
            raise RuntimeError(f"Unrecorded tracked/untracked files in {name}; review its source first")
    return path


def sign(path, env, identifier=None):
    args = ["codesign", "--force", "--sign", "-", "--timestamp=none", "--options", "runtime"]
    if identifier:
        args += ["--identifier", identifier]
    run(args + [path], env=env)
    run(["codesign", "--verify", "--strict", path], env=env)


def executable_payload(path, state):
    # Tauri re-signs sidecars with its entitlements and identifier. Compare the
    # executable bytes independently of that signature, using disposable copies.
    with tempfile.TemporaryDirectory(prefix="payload-", dir=state) as temporary:
        copy = Path(temporary) / "executable"
        shutil.copy2(path, copy)
        run(["codesign", "--remove-signature", copy])
        return digest(copy)


def prepared_files():
    expected = {"src-tauri/resources/" + name for name in SERVICE["binaries"]}
    expected.update("src-tauri/resources/" + entry["file"] for entry in SPEC["geodata"])
    expected.update("src-tauri/resources/" + name for name in ("set_dns.sh", "unset_dns.sh"))
    expected.update(f"src-tauri/sidecar/{name}-{TARGET}" for name in ("verge-mihomo", "verge-mihomo-alpha"))
    actual = {}
    for directory in (ROOT / "src-tauri/resources", ROOT / "src-tauri/sidecar"):
        if directory.is_symlink():
            raise RuntimeError(f"Symlinked component directory: {directory}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"Symlinked bundled resource: {path}")
            if path.is_file():
                actual[str(path.relative_to(ROOT))] = digest(path)
    if set(actual) != expected:
        raise RuntimeError(f"Unexpected/missing bundled inputs: {sorted(set(actual) ^ expected)}")
    return actual


def prepare(state, env):
    check_tools(env)
    service = source(state, "service", SERVICE)
    if digest(service / "Cargo.lock") != SERVICE["cargoLockSha256"]:
        raise RuntimeError("Service dependency lock has changed")
    service_env = env | {"CARGO_HOME": str(state / "cache/cargo-service"),
                         "CARGO_TARGET_DIR": str(service / "target")}
    run(["cargo", "build", "--locked", "--release", "--features", "standalone,client",
         *[arg for name in SERVICE["binaries"] for arg in ("--bin", name)]], cwd=service, env=service_env)
    resources = ROOT / "src-tauri/resources"
    resources.mkdir(parents=True, exist_ok=True)
    for name in SERVICE["binaries"]:
        destination = resources / name
        shutil.copy2(service / "target/release" / name, destination)
        sign(destination, env, SERVICE["signingIdentifier"])
    mihomo_spec = json.loads((ROOT / "local-build/mihomo-source.json").read_text())
    core = source(state, "mihomo", mihomo_spec)
    for name, key in (("go.mod", "goModSha256"), ("go.sum", "goSumSha256")):
        if digest(core / name) != mihomo_spec[key]:
            raise RuntimeError(f"Mihomo {name} changed")
    epoch = run(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=core, capture=True).strip()
    ldflags = (f"-X github.com/metacubex/mihomo/constant.Version={mihomo_spec['tag']}-local.1 "
               f"-X github.com/metacubex/mihomo/constant.BuildTime={epoch} -w -s -buildid=")
    binary = core / "bin/mihomo-darwin-arm64"
    run(["go", "build", "-mod=readonly", "-tags", "with_gvisor", "-trimpath",
         "-ldflags", ldflags, "-o", binary, "."], cwd=core, env=env)
    run(["go", "mod", "verify"], cwd=core, env=env)
    sidecars = ROOT / "src-tauri/sidecar"
    sidecars.mkdir(parents=True, exist_ok=True)
    # Both upstream slots contain the same reviewed stable core; no Alpha download.
    for name in ("verge-mihomo", "verge-mihomo-alpha"):
        destination = sidecars / f"{name}-{TARGET}"
        shutil.copy2(binary, destination)
        sign(destination, env, "local.clash-verge.mihomo")
    for spec in SPEC["geodata"]:
        cache = state / "cache/geodata" / spec["file"]
        fetch(f"https://api.github.com/repos/MetaCubeX/meta-rules-dat/releases/assets/{spec['assetId']}",
              cache, spec["sha256"], ("Accept: application/octet-stream",))
        shutil.copy2(cache, resources / spec["file"])
    for name in ("set_dns.sh", "unset_dns.sh"):
        shutil.copy2(ROOT / "scripts" / name, resources / name)
    (state / "prepared-inputs.json").write_text(json.dumps({
        "inputs": prepared_files(), "manifest": digest(ROOT / "local-build/manifest.json"),
        "service": digest(ROOT / "local-build/service-source.json"),
        "mihomo": digest(ROOT / "local-build/mihomo-source.json"),
    }, indent=2) + "\n")
    run(["pnpm", "install", "--frozen-lockfile", "--store-dir", state / "cache/pnpm"], env=env)


def package(state, env, allow_dirty=False):
    check_tools(env)
    start_revision = run(["git", "rev-parse", "HEAD"], capture=True).strip()
    status = run(["git", "status", "--porcelain"], capture=True).strip()
    if status and not allow_dirty:
        raise RuntimeError("Release requires a clean committed tree; --allow-dirty is for development only")
    prepared = json.loads((state / "prepared-inputs.json").read_text())
    if prepared_files() != prepared["inputs"]:
        raise RuntimeError("Prepared component set or bytes changed; rerun prepare")
    for name, key in (("manifest", "manifest"), ("service-source", "service"), ("mihomo-source", "mihomo")):
        if digest(ROOT / "local-build" / (name + ".json")) != prepared[key]:
            raise RuntimeError("Source manifest changed; rerun prepare")
    for relative, expected in prepared["inputs"].items():
        if digest(ROOT / relative) != expected:
            raise RuntimeError(f"Prepared component changed: {relative}; rerun prepare")
    run(["pnpm", "exec", "tauri", "build", "--config", "src-tauri/tauri.local.conf.json",
         "--features", "local-build", "--bundles", "app", "--", "--locked"], env=env)
    app = state / "target/app/release/bundle/macos/Clash Verge.app"
    run(["codesign", "--verify", "--deep", "--strict", app], env=env)
    executables = {}
    for path in sorted(app.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe"):
            continue
        run(["codesign", "--verify", "--strict", path], env=env)
        details = subprocess.run(["codesign", "-dv", str(path)], text=True, capture_output=True, check=True)
        if "Signature=adhoc" not in details.stderr or "Authority=" in details.stderr:
            raise RuntimeError(f"Non-local signature in bundle: {path}")
        executables[str(path.relative_to(app))] = digest(path)
    if len(executables) != 6:
        raise RuntimeError("Bundle is missing expected local executables")
    for name in SERVICE["binaries"]:
        bundled = app / "Contents/Resources/resources" / name
        if digest(bundled) != prepared["inputs"]["src-tauri/resources/" + name]:
            raise RuntimeError(f"Bundled service bytes differ from prepared source build: {name}")
    for name in ("verge-mihomo", "verge-mihomo-alpha"):
        bundled = app / "Contents/MacOS" / name
        original = ROOT / "src-tauri/sidecar" / f"{name}-{TARGET}"
        if executable_payload(bundled, state) != executable_payload(original, state):
            raise RuntimeError(f"Bundled core payload differs from prepared source build: {name}")
    end_revision = run(["git", "rev-parse", "HEAD"], capture=True).strip()
    end_status = run(["git", "status", "--porcelain"], capture=True).strip()
    if end_revision != start_revision or (not allow_dirty and end_status):
        raise RuntimeError("Source changed during compilation; refusing to publish this artifact")
    if prepared_files() != prepared["inputs"]:
        raise RuntimeError("Prepared inputs changed during compilation; rerun prepare")
    revision = run(["git", "rev-parse", "--short=12", "HEAD"], capture=True).strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = state / "artifacts" / f"{SPEC['version']}-{revision}-{stamp}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    archive = artifact_dir / f"Clash-Verge-{SPEC['version']}-macOS-arm64.zip"
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive])
    provenance = {
        "version": SPEC["version"], "target": TARGET,
        "guiCommit": run(["git", "rev-parse", "HEAD"], capture=True).strip(),
        "developmentOnly": bool(status),
        "preparedInputs": prepared,
        "sourceManifest": SPEC, "service": SERVICE,
        "mihomo": json.loads((ROOT / "local-build/mihomo-source.json").read_text()),
        "cargoLockSha256": digest(ROOT / "Cargo.lock"),
        "pnpmLockSha256": digest(ROOT / "pnpm-lock.yaml"),
        "executables": executables, "archiveSha256": digest(archive),
        "signature": "ad-hoc; no Apple-authenticated developer identity",
        "installation": "NOT installed; gateway/TUN acceptance remains pending",
    }
    (artifact_dir / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (artifact_dir / "SHA256SUMS").write_text(f"{digest(archive)}  {archive.name}\n")
    print(f"Build complete: {archive}\nSTOP: no app or service has been installed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "prepare", "package", "release", "ui", "check-tools"))
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local-build")
    parser.add_argument("--allow-dirty", action="store_true", help="Mark an uncommitted package as development-only")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("This reviewed build targets Apple Silicon macOS only")
    state = args.state_dir.expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    env = environment(state)
    if args.action == "bootstrap":
        bootstrap(state, env)
    elif args.action == "check-tools":
        check_tools(env)
    elif args.action == "ui":
        check_tools(env)
        run(["pnpm", "web:dev", "--host", "127.0.0.1"], env=env)
    else:
        if args.action in ("prepare", "release"):
            prepare(state, env)
        if args.action in ("package", "release"):
            package(state, env, args.allow_dirty)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
