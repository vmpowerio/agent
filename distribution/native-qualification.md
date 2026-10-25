# Native qualification candidate; not dispatched or run locally

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
cleanup of a pre-existing service/configuration. Windows PowerShell parsing, native
ACLs and both service lifecycles remain UNRUN on the development Mac; workflow
existence is not acceptance. No local server or service is started.

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

Primary behavior references: [systemd service startup semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[Windows program/address firewall rules](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsserver2025-ps),
and [standard GitHub runner labels](https://github.com/actions/runner-images).
