// Real helper subprocesses and temporary files; no sockets, services or installs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
function fixture(check) {
  const temp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'native-path-control-')));
  let primaryFailure;
  try {
    const root = path.join(temp, 'vmpower-native');
    const bundle = path.join(root, 'bundle');
    fs.mkdirSync(path.join(bundle, 'runtime'), { recursive: true });
    fs.mkdirSync(path.join(bundle, 'app'));
    const node = path.join(bundle, 'runtime', process.platform === 'win32' ? 'node.exe' : 'node');
    fs.copyFileSync(process.execPath, node); fs.chmodSync(node, 0o755);
    fs.writeFileSync(path.join(bundle, 'bundle-manifest.json'), JSON.stringify({ target: process.platform === 'win32' ? 'win-x64' : 'linux-x64', nodeVersion: process.versions.node }));
    // Synthetic CLI proves path selection only. Real bundle CLI remains native CI.
    fs.writeFileSync(path.join(bundle, 'app/index.js'), "const a=process.argv[2]; console.log(a==='--version'?'0.0.2':a==='--help'?'--virtualMachine':'required option');process.exit(a?0:1);");
    const preload = path.join(temp, 'no-listener.cjs');
    fs.writeFileSync(preload, `
      const assert=require('node:assert/strict');
      require('node:net').Server.prototype.listen=()=>{throw new Error('REAL_LISTENER_DENIED')};
      require('node:net').Socket.prototype.connect=()=>{throw new Error('OUTBOUND_DENIED')};
      // Simulate only platform guards on Darwin; never open a local listener.
      if(process.platform==='darwin')Object.defineProperty(process,'platform',{value:'linux'});
      Object.defineProperty(process,'arch',{value:'x64'});
      require('node:http').createServer=()=>({listen(port,host,ready){assert.equal(port,0);assert.equal(host,'127.0.0.1');ready();},address(){return {port:12345}},close(done){done();},closeAllConnections(){}});
    `);
    const run = (name, args = [], executable = node) => spawnSync(executable, ['--require', preload, path.join(__dirname, name), ...args], {
      encoding: 'utf8', timeout: 8000,
      env: { ...process.env, NODE_OPTIONS: '', GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted' }
    });
    check({ temp, root, bundle, node, run, preload });
  } catch (error) {
    primaryFailure = error;
    throw error;
  } finally {
    try {
      // Allow brief Windows sharing delays after synchronous children exit.
      // Node handles readonly files; persistent denial must still fail the test.
      fs.rmSync(temp, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch (cleanupFailure) {
      if (primaryFailure) throw new AggregateError([primaryFailure, cleanupFailure], 'Fixture and cleanup both failed');
      throw cleanupFailure;
    }
  }
}
function passes(result) { assert.equal(result.error, undefined); assert.equal(result.signal, null); assert.equal(result.status, 0, result.stderr); }
function rejects(result) { assert.equal(result.error, undefined); assert.equal(result.signal, null); assert.notEqual(result.status, 0); }
test('fixture cleans readonly files after a primary failure and preserves that failure', () => {
  const primary = new Error('FIXTURE_PRIMARY_FAILURE');
  let owned;
  assert.throws(() => fixture(({ temp }) => {
    owned = temp;
    const file = path.join(temp, 'readonly');
    fs.writeFileSync(file, 'fixture'); fs.chmodSync(file, 0o400);
    throw primary;
  }), error => error === primary);
  assert.equal(fs.existsSync(owned), false);
});
for (const primaryFails of [false, true]) {
  test(`fixture preserves persistent cleanup failure with primary failure ${primaryFails}`, context => {
    const primary = new Error('FIXTURE_PRIMARY_FAILURE');
    const denied = Object.assign(new Error('FIXTURE_CLEANUP_DENIED'), { code: 'EPERM' });
    const remove = fs.rmSync;
    let owned;
    const removal = context.mock.method(fs, 'rmSync', (target, options) => {
      if (target !== owned) return remove(target, options);
      // Model native removal exhausting its bounded retry, not a successful delete.
      throw denied;
    });
    try {
      assert.throws(() => fixture(({ temp }) => {
        owned = temp;
        if (primaryFails) throw primary;
      }), error => primaryFails
        ? error instanceof AggregateError && error.errors[0] === primary && error.errors[1] === denied
        : error === denied);
      assert.equal(fs.existsSync(owned), true);
    } finally {
      removal.mock.restore();
      if (owned) remove(owned, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    }
  });
}
test('real smoke helper uses the executable bundle without a path argument', () => fixture(({ run }) => {
  passes(run('native-bundle-smoke.cjs'));
}));
test('real receiver writes only its fixed new state file without a path argument', () => fixture(({ root, run }) => {
  passes(run('native-report-fixture.cjs'));
  const file = path.join(root, 'receiver.json');
  assert.equal(JSON.parse(fs.readFileSync(file)).port, 12345);
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
}));
test('smoke rejects an otherwise valid caller-selected bundle', () => fixture(({ bundle, run }) => {
  rejects(run('native-bundle-smoke.cjs', [bundle]));
}));
test('receiver rejects an arbitrary overwrite target and preserves sentinel', () => fixture(({ temp, run }) => {
  const sentinel = path.join(temp, 'must-preserve.json');fs.writeFileSync(sentinel, 'sentinel');
  rejects(run('native-report-fixture.cjs', [sentinel]));assert.equal(fs.readFileSync(sentinel, 'utf8'), 'sentinel');
}));
test('both helpers reject a non-staged runtime', () => fixture(({ run }) => {
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(run(name, [], process.execPath));
}));
test('both helpers reject a wrong runtime filename', () => fixture(({ node, run }) => {
  const wrong = path.join(path.dirname(node), process.platform === 'win32' ? 'other.exe' : 'other');
  fs.copyFileSync(node, wrong);fs.chmodSync(wrong, 0o755);
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(run(name, [], wrong));
}));
test('both helpers reject wrong bundle layout', () => fixture(({ bundle, node, run }) => {
  const wrong = path.join(path.dirname(bundle), 'other-bundle');fs.renameSync(bundle, wrong);
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(run(name, [], path.join(wrong, 'runtime', path.basename(node))));
}));
for (const file of ['bundle-manifest.json', 'app/index.js']) {
  test('both helpers reject a symbolic link at ' + file, () => fixture(({ temp, bundle, run }) => {
    const target = path.join(bundle, file);const outside = path.join(temp, 'outside');fs.renameSync(target, outside);fs.symlinkSync(outside, target, 'file');
    for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(run(name));
  }));
  test('both helpers reject a hard link at ' + file, () => fixture(({ temp, bundle, run }) => {
    fs.linkSync(path.join(bundle, file), path.join(temp, 'outside'));
    for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(run(name));
  }));
}
test('receiver refuses an existing regular file', () => fixture(({ root, run }) => {
  const target = path.join(root, 'receiver.json');fs.writeFileSync(target, 'sentinel');
  rejects(run('native-report-fixture.cjs'));assert.equal(fs.readFileSync(target, 'utf8'), 'sentinel');
}));
test('receiver refuses a symbolic-link overwrite', () => fixture(({ temp, root, run }) => {
  const sentinel = path.join(temp, 'outside');fs.writeFileSync(sentinel, 'sentinel');
  fs.symlinkSync(sentinel, path.join(root, 'receiver.json'), 'file');
  rejects(run('native-report-fixture.cjs'));assert.equal(fs.readFileSync(sentinel, 'utf8'), 'sentinel');
}));
test('replacing receiver pathname cannot redirect a subsequent report write', () => fixture(({ root, run, preload }) => {
  fs.appendFileSync(preload, `
    const fs=require('node:fs'),path=require('node:path');
    const output=path.join(path.dirname(path.dirname(path.dirname(process.execPath))),'receiver.json');
    require('node:http').createServer=handler=>({
      listen(port,host,ready){
        ready();fs.renameSync(output,output+'.held');fs.writeFileSync(output,'sentinel');
        const config=require(${JSON.stringify(path.join(__dirname, 'native-report-fixture.cjs'))}).config;
        const raw=JSON.stringify({data:['synthetic row'],platform:process.platform,version:'0.0.2',systemTime:1});
        handler({method:'POST',url:'/login-data?vm=fixture-native',headers:{'x-api-key':config.key,'x-api-secret':config.secret},on(event,callback){if(event==='data')callback(Buffer.from(raw));if(event==='end')callback();}}, {writeHead(){},end(){}});
      },address(){return {port:12345}},close(done){done();},closeAllConnections(){}
    });
  `);
  passes(run('native-report-fixture.cjs'));
  assert.equal(fs.readFileSync(path.join(root, 'receiver.json'), 'utf8'), 'sentinel');
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'receiver.json.held'))).count, 1);
}));
