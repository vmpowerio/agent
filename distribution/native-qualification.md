# Native qualification contract

Root review and CI/budget admission precede a push. The prepared workflow
`.github/workflows/native-qualification.yml` has one standard Ubuntu 24.04 x64 job
(15 minutes) and one standard Windows 2025 x64 job (20 minutes). It runs on this
delivery branch/master pushes and master PRs, with read-only contents permission,
no customer/provider secrets, no dispatch/release/tag event and no binary artifact
publishing. Existing source/CodeQL jobs remain separate and count toward admission.
Only sanitized JSON qualification receipts are uploaded for three days; package
bytes, synthetic configuration, login rows and raw service environments are excluded.
Download only the pinned public Node and NSSM inputs and public npm packages before
enforcing outbound isolation. Record exact runner OS, kernel/libc or Windows build
and PowerShell version. These results do not establish Windows 10/Server 2016,
oldest Linux baseline or customer-host compatibility; untested platforms stay Unknown.

1. Check out the exact reviewed candidate. The existing source workflow retains
   full Flow/build/lint/source tests. Each native job performs fresh
   integrity-checked `npm ci`, compiles and stages that lock's production
   closure. Verify the supplied Node archive hash and per-file bundle manifest.
   On Windows verify both NSSM archive and extracted x64 hashes; retain bundled
   source-file and licence hashes in sanitized evidence. Never execute a mirror or
   a version chosen dynamically by Chocolatey.
2. Run packaging negative tests and the matching native-bundle smoke with the
   staged runtime. Run Linux filesystem transaction tests and Windows real native
   argv/ACL tests. Negative controls must prove changed bytes, missing credentials,
   links, writable executable ACLs and injected start failure are rejected.
3. Before any real service installation, establish service-scoped OS outbound
   denial. Linux rules reject nonloopback traffic for the dedicated service UID;
   a direct Node socket probe runs as that UID and must increment the IPv4 rejection
   counter. IPv6 rejection rules are installed/read back, but IPv6 Internet
   reachability is not claimed. Windows rules block nonloopback ranges for every
   exact staged/installed Node path, across all profiles. Profiles must already
   be enabled; no global profile or runner control-channel settings change. A
   direct probe must connect to owned loopback and receive access-denied externally;
   a timeout alone fails Windows qualification. The only external probe is bare
   TCP to 1.1.1.1:443, carrying no payload or credentials, that must be denied.
   This does not claim the whole runner is offline. Abort before service creation
   if isolation cannot be established.
4. Assert the fixed VMPower service names, install paths, account and configuration
   do not already exist. Use the fixed production names only on this disposable
   host, so the tested path matches the installer. No customer resources exist on
   it. Register unconditional cleanup before creating any service or test files.
5. Install synthetic JSON credentials from stdin, including quotes/backslashes,
   with a synthetic noncustomer host. Start the actual systemd/NSSM service and
   identify its real staged Node child and command line without printing secrets.
   Confirm the exact config reader succeeds and native collection reaches the
   report caller. Receiver validation checks synthetic headers, body shape,
   platform and version; only counts/timestamps persist. Ephemeral-runner login
   rows are not logged or uploaded. Require two reports across 60 seconds from the
   same expected Node child. Verify account, executable
   permissions, configuration permissions and wrapper identity.
6. Stop and confirm the child PID exits; restart; upgrade to a second fixture
   manifest without supplying credentials and compare configuration byte hashes.
   Keep the second fixture visibly synthetic, never a release candidate. Test
   a stopped/disabled upgrade too. Inject a broken entrypoint that really fails
   startup, assert rollback to previous runtime and running state, and compare
   config hashes. A service-manager status alone may hide a restarting failed child;
   installer startup checks child identity/stability for five seconds, and the
   longer reporting test independently proves progress beyond service-manager state.
7. Uninstall through the new installer; verify service and child absence and retained
   config/release hashes. Separately test migration from synthetic legacy unit/NSSM
   settings using pinned Node24 fixture bytes. This does not execute a legacy
   published Node10 binary or qualify Debian/Chocolatey package-manager ownership;
   those migration checks remain required before replacing public packages.
   Preserve published legacy artifacts.
   A legacy uninstaller may delete config and must not be used as cleanup.
8. In finally/always cleanup, remove only the synthetic resources recorded by this
   run and restore runner network rules. Verify no service, Node/NSSM child, config,
   test account, temporary executable or credential-bearing log remains. Upload
   only sanitized hashes, versions, checks and cleanup results, not configuration
   or raw service environment. Any failed cleanup fails qualification.

No binary signing identity, release version, customer OS inventory, customer
installation or artifact download has been qualified by this plan. A successful
native run is an input to a separately authorized release and consumer readback.

Implementation: `prepare-native-inputs.py` binds public vendor bytes and labels
synthetic upgrade/crash variants; `native-report-fixture.cjs` owns the loopback
receiver; `qualify-native-linux.py` and `qualify-native-windows.ps1` enforce empty
host guards, real service lifecycle and cleanup. Each has a separate cleanup entry
point in an `always()` workflow step. Failure before ownership cannot authorize
cleanup of a pre-existing service/configuration. Native acceptance comes from
exact-source hosted job/phase/cleanup receipts, never from this development Mac
or workflow existence. No local server or service is started.

