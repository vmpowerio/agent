# Proposed native qualification; not scheduled or run

Root review and CI/budget admission precede workflow changes or a push. Use one
disposable Linux x64 runner and one disposable Windows x64 runner, with explicit
timeouts, read-only repository permissions, no customer/provider secrets, no
release event, and no artifact publishing. Download only the pinned public Node
and NSSM inputs and public npm packages before enforcing outbound isolation.
Record exact runner OS, kernel/libc or Windows build and PowerShell version.
These runner results do not establish Windows 10/Server 2016, oldest Linux baseline,
or customer-host compatibility; each untested platform stays Unknown.

1. Check out the exact reviewed candidate. Perform the established source gates
   and fresh integrity-checked `npm ci`, compile and stage that lock's production
   closure. Verify the supplied Node archive hash and per-file bundle manifest.
   On Windows verify both NSSM archive and extracted x64 hashes; retain bundled
   source and licence in the sanitized evidence set. Never execute a mirror or
   a version chosen dynamically by Chocolatey.
2. Run packaging negative tests and the matching native-bundle smoke with the
   staged runtime. Run Linux filesystem transaction tests and Windows real native
   argv/ACL tests. Negative controls must prove changed bytes, missing credentials,
   links, writable executable ACLs and injected start failure are rejected.
3. Before any real service installation, establish OS-level outbound denial and
   independently prove a direct TCP attempt is denied. Preserve any runner control
   channel needed for cleanup, and identify its exception explicitly; do not call
   the service isolated solely because an application mock rejects network calls.
   Abort before service creation if isolation cannot be safely established.
4. Assert the fixed VMPower service names, install paths, account and configuration
   do not already exist. Use the fixed production names only on this disposable
   host, so the tested path matches the installer. No customer resources exist on
   it. Register unconditional cleanup before creating any service or test files.
5. Install synthetic JSON credentials from stdin, including quotes/backslashes,
   with a synthetic noncustomer host. Start the actual systemd/NSSM service and
   identify its real staged Node child and command line without printing secrets.
   Confirm the exact config reader succeeds, native collection reaches the report
   caller, and network access fails at the OS boundary. Verify account, executable
   permissions, configuration permissions and wrapper identity.
6. Stop and confirm the child PID exits; restart; upgrade to a second fixture
   manifest without supplying credentials and compare configuration byte hashes.
   Keep the second fixture visibly synthetic, never a release candidate. Test
   a stopped/disabled upgrade too. Inject a broken entrypoint that really fails
   startup, assert rollback to previous runtime and running state, and compare
   config hashes. A service-manager status alone may hide a restarting failed child;
   observe child stability and exit state across the configured restart interval.
7. Uninstall through the new installer; verify service and child absence and retained
   config/release hashes. Separately test migration from synthetic legacy unit/NSSM
   settings and package-manager ownership. Preserve published legacy artifacts.
   A legacy uninstaller may delete config and must not be used as cleanup.
8. In finally/always cleanup, remove only the synthetic resources recorded by this
   run and restore runner network rules. Verify no service, Node/NSSM child, config,
   test account, temporary executable or credential-bearing log remains. Upload
   only sanitized hashes, versions, checks and cleanup results, not configuration
   or raw service environment. Any failed cleanup fails qualification.

No binary signing identity, release version, customer OS inventory, customer
installation or artifact download has been qualified by this plan. A successful
native run is an input to a separately authorized release and consumer readback.
