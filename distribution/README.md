# Node 24 delivery candidate

This additive scaffold stages a runtime bundle. It does not install a service,
replace existing installers, upload files, create a release tag, or change the
published download URLs. The existing source-qualification workflow is unchanged.

Use the official, unmodified Node 24.22.1 binary with the compiled application
and the production nodes from the committed lock. This avoids a new bundler,
experimental SEA injection, and modification of the vendor's executable signature.
The official archive hashes are pinned in `runtime-pins.json`; the staging tool
checks them before reading archive members. HTTPS checksum provenance is recorded;
this scaffold does not claim GPG or Authenticode validation.

## Reproduce staging

First run the required source checks and a fresh `npm ci --legacy-peer-deps
--ignore-scripts --no-audit --no-fund` against the exact committed lock. The packager
copies only production lock entries, including nested production dependencies.
It checks installed package identities and versions; that check does not replace
npm's tarball-integrity verification. For delivery qualification use a fresh
isolated install, not an untrusted existing node_modules directory.

Download the two exact archives from `https://nodejs.org/dist/v24.22.1/` into a
separate input directory. Run, for each target:

```sh
python3 scripts/test-stage-distribution.py
python3 scripts/stage-distribution.py --repo . --modules ./node_modules \
  --runtime-archive /absolute/input/node-v24.22.1-linux-x64.tar.xz \
  --target linux-x64 --output /absolute/new-output/linux-x64
```

Use `win-x64` and its pinned ZIP for Windows. Existing output directories are
refused. A per-file SHA-256 manifest binds staged contents and includes the lock
hash, runtime archive hash, application version and exact production nodes.
No machine configuration or credentials belong in the bundle. The Windows
launcher is `vmpower.cmd`; it is not a replacement for the legacy standalone EXE.

On the matching native host, run the staged runtime itself:

```sh
/absolute/new-output/linux-x64/runtime/node scripts/native-bundle-smoke.cjs \
  /absolute/new-output/linux-x64
```

The corresponding Windows invocation uses `runtime\node.exe`. The check rejects
the wrong platform or runtime path. It exercises the real packaged CLI version,
help and missing-required-configuration failure, with machine configuration and
Node transports denied. It does not prove service lifecycle or OS-level network
isolation; a qualified hosted job must provide and verify that isolation.

## Compatibility and remaining acceptance

The [official Node 24 platform requirements](https://github.com/nodejs/node/blob/v24.22.1/BUILDING.md)
require supported GNU/Linux x64 with kernel >=4.18, glibc >=2.28 and libstdc++
>=6.0.25, or supported Windows x64 (Windows 10 / Server 2016 or newer). Installed
customer operating systems have not been inventoried. A modern build must not be
published as compatible with older hosts without evidence.

Keep JSON fields `key`, `secret`, `vm_id`, optional `host`, and the existing
`/etc/vmpower.conf` and `C:\ProgramData\vmpower\vmpower.conf` locations. An upgrade
must preserve existing configuration; uninstall must preserve credentials unless
an explicit purge is requested. Credentials must not appear in command lines,
logs, package artifacts or manifests. New configuration should be serialized as
JSON rather than shell or regular-expression replacement.

Before installing this bundle, qualify actual Linux and Windows installers on
disposable hosted runners with synthetic configuration and outbound denial:

- Fresh install, upgrade, rollback and uninstall preserve the configuration
  contract and restrict its file permissions/ACLs.
- Start the actual service, verify it is running, stop it, and prove its child
  processes are gone. Use distinct test service names and guaranteed cleanup.
- Exercise synthetic successful and failed `last` / `query user` collection
  through the real report caller without contacting a customer endpoint.
- Validate native package contents, architecture, runtime/library requirements,
  artifact SHA-256 and a version that upgrades both legacy package formats.

The Debian package currently labels a native binary `Architecture: all`; its
postinstall does not create the service user or enable the service. The Windows
installer overwrites configuration and its uninstaller deletes it. These legacy
scripts are unqualified and remain unchanged by this scaffold.

Windows also needs an explicitly pinned, native-tested service wrapper. The
[NSSM publisher](https://nssm.cc/download) warns that modern Windows needs build
2.24-101 or newer rather than stable 2.24. Selecting a replacement such as WinSW
adds a service migration and runtime dependency that needs separate qualification.
Do not silently substitute an arbitrary Chocolatey wrapper version.

The [public installation documentation](https://help.vmpower.com/VMPower%20agent%20%5Bbeta%5D/Installation/)
still references legacy Debian/Chocolatey packages and manual archive URLs with
MD5 checksums. Preserve those published bytes for rollback. New delivery needs
versioned HTTPS artifacts, SHA-256 verification, an explicit package-version and
signing decision, and coordinated consumer instructions. No legacy Azure upload
step is invoked by these tools. Native smoke success alone is not authorization
to publish or proof of customer installation.
