# Runtime image advisories, November 23, 2026

Both release architectures have **211 Debian package/advisory occurrences across 97 distinct advisories** after this change (86 CVE identifiers and 11 Debian temporary identifiers). Debian's tracker listed all 97 as open for trixie on November 23. Trivy reported zero fixed versions for those occurrences, zero critical findings and zero Python package findings. The remaining advisories are tracked in [issue #22](https://github.com/serpro69/pal-mcp-server/issues/22).

## What the rebuild fixed

The two November amd64 builds used the same base digest, repository revision and scanner database. Their only Dockerfile difference was the purge of Debian's `mount` package. The October row is the historical baseline scanned with Trivy 0.70.0 and its October database. [October scan record](../feat/done/server-reliability-and-capabilities/container-scan-task42.json)

| Runtime image | Critical | High | Medium | Low | Unknown | Debian occurrences |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| October 12, amd64 | 0 | 44 | 58 | 61 | 1 | 164 |
| November 23, rebuilt `main`, amd64 | 0 | 50 | 74 | 79 | 14 | 217 |
| November 23, with `mount` removed, amd64 | 0 | 46 | 73 | 78 | 14 | 211 |
| November 23, with `mount` removed, arm64 | 0 | 46 | 73 | 78 | 14 | 211 |

The Dockerfile's existing `apt-get upgrade` installed `libc6` and `libc-bin` at `2.41-12+deb13u5` and `zlib1g` at `1:1.3.dfsg+really1.3.1-1+deb13u1`. Those versions resolve 14 glibc CVEs and two zlib CVEs, accounting for 30 October occurrences. The November scan of rebuilt `main` also reported 83 package/advisory pairs absent in October. Those two changes account for the increase from 164 to 217.

Removing `mount` from the runtime eliminated six occurrences on each architecture, including four high. Other binaries built from the `util-linux` source still carry all six advisories. The final package names, installed versions, advisory identifiers, severities, statuses and fixed-version fields matched across architectures.

## Package decisions and follow-up

- **Mount utilities.** On the rebuilt amd64 runtime, `apt-cache rdepends --installed mount` listed no reverse dependencies, and `apt-get -s purge mount` selected exactly one package. The runtime uses `dpkg --purge mount` so a future hard dependency will fail the build instead of cascading into other removals. Docker supplies PAL's mounts before startup.
- **Base utilities and PAM.** Debian marks `util-linux`, `perl-base` and `sysvinit-utils` essential and `login` protected. The package solver rejected removal of `libpam-modules` because `util-linux` pre-depends on it and `login` depends on it. The installed `util-linux` also depends on `liblastlog2-2`. These packages remain installed.
- **Package and account management.** The image retains APT so derived images can install system dependencies. A simulated purge of `passwd` also selected `adduser` for removal. Account management remains available for derived images.
- **Remaining advisories.** The maintainer of #22 should rebuild and rescan both platforms when Debian publishes a fixed trixie version or the pinned base changes. Compare the installed version and package/advisory pairs before marking an advisory resolved. The existing package-by-package applicability assessment remains available. [Advisory register](runtime-image-2026-11-23.csv) · [Debian tracker](https://security-tracker.debian.org/tracker/) · [Historical applicability assessment](../feat/done/server-reliability-and-capabilities/validation.md#debian-follow-up)

## Evidence and reproduction

The [CSV register](runtime-image-2026-11-23.csv) contains all 113 source-package/advisory pairs reported in October or November. It records the trixie tracker disposition, October count, rebuilt-main count and final count per architecture. The `candidate_binary_packages` column lists every affected installed binary and its version. Severity comes from the latest scan containing the advisory. The [Debian tracker dataset](https://security-tracker.debian.org/tracker/data/json) used for this comparison had SHA-256 `bd63f49d273a1a9dc78f8f842692236fa4dc7b48ca4f588ae571d0a63864188f`.

The builds used repository base `5adcc301da840e69a7472e17c239b9a90f7a435f` and Python `3.11.18-slim-trixie` at index digest `sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535`. Trivy 0.76.2 used its database updated at 07:04:04 UTC on November 23. All scans included every severity, used an empty ignore file and retained unfixed entries.

| Image | Docker image ID (SHA-256) | Raw Trivy JSON SHA-256 |
| --- | --- | --- |
| Rebuilt main, amd64 | `1fbcb28d5a6a0a21ff03a8bd5b66c5f74a983b999348e9ee5fbac99e6a5ad6eb` | `b6975663da1441ecc4afc027925cff4d8805fdf9074dfce7a725f453ee577267` |
| Candidate, amd64 | `47a0259c7c471e740e918cd75c8a6816cea4a87c48f9763fbda4c128c859e6cc` | `e3d4d4a69d0663193b28d3f7c88a8fae2697c9de448bc39806e6ed45e4d33233` |
| Candidate, arm64 | `ea44cec4c6f2d7abaf51ff2f2a62a1f1bdb658202415075e345430014368f6ea` | `cfacf97543ff5b6064de35622093c366748e525f2c127e6cf335efebcf2ddafb` |

Run from the relevant checkout with Docker and Trivy installed. Download the database once so both platforms use the same snapshot.

```sh
trivy image --download-db-only
for arch in amd64 arm64; do
  docker build --pull --no-cache --platform "linux/$arch" -t "pal-audit:$arch" .
  trivy image --image-src docker --platform "linux/$arch" --scanners vuln \
    --skip-db-update --ignorefile /dev/null --list-all-pkgs=true --format json \
    --output "/tmp/pal-audit-$arch.json" "pal-audit:$arch"
done
```

Both candidate images started their default stdio server as UID/GID 999/999 with a read-only root filesystem, all capabilities dropped, no network, a temporary filesystem at `/tmp` and a fresh state volume. Each completed MCP initialization and listed 19 tools, passed the image health check with a synthetic key and passed `dpkg --audit`. The probes also verified the absence of `mount` and `umount`, the presence of `pgrep`, certificate loading and a zlib round trip. The amd64 runs used Rosetta in an arm64 Linux VM. These checks cover local runtime behavior.
