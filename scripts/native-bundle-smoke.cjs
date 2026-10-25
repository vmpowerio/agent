// Invoke with the staged runtime itself. No services, customer config or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const paths = require('./native-fixture-paths.cjs').fixturePaths();
const root = paths.bundle;
const manifest = JSON.parse(fs.readFileSync(paths.manifest, 'utf8'));
const expectedPlatform = manifest.target === 'win-x64' ? 'win32' : 'linux';
assert.equal(process.platform, expectedPlatform, 'Native target execution is required');
assert.equal(process.arch, 'x64');
assert.equal(process.versions.node, manifest.nodeVersion);
assert.equal(fs.realpathSync(process.execPath), fs.realpathSync(path.join(root, 'runtime', process.platform === 'win32' ? 'node.exe' : 'node')));
const boundary = path.resolve(__dirname, 'ci-test-boundary.cjs');
const env = { CI: 'true' };
for (const key of ['PATH', 'Path', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'HOME']) {
  if (process.env[key]) env[key] = process.env[key];
}
for (const [args, code, expected] of [
  [['--version'], 0, '0.0.2'],
  [['--help'], 0, '--virtualMachine'],
  [[], 1, 'required option']
]) {
  const child = spawnSync(process.execPath, ['--require', boundary, paths.app, ...args], {
    encoding: 'utf8', timeout: 5000, env, windowsHide: true
  });
  assert.equal(child.error, undefined);
  assert.equal(child.signal, null);
  assert.equal(child.status, code);
  assert((child.stdout + child.stderr).includes(expected));
}
console.log(JSON.stringify({ nativeBundleSmoke: 'PASS', target: manifest.target, node: process.versions.node, cases: 3,
  serviceLifecycle: 'NOT_TESTED', customerConfiguration: 'DENIED', transports: 'DENIED_BY_PRELOAD' }));
