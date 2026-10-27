# Linux Debian ownership and rollback candidate

This source prepares one package builder and the actual maintainer hooks for a
future release. It is not published or customer-qualified. The optional native
gate below has not run. The current public binaries remain unchanged.

The two cached public Linux packages are both `vmpower-agent` version `0.1`, with
different executable bytes. They own `/usr/bin/vmpower`,
`/etc/systemd/system/vmpower.service`, and `/etc/vmpower-template.conf`. Replacing
the first two files with the standalone bundle installer leaves their old dpkg
ownership intact. The hosted negative controls must demonstrate both old removal
and old reinstall replacing those modern entrypoints, for **both** exact packages:

| Input | Bytes | SHA256 |
| --- | ---: | --- |
| dashboard-linux.deb | 10794560 | 06bf0d767e4625db0477f3e23e30b4e36ac6e7a7d50882b3c655c472f45875b1 |
| docs-linux.deb | 11090764 | e8c0028ea78322e5937becba766654f178e4dfbe1c9817af33e024d93b588311 |

`scripts/build-linux-deb.py` uses a verified `linux-x64` bundle and an explicit
package version. The supported version series is `0.2.0`, optionally with a
prerelease suffix such as `0.2.0~rc1`; the hosted gate compares versions with real
dpkg. Application version `0.0.2` is deliberately not used as a Debian upgrade
over `0.1`. Choosing the final published version remains a release decision.

The resulting amd64 package owns the launcher, service unit, packaged bundle and
installer helpers. The launcher/unit bytes come from the same rendering functions
used by the existing Linux installer. The packaged bundle is installed by that
qualified installer into retained, immutable `/opt/vmpower-agent/releases` paths.
These retained rollback releases, `/etc/vmpower.conf`, the dedicated service
account and root-only transaction snapshots are not removed by package removal
or purge. They remain explicitly managed state; this is an intentional retention
policy, not a claim that dpkg owns every retained file.

The actual preinst accepts both pinned legacy binary families and fails closed
on an unknown legacy executable. It journals the recognized unit/launcher and service
state before dpkg unpacks replacement files. Existing JSON configuration bytes
are preserved. First installation retains the previous `API_KEY`, `API_SECRET`,
`VM_ID` environment contract and creates configuration exclusively as 0600.
Postinst restores the pre-unpack service files and calls the existing verified
installer, which creates/validates a dedicated nonroot service account, assigns
root:vmpower 0640 configuration access, preserves stopped/enabled state, checks a
stable child, and restores runtime state on activation failure. Prerm stops and
disables the service on removal; postrm reloads systemd. Upgrade hooks retain the
predecessor until the installer's transaction replaces it.

If postinst fails, a running predecessor is restored, but dpkg correctly reports
the candidate as **half-configured**. Do not call that a complete rollback.
Reinstall the retained, verified previous deb with `dpkg --install PREVIOUS.deb`
to restore the installed version and package database ownership. No hook runs a
nested dpkg operation under its lock. The gate tests both the immediate runtime
restoration and this explicit package rollback. Retain the prior exact deb and
its hash before a real upgrade; do not delete the snapshots or old public URLs.

## Bounded hosted gate proposal — not dispatched

Use only an explicitly admitted disposable Ubuntu 24 x64 runner with systemd and
the existing 15-minute Linux native ceiling. The integrated existing workflow
enables this extension on the explicit combined candidate branch and retains its
events, permissions and timeout. Root must admit the exact source and invocation
before any push; this source preparation has not dispatched CI.

1. Retain the existing source tests and native input preparation. Run
   `python3 scripts/test-linux-deb.py` and the existing Linux installer tests.
   Stage the same pinned Node24 bundle/upgrade/crashing inputs with the unchanged
   preparation helper. `prepare-legacy-package-inputs.py` stages the two exact
   archived public families above from their fixed HTTPS origins into
   `$RUNNER_TEMP/vmpower-native/legacy-debs/` without installing or executing them.
   It reuses the existing no-redirect downloader and verifies hashes and sizes;
   the qualifier independently verifies both byte counts and SHA256 values.
