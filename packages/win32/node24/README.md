# Windows Chocolatey ownership handoff (prepared, not distributed)

Native run `40437980932` passed the prior `18d6aef` source on Windows 2025,
PowerShell 5.1 and Chocolatey 2.7.4. That pass included fixture removal of an
orphan legacy shim; it did not prove product-side shim cleanup. The following
product-hook correction is source-only until a new admitted native run passes.

Run `40445133666` at `0e9dc09` subsequently passed 26 preflight controls, four
modeled finalization-state controls and the original nine lifecycle phases, but
failed at the first docs-family modern package upgrade. Cleanup passed separately.
No actual product shim handoff or post-activation package compensation passed in
that run. Its underlying Chocolatey error was not retained, so the cause remains
Unknown. The qualifier now maps captured output only to a closed set of fixed
shim/installer labels; unrecognized errors become `UNKNOWN`. It never writes raw
output, paths, credentials or excerpts. Six redaction controls include secret-like
unknown errors and recognized errors mixed with secret-like text. New diagnostic
execution remains unrun until an admitted native job proves it.

Do not assume repeatable shim SHA-256 from a fixed Chocolatey version. The
[official ShimGen repository](https://github.com/chocolatey/shimgen) says its
generator is closed source; its published shim code is reference source, not the
generator/build pipeline. Chocolatey's versioned generation caller supplies path,
output, icon and optional GUI inputs, but provides no byte-reproducibility contract.
The two observed hashes remain narrowly accepted evidence, not proof of all
future generated shims. Hash drift is only an untested explanation for this run;
do not add hashes or weaken ownership without actual discriminating evidence.

The product hooks now preflight the exact `bin\vmpower-agent.amd64.exe` path and
remove it inside the recoverable activation transaction, or before service removal
on uninstall. Existing shims must match
one of the two exact SHA-256/size pairs observed from the pinned legacy packages
in that run, under `C:\ProgramData\chocolatey`, with protected ownership/ACLs,
no reparse points and a single link. Unknown bytes, custom roots, unsafe parents
or an intervening replacement fail closed and preserve the file. Missing shims
are harmless. No shim or legacy executable is executed to identify its target.
Older Chocolatey-generated shim variants are not qualified by these two hashes.

Both old package scripts register the service against the copied
`C:\ProgramData\vmpower\vmpower.exe`, not Chocolatey's automatic global shim.
The inspected dashboard install command and published help pages use Chocolatey
installation/service reporting, not that shim. Its removal therefore cleans up
an obsolete automatic command; the old copied executable is retained for rollback
and the modern release keeps its existing `vmpower.cmd`. Uninspected external
scripts that invoke the old global EXE are not claimed compatible.

The native package test requires the legacy shim before upgrade, its absence
immediately after the product upgrade and after ordinary product uninstall, all
before emergency fixture cleanup. The existing preflight also runs product-hook
wiring controls with synthetic ownership/service boundaries, plus real helper
refusal controls. Those local wiring controls do not replace actual shim proof.
Post-success rollback to legacy is still unqualified: in-memory installer failure
recovery is not a persistent operator rollback command, and normal old-package
downgrade would overwrite configuration.

The first product correction (`0416667`) incorrectly removed the shim after the
service transaction returned. A changed/locked/denied shim could then fail the
package operation while leaving the service on the new runtime. The successor
places both Chocolatey ignore-marker restoration and shim removal inside the
installer's activation catch boundary. Compensation restores wrapper, application,
arguments, directory, NSSM startup mode and prior running/stopped state; failures
remain failures. Uninstall reconciles the shim before touching the service.
Controls exercise changed, locked, denied and marker failures through the actual
installer with modeled SCM; a synthetic native package additionally requires
real Chocolatey registration and actual service/child/config restoration while
preserving the unrelated replacement. This is source preparation, not native proof.

Use the existing `vmpower-agent` package ID with an explicit version greater than
`0.0.1`. A normal Chocolatey upgrade invokes the **new** install script after the
**old** before-modify script. It does not run the old uninstall script. Both
observed published packages lack a before-modify script; both old uninstall
scripts delete `C:\ProgramData\vmpower\vmpower.conf` and the old executable.
An uninstall-first migration would therefore destroy configuration unnecessarily.

Primary references, checked 2026-10-26:

- [Package script lifecycle](https://docs.chocolatey.org/docs/create-packages/)
- [Install script ordering](https://docs.chocolatey.org/en-us/chocolatey-install-ps1/)
- [Upgrade command](https://docs.chocolatey.org/en-us/choco/commands/upgrade/)
- [Rollback limitations](https://docs.chocolatey.org/en-us/why/)
- [Registration without scripts](https://docs.chocolatey.org/en-us/guides/usage/install-upgrade-package-without-scripts/)
- [Uninstall and cleanup flags](https://docs.chocolatey.org/en-us/choco/commands/uninstall/)

The two cached, statically inspected published inputs are:

| Input | SHA-256 |
| --- | --- |
| `docs-windows.nupkg` | `3b34023ffa2d05b54bdf10abfe9b3c515ab5dccf70af823407dcd1cb976a8956` |
| `dashboard-windows.nupkg` | `1e72a1db993a3b013b8503e8cde2f0492a8bcee18539ee9e1056c2eb203893af` |

They share the ID, version, scripts and unbounded `nssm` dependency but contain
different executable bytes. Their scripts were inspected, never executed.

## Package inputs

`scripts/build-windows-package.py` prepares inputs for Chocolatey's own `pack`
command. It does not download, install, execute or publish anything. Supply an
already qualified `win-x64` bundle, its admitted manifest SHA-256, the exact Node
archive and NSSM archive named by existing distribution pins, an explicit package
version, and a new output directory:

```text
python scripts/build-windows-package.py --bundle BUNDLE --expected-manifest-sha256 SHA256 --node-archive NODE_ZIP --nssm-archive NSSM_ZIP --version VERSION --output NEW_DIRECTORY
```

The builder verifies the sealed bundle, current source lock, pinned archives and
actual bundled Node bytes. It embeds the existing installer unchanged, pinned
NSSM, vendor license text and package scripts. There is no package dependency
fetch, new runtime format, signing scheme or service launcher. Chocolatey shim
markers stay outside the installed sealed runtime. The install script temporarily
removes only its package marker while the original bundle validator runs.

The package is for upgrades with existing configuration. It accepts no secret
package parameters. Fresh enrollment still uses the existing bundle installer's
stdin entrypoint. The installer preserves configuration, service startup mode and
running/stopped state, retains content-addressed releases, and restores its prior
SCM snapshot when a new child fails. Its existing custom-identity/non-NSSM and
non-C-drive refusals remain in force. The new uninstall script removes the owned
service and retains configuration, releases, wrapper and old executable.

Chocolatey's own rollback restores package files only; it does not restore files
outside the package folder or rerun an installer. Both package ownership rollback
and installer SCM rollback must pass the native fixture. Do not use a downgrade
to the old package as a rollback procedure: its old install script overwrites
configuration. The installer snapshot is an immediate failure recovery path,
not a general post-success rollback command.

## Held native qualification

The existing Windows qualifier accepts optional `-LegacyPackages DIRECTORY
-ChocolateyVersion EXACT_VERSION`. The integrated native workflow enables this
extension with Chocolatey **2.7.4**, the version listed by the official
[Windows 2025 image manifest](https://github.com/actions/runner-images/blob/main/images/windows/Windows2025-Readme.md)
for image `20261021.299.1` when read on 2026-10-26. Both workflow preflight and the
extension reject version drift; neither installs nor updates Chocolatey. The
workflow parses the candidate PowerShell before service work and stages both
exact legacy archives through the existing no-redirect HTTPS/hash downloader.
Root must still admit the exact source and bounded native execution.

Use the existing disposable, elevated GitHub-hosted Windows 2025 fixture, empty
VMPower destinations, owned-resource journal, loopback receiver and executable
firewall rules. Stage only the two fixed-hash cached packages and already pinned
modern inputs. The job must have no customer/provider/package secrets and no
persisted checkout credential; any job token is read-only. Do not run on a
customer host, self-hosted runner or machine with existing agent registration.

For each exact old package, the fixture registers the real bytes using
`--skip-automation-scripts --ignore-dependencies` and a local-only source. This is
**registration-only evidence**, not proof that the old software was installed or
works. The old executable and both old PowerShell scripts are never invoked.
The SCM fixture uses only the qualified Node runtime with synthetic configuration
and a loopback receiver. It then performs normal Chocolatey upgrades and the new
uninstall, without script-skipping, to test:

- replacement of the actual `0.0.1` package registration;
- running/manual and stopped/disabled service preservation and configuration ACLs;
- a crashing modern child restoring prior package registration and service progress;
- new uninstall preserving configuration and retained runtime;
- removal of owned package registration, exact caches, legacy shim, service,
  processes, firewall rules and fixture inputs.

Versions `0.0.2`, crashing `0.0.3`, and finalization-failure `0.0.4` in this fixture are test inputs and must never
be published by qualification. The successful `0.0.2` archive contains the
unmodified product `bundle`, not the synthetic comment-bearing `upgrade` bundle;
the default lifecycle fixture still checks that separate synthetic upgrade.
A final version and retained deliverable artifact require separate root admission.
The `0.0.4` fixture uses the explicitly synthetic upgrade bundle and a labeled
test finalizer. It must fail after activation, preserve the replacement sentinel,
and restore `0.0.2` package/service state before ordinary uninstall is tested.
Cleanup skips all old automation and the auto-uninstaller, including
when upgrade failed while `0.0.1` remains registered. Exact package directories and
the legacy shim must be absent before ownership is recorded. A cleanup failure
fails qualification and retains evidence; the disposable host must be discarded.
Package operations preserve completed fixed phase markers and a primary failure
classification independently of cleanup, so a cleanup exception cannot hide the
operation that failed first. Raw native output and exception messages never enter
those receipts. After actual package deregistration, cleanup reconciles only the
exact journal-owned `bin\vmpower-agent.amd64.exe` orphan: explicit absent-before
ownership, matching package-manager root, protected parents, no reparse point or
directory, and a single-link regular file are required. It records the removed
file's hash/size and asserts absence; it never glob-deletes the shared bin folder.
Filesystem controls and the actual qualifier catch/finally regression controls
run before native service work. Their source is preparation until admitted CI
has executed them; a prior cleanup failure is not proof of package success.
No future test may execute unsupported old telemetry without a separately reviewed
quarantine design. This fixture intentionally avoids that need.

Local Python tests validate pack inputs using generated non-executable byte
fixtures. The native extension additionally runs `verify-windows-package.py`
against each actual `choco pack` archive before registration. It checks all
payload bytes, same-ID/version metadata, no dependencies or extra payload, and
the actual Node/NSSM ignore-marker mapping, then records each nupkg SHA-256.
Offline archive controls use generated ZIP fixtures, not Chocolatey itself.
Each Chocolatey transaction has a 180-second watchdog; expiry kills its process
tree and fails qualification. A failing-child control must observe the existing
installer's fixed recovery classification as well as restored package/service
state, so a package lock, fetch error or timeout cannot count as rollback proof.
The outer Windows job remains bounded at 20 minutes; no automatic retry is added.
These checks do not prove NuGet packing, PowerShell/SCM behavior, old runtime
compatibility, customer OS support, customer usage, signer trust or distribution
rights. Actual old runtime and older Windows compatibility remain **unqualified**.
Native packing, install/upgrade/rollback/uninstall and complete cleanup are release
gates; this source preparation grants no native execution or distribution authority.