Staging prevention: manifest names must use POSIX separators on every host.
Mac/Linux staging cannot qualify Windows path handling; the Windows job stages
and verifies the real Windows bundle before any installer/service operation.

Native preparation prevention: runner-owned temporary/repository ancestors are
not a service-user access contract. The Linux harness copies only the public
bundle and probe into the empty, root-owned `/opt/vmpower-agent-qualification`
fixture, seals and hash-verifies it before making it readable, and includes it in
the ownership journal and cleanup. It never changes runner ancestor permissions;
configuration and receiver state remain separate. Probe failures emit only fixed
classifications and exit codes, not raw stderr or environment. The first hosted
probe failure omitted those diagnostics, so its underlying cause remains Unknown.
An external-connect success, timeout without qualifying denial, or inaccessible
fixture remains a failure rather than an isolation pass.

Pinned vendor downloads allow at most three attempts to the same URL for429/5xx
or transient network failures, with two/four-second backoff. Redirects, TLS
verification failures, other HTTP errors and hash mismatches are hard stops;
there is no mirror or unpinned fallback. The first Windows hosted attempt stopped
on the official NSSM HTTP503 before service tests. Offline tests exercise that
retry path and its rejection controls; only a subsequent admitted native run can
qualify actual provider availability and lifecycle behavior.

Diagnostic prevention: when a guarded native phase fails, record enough fixed
metadata to distinguish the failed prerequisite from later behavior. The Linux
receipt now records only uid/gid/mode and classification for `/`, `/opt`, `/etc`,
`/etc/systemd`, `/etc/systemd/system`, `/usr` and `/usr/bin` before the unchanged
destination guard. It does not repair ownership/permissions or inspect arbitrary
paths. The first runuser failure and the second run's exact offending ancestor
remain Unknown until their own evidence supports a narrower conclusion.
Windows PowerShell5.1 JSON arrays are assigned directly rather than wrapped in
`@(...)`; the real native echo still must preserve all six argument values,
including empty and quoted arguments. Failures report only count/type/index.
The product quoting function is unchanged. Platform checks remain pending;
offline inspection is not evidence of successful native quoting or service use.

Ephemeral Ubuntu `/opt` normalization: the hosted image was observed with the
exact root0:0 directory mode0777, which the production destination guard correctly
rejects. The qualification harness alone may temporarily change that exact
baseline to0755, after recording its original/temporary mode in the atomic owned
journal. It rejects different ownership, symlinks, other modes or ambiguous
journals; the product installer and its destination guard remain unchanged.
No other ancestor is changed. The existing finally/always cleanup restores0777
only after all owned service, child, file, firewall, receiver and input cleanup
flags pass. An incomplete cleanup defers restoration and fails qualification;
the same journal is reconciled by the always step, not discarded or replaced.
Tests use disposable directories and inject phase, journal, permission and
cleanup failures. Native restoration still requires a new exact-head hosted
receipt before acceptance; no development-machine `/opt` change is permitted.

Fixture path prevention: PR21 CodeQL found that the smoke helper accepted a
caller-selected manifest root and the receiver accepted an arbitrary output
filename. Their workflow callers were trusted, but that did not constrain the
helper itself. Both now derive fixed paths from the actual staged Node executable
and require the canonical `vmpower-native/bundle/runtime/node[.exe]` layout,
regular unlinked manifest/app files, and no path arguments. Receiver creation is
exclusive and subsequent writes retain that descriptor, so replacing its path
cannot redirect a later write. Cleanup checks the exact owned executable and
helper command, rather than a substring of the former output argument. Offline
tests invoke the actual helpers with a copied Node executable, adverse paths and
preserved sentinels; their fake loopback receiver opens no socket. These checks
do not replace new exact-head native lifecycle and CodeQL qualification.

Cross-platform test placement: the first path-hardening successor incorrectly
selected its Linux `/proc` cleanup test on Windows, where importing the Linux
harness fails on `pwd` before native staging. This was a test-placement mistake,
not a product or vendor failure. Classify OS-specific imports before adding a
test to a shared entrypoint; verify selection on both platform families. This
test remains selected on Linux/macOS and uses the existing Windows-only POSIX
exclusion. All 14 actual JavaScript helper controls and the real Windows process
identity, cleanup and lifecycle checks remain mandatory on Windows.

Python boundary prevention: compare the actual configured CodeQL language matrix
with changed source languages. The first canonical scan added Python after the
PR had analyzed only JavaScript/TypeScript and Actions; successful analysis jobs
did not mean zero alerts. Inspect the resulting alerts and their actual dataflows.
The Linux installer now checks SHA256 syntax before reading the bundle and
derives release paths from the matching computed digest. New configuration is
created0600, written and closed before assigning the already-verified dedicated
service group and0640. Ordinary bundle-directory selection remains supported;
linked bundle roots are rejected, and copied integrity/sealing checks remain.
The hosted fixture rejects relative, traversing, linked or ambiguously normalized
runner temporary paths before filesystem effects. Offline adverse controls must
reach the old boundary itself, not fail first on a different host/platform guard.
These controls do not establish that every detector finding is exploitable or
resolved; exact-head Python scanning and both native platforms remain required.

Primary behavior references: [systemd service startup semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[Windows program/address firewall rules](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsserver2025-ps),
and [standard GitHub runner labels](https://github.com/actions/runner-images).