2. Run the existing guarded native qualifier with its optional flag:
   `sudo --preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT,GITHUB_RUN_ID,ImageOS,ImageVersion,RUNNER_TEMP python3 scripts/qualify-native-linux.py --output "$RUNNER_TEMP/vmpower-native" --legacy-dpkg`.
   It retains the original nine native phases and their network/cleanup controls.
   It rejects pre-existing package registration, account/config/service or owned
   package paths before reading any customer configuration.
3. Keep the existing service-user IPv4/IPv6 rules. Immediately before any actual
   package install, `linux-package-quarantine.py` adds two journaled whole-runner
   OUTPUT rejects for every nonloopback IPv4/IPv6 protocol and identity. Root and
   service-user socket probes must reach owned loopback and receive explicit
   external denial; the IPv4 reject counter must cover both probes. IPv6 rule
   readback plus an explicit denial is retained even on a host without an IPv6
   route. No package download or dependency resolution occurs under quarantine.
   This temporarily disconnects the GitHub runner's control connection as well.
   A separate-session watchdog must acknowledge readiness before rules are added.
   After 420 seconds it terminates the qualifier's private process group (including
   dpkg/maintainer descendants) before removing only the two whole-runner rules.
   Existing service-UID rules still deny any systemd child until ordinary cleanup.
   Deadline expiry is a failed run, never acceptance or an automatic retry.
   Under this quarantine, install each exact
   legacy deb with real dpkg and pre-existing synthetic config. Only its known
   postinst executes; **no legacy Node10 executable starts**. Run both collision
   controls. Build and install real `0.2.0~rc1`, `rc2`, `rc3` packages with the same
   builder/hooks intended for release; use rc3's existing crashing Node24 fixture
   for rollback. Verify actual dpkg ownership/version/checksums, unchanged config
   bytes, root:vmpower 0640 access, service-user readability and ordinary-user
   denial, nonroot stable startup and two reports, actual hook-driven upgrade,
   half-configured failure plus explicit prior-deb rollback, and actual package
   remove/reinstall/purge retention. Do not substitute mocked package operations.
4. Always invoke the existing `--cleanup` entrypoint. Its journal now includes
   the exact fixture-owned package registration and additional fixed paths when
   this option is used; the cleanup command needs no extra flag. It purges only
   that registration **before dropping quarantine**, verifies absence, stops the
   exact watchdog, removes owned state, stops all owned
   processes/receivers, removes firewall rules and restores the original `/opt`
   permissions. Upload sanitized lifecycle/cleanup JSON only; no packages,
   configuration bytes, raw environments or snapshots.

Expected incremental gate work is two package-family matrices within the existing
15-minute Linux job. The two-report waits alone need about 60 seconds per family;
package compression, installation and rollback add time. This is an unmeasured
bound proposal; timeout or incomplete cleanup is failure, not permission to
increase resources or rerun automatically.

The prior candidate incorrectly treated service-UID denial as sufficient for
root package hooks. The package entrypoint now enforces whole-runner quarantine;
the watchdog is necessary because a blocked runner cannot receive its next
cleanup action. Offline tests cover the exact rule scope, process-group identity,
deadline kill-before-restore ordering and reused-process refusal. Hosted proof of
watchdog recovery, root/service denial, runner reconnection and package cleanup is
still required. Do not bypass this gate based on the old postinst's current
no-op behavior with existing synthetic configuration.

## Remaining release limits

Only modern runtime execution on the admitted Ubuntu 24 host can be established
by this gate. Active legacy Node10 runtime compatibility, other distributions,
older customer kernels/libc/systemd/Python, customer architectures, actual fleet
versions and custom unit/account configurations remain unqualified. The package
requires Python 3.9+, systemd and compatible native libraries; those dependencies
do not prove any untested host is supported. Unknown units and unsafe ownership
fail closed. Exact old binary families are retained; Windows packaging is outside
this change. Existing HTTPS/source/hash provenance is preserved; no new signing
or publisher-authentication claim is introduced. Root owns execution admission,
final release version, immutable package publication, rollback inventory and
consumer readback after the live-usage audit.

References: [Debian maintainer-script upgrade ordering](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html),
[configuration handling](https://www.debian.org/doc/debian-policy/ap-pkg-conffiles.html),
and [dpkg version/ownership/verification behavior](https://manpages.debian.org/unstable/dpkg/dpkg.1.en.html).
