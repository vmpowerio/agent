# VMPower agent

Collects login data for your virtual machines and reports them to your VMPower account. Read the documentation [here](https://help.vmpower.com/VMPower%20agent%20%5Bbeta%5D/Getting-Started/).

# Developing

These are the instructions for developing this package. Only follow these if you need to make a change to the agent.
 
## Getting started

Use Node 18.20.8 with npm 10, or Node 24.22.1 with npm 11, for the
validated development toolchain. npm must support the scoped dependency
override; do not regenerate this lock with npm 6. The agent's runtime
dependency ranges, application version and production source are unchanged.

```bash
# Install dependencies
npm ci --legacy-peer-deps --ignore-scripts --no-audit --no-fund
# Transpile the code
npm run build
# Run the CLI
node build/index.js
```

## Running the tests

```bash
# Runs flow typechecks
npm run flow
# Runs the functional test suite
npm run test
# Runs the CI checks. Do this before a PR
npm run ci_validate
```

Jest 27 explicitly retains the previous jsdom test environment. `node-notifier`
remains an explicit development peer so optional test notifications are still
available; only its UUID dependency is pinned to the patched CommonJS release.
The regression tests exercise that actual caller with pipe creation and command
execution mocked, plus UUID bounds, JSON5 inheritance, multipart serialization,
Babel evaluation, and the real Windows `run-query` module. Query tests must
import `run-query`, assert the `query user` command, and distinguish Windows
exit code 1 from errors; tests of `run-last` do not establish that behavior.

The unchanged Flow 0.124 executable is x86_64 on macOS and cannot start on an
ARM Mac without a supported runtime. `npm run flow` now runs the full foreground
`flow check --max-workers 2`; it does not start the default background server.
A passing build, lint and functional suite does not replace `ci_validate`.
The Linux source-qualification job runs that complete command with a timeout,
separate Flow temporary directory, and cleanup on success, failure or signal.
No Flow ignores or tests are removed. Keep dependency updates at least seven
days old and verify their public registry dates and integrity before accepting
a new lock.

## CI and binary delivery

Pull requests to `master` run the GitHub Linux source-qualification job on Node
24.22.1. It installs the exact lock without package scripts or audit uploads,
then runs build, lint, full Flow and functional tests in a container with no
network. A real transport negative control verifies that namespace before
testing. Tests additionally deny Node transports and machine agent-config
reads; notification pipes, processes and provider calls remain synthetic.

Drone's ordinary branch pushes, pull requests and `master` merges run only the
modern source-validation step. Every legacy packaging and Azure publishing
step requires both the `tag` event and a `refs/tags/v*` reference. A release tag
is an explicit binary-delivery event; do not create one as part of source
integration. This follows Drone's [combined event/ref conditions](https://docs.drone.io/pipeline/docker/syntax/conditions/).
The legacy Node 10/nexe/Chocolatey distribution steps are retained, but remain
unqualified and must be modernized and tested before binary delivery. Node 10
cannot parse the new development UUID dependency and npm 6 ignores overrides;
a failed build is never a publishing safeguard.

These checks do not run an installed customer agent. Windows/Linux installer,
service lifecycle, and actual user-session behavior require separate delivery
validation. In particular, the current query fixture's stderr `error` event
does not model real stderr text (`data`); that pre-existing behavior is not
qualified by the query tests. Do not describe these tests as complete Windows
error-handling proof.
