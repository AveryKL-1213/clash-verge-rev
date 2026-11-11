# Local security release scope

This fork targets an Apple Silicon macOS LAN gateway. The source baseline is Clash Verge Rev 2.5.10, service 2.7.7 and Mihomo 1.19.34. Exact commits, patches and build tools are pinned in `local-build/`. All six application executables are built locally and ad-hoc signed. The existing production application and service identifiers are retained for replacement installation.

The changes preserve LAN proxy access, DNS and TUN configuration. They restrict the management API to loopback, replace invalid/default controller secrets, reject downloaded application/core updates, remove secrets from dashboard URLs, require verified HTTPS for WebDAV, and remove Mihomo environment overrides and shell lifecycle hooks. Core UI downloads are disabled; subscription and GeoData updates remain available.

## Known vulnerability checks on 2026-11-12

| Scope | Result after fixes | Qualification |
| --- | --- | --- |
| Frontend production dependencies | 5 advisory records reduced to 0 | DOMPurify 3.4.17 is used by the emitted Monaco bundle; an override alone would have left Monaco's embedded older copy active |
| GUI Rust lockfile | 10 vulnerability records reduced to 0 | Seven maintenance warnings and one Linux/glib unsoundness warning remain; the latter is absent from the macOS normal dependency tree |
| Service Rust lockfile | 0 vulnerability records, 0 warnings | Locked production service features only; no test authentication bypass |
| Mihomo main call graph, darwin/arm64, with_gvisor | 11 affected call-graph advisories reduced to 0 | Four module-level records remain for packages not imported by this target; this is a static analysis result |

RustSec database revision: `fd6f39814c4d3c359fdbe223d0280dec4933f78f` (2026-11-11). Go database last-modified timestamp: `2026-11-11T02:51:11Z`, scanner `govulncheck v1.9.0`. Stripped Mach-O binary analysis can conservatively expand module findings into possible symbols; those findings do not establish linkage or reachability. The source analysis was corroborated by a symbol-preserving diagnostic build.

The Go module-level records are [GO-2026-6237](https://pkg.go.dev/vuln/GO-2026-6237), [GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932), [GO-2026-6860](https://pkg.go.dev/vuln/GO-2026-6860) and [GO-2026-6866](https://pkg.go.dev/vuln/GO-2026-6866). Their affected DHCP client, OpenPGP and HTML packages are not imported by this target. Forked dependency module paths may not inherit upstream database entries.

The frontend fix addresses the [DOMPurify advisory set](https://github.com/cure53/DOMPurify/security/advisories), including GHSA-cg2g-xh64-v4q7 and GHSA-mrv2-3pqr-j5jr. Rust patch updates cover `ctutils`, `h2`, `rustls-webpki`, `webpki-roots`, `webpki-root-certs`, `quinn-proto` and `rustix`. Go updates and individual advisory identifiers are recorded in `local-build/mihomo-source.json`.

## Verification and remaining acceptance

Frontend type checking, 44 unit tests and production web compilation passed. Service tests cover peer identity, approved executable paths and private IPC permissions. GUI tests cover controller restrictions, secret handling, blocked updates and WebDAV transport policy. Core tests cover blocked updates, startup flags, environment overrides and path restrictions. Old/new core MRS domain and IP-CIDR output was cross-read successfully.

The build tool verifies exact component source trees, rejects additional/unrecorded bundled files, compares packaged service bytes and core executable payloads against source-built inputs, verifies all signatures, and records hashes in the release provenance. It refuses to publish a normal release from a dirty or concurrently changed working tree. It never starts or installs the application, service or TUN.

These checks do not establish absence of unknown vulnerabilities. The WebView CSP remains upstream's configuration. Some Rust dependencies are no longer maintained. Raising Mihomo's Go module floor to 1.26 changes standard-library compatibility defaults. Real node connections, TUN routing, LAN DNS/UDP, sleep/restart recovery and actual rollback must still be accepted in the maintenance window. No PF kill switch is added, and there is no claim that loss of the proxy prevents direct traffic.

Use the [maintenance and rollback guide](gateway-maintenance.zh-CN.md) before replacement. The backup tool keeps real subscriptions, configuration and network snapshots outside Git; none belongs in a public release or audit report.
