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
ARM Mac without a supported runtime. A passing build, lint and functional suite
does not replace `ci_validate`: qualify Flow on a supported isolated host before
release. The legacy Drone Node 10 builder and Windows/Linux installers require
their own delivery validation; these local tests do not run the installed agent
or contact a VMPower service. Keep dependency updates at least seven days old
and verify their public registry dates and integrity before accepting a new lock.
