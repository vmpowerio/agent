// Fixed hosted-fixture layout, anchored to the running staged executable.
// No argv/environment value selects a file to read or overwrite.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
function regularFile(file) {
  const info = fs.lstatSync(file);
  assert(info.isFile() && !info.isSymbolicLink() && info.nlink === 1, 'Fixture file must be regular and unlinked');
  assert.equal(fs.realpathSync(file), file, 'Fixture file must have a canonical path');
}
function fixturePaths() {
  assert.equal(process.argv.length, 2, 'Fixture path arguments are not accepted');
  const executable = process.execPath;
  const runtime = path.dirname(executable);
  const bundle = path.dirname(runtime);
  const root = path.dirname(bundle);
  assert.equal(path.basename(executable), process.platform === 'win32' ? 'node.exe' : 'node');
  assert.equal(path.basename(runtime), 'runtime');
  assert.equal(path.basename(bundle), 'bundle');
  assert.equal(path.basename(root), 'vmpower-native');
  for (const directory of [root, bundle, runtime]) {
    assert(fs.lstatSync(directory).isDirectory(), 'Fixture directory must not be a link');
    assert.equal(fs.realpathSync(directory), directory, 'Fixture directory must have a canonical path');
  }
  regularFile(executable);
  const manifest = path.join(bundle, 'bundle-manifest.json');
  const app = path.join(bundle, 'app', 'index.js');
  regularFile(manifest);
  regularFile(app);
  return { bundle, manifest, app, receiver: path.join(root, 'receiver.json') };
}
function openReceiverState() {
  const output = fixturePaths().receiver;
  // Reject pre-existing files/links. Retain the exclusive descriptor so a path
  // replacement cannot redirect a later report write to another file.
  const fd = fs.openSync(output, 'wx', 0o600);
  const info = fs.fstatSync(fd);
  assert(info.isFile() && info.nlink === 1, 'Receiver state must be a new regular file');
  return fd;
}
module.exports = { fixturePaths, openReceiverState };
