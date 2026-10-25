# Node 24 delivery candidate

This additive candidate stages a runtime bundle and provides explicit-apply,
offline installers for qualification. The native workflow invokes them only on
guarded disposable runners. It does not replace legacy installers, publish binary
artifacts, create a release tag, or
change published download URLs. The source-qualification workflow is unchanged;
a separate native workflow is prepared for reviewed, budget-admitted execution.

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
  processes are gone. Use clean disposable hosts and guaranteed cleanup.
- Exercise synthetic successful and failed `last` / `query user` collection
  through the real report caller without contacting a customer endpoint.
- Validate native package contents, architecture, runtime/library requirements,
  artifact SHA-256 and a version that upgrades both legacy package formats.

The Debian package currently labels a native binary `Architecture: all`; its
postinstall does not create the service user or enable the service. The Windows
installer overwrites configuration and its uninstaller deletes it. These legacy
scripts are unqualified and remain unchanged by this scaffold.

Windows uses the pinned NSSM 2.24-101-g897c7ad wrapper. The
[NSSM publisher](https://nssm.cc/download) warns that modern Windows needs build
2.24-101 or newer rather than stable 2.24. Selecting a replacement such as WinSW
adds a service migration and runtime dependency that needs separate qualification.
Do not silently substitute an arbitrary Chocolatey wrapper version.

`nssm-pins.json` binds the exact publisher HTTPS archive, its published SHA-1,
and independently computed archive/executable SHA-256. The archive includes source
and a README public-domain licence. An independent Git archive fetch returned 503;
no reproducible binary/source equivalence or Authenticode validation is claimed.
The publisher lists this development build from 2017; age alone proves neither
safety nor vulnerability. Current support commitments and comprehensive advisory
coverage remain unknown. [CERT VDE advisory VDE-2025-063](https://certvde.com/de/advisories/VDE-2025-063/)
and CVE-2025-41686 concern Phoenix Contact DaUM's writable NSSM installation files,
not a universal finding against this executable. The installer therefore protects
both executable files and their parent directories. Native ACL tests are required;
this is not a zero-vulnerability assertion.

## Additive installer candidate

Both installers require an explicitly supplied, qualified bundle-manifest SHA-256.
They download nothing. First-install configuration arrives as JSON on standard
input only. Existing configuration takes precedence even over invalid replacement
input; missing input never overwrites it. Uninstall has no purge option and retains
configuration, releases and rollback files. No credential is placed in service
arguments. Credentials must come from a protected delivery mechanism, not a literal
shell command or committed fixture.

- `install-linux-bundle.py` preserves service `vmpower.service`, `/usr/bin/vmpower`
  and `/etc/vmpower.conf`. It creates/reuses a non-root `vmpower` service account,
  sets configuration root:vmpower 0640, retains content-addressed releases under
  `/opt/vmpower-agent`, and saves previous launcher/unit bytes in a root-only
  rollback directory. It rejects linked or writable managed destinations. A failed
  start restores the previous launcher/unit and previous active/enabled state.
- `install-windows-bundle.ps1` preserves NSSM service `vmpoweragent`, LocalSystem
  and `C:\ProgramData\vmpower\vmpower.conf`. It puts executables below Program Files
  with Administrators/SYSTEM write access and Users read/execute only. Configuration
  permits Administrators/SYSTEM only. Reparse points and hardlinked configuration
  are refused. It preserves the prior NSSM Application/AppDirectory/AppParameters
  and wrapper path on failure, and retains all release and wrapper files.
  Custom service identities are refused rather than reset. The current source
  reader resolves `\ProgramData` on the working-directory drive: non-C Program
  Files drives therefore fail closed pending explicit compatibility qualification.

Invoke only on a qualified disposable native host, with the respective `--apply`
or `-Apply` switch. No-apply invocation fails without changing the machine. Linux
requires root; Windows requires an elevated x64 PowerShell process. Successful
upgrades preserve stopped/disabled state; a new service starts automatically.
The wrappers are not Debian/Chocolatey replacement packages and no package version
has been selected. Before customer use, qualify legacy package-manager coexistence
so an old uninstaller cannot remove the new service or retained configuration.

Offline checks:

```sh
python3 scripts/test-stage-distribution.py
python3 scripts/test-install-linux-bundle.py
```

The Linux installer tests replace account/service operations and exercise real
temporary files. Windows `test-install-windows-bundle.ps1` takes `-Bundle`,
`-ExpectedManifestSha256`, `-NssmExe` and tests real native argument passing and ACLs
on temporary files with all service operations replaced. It is prepared, not run
on macOS. Neither suite proves service lifecycle.

The bounded hosted qualification plan is in `native-qualification.md`. Do not infer
authenticated caller continuity or installation correctness from denial tests or
mocked service calls: execute each changed positive lifecycle path on its actual
native service manager, with synthetic configuration and enforced outbound denial.
Startup additionally requires the same expected Node child to stay alive for five
seconds. This detects immediate crashes/restarts; it does not prove reporting.
Native lifecycle tests separately require two scheduled reports across 60 seconds
to the owned loopback receiver, from the same child. These hosted tests have not
run merely because their workflow exists.

The [public installation documentation](https://help.vmpower.com/VMPower%20agent%20%5Bbeta%5D/Installation/)
still references legacy Debian/Chocolatey packages and manual archive URLs with
MD5 checksums. Preserve those published bytes for rollback. New delivery needs
versioned HTTPS artifacts, SHA-256 verification, an explicit package-version and
signing decision, and coordinated consumer instructions. No legacy Azure upload
step is invoked by these tools. Native smoke success alone is not authorization
to publish or proof of customer installation.
