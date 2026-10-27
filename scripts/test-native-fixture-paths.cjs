// Real helper subprocesses and temporary files; no sockets, services or installs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { test } = require('node:test');
// Three inner CLI calls each have a 5s deadline; allow another 5s for startup.
const helperDeadline = 20000;
function runHelper(executable, args, env, timeout = helperDeadline) {
  return new Promise((resolve, reject) => {
    // The admitted Windows fixture is C:\Windows. Refuse before creating any
    // child; cleanup must never select an executable from an environment path.
    if (process.platform === 'win32' && (process.env.SystemRoot !== 'C:\\Windows' || env.SystemRoot !== 'C:\\Windows')) {
      throw new Error('WINDOWS_SYSTEM_ROOT_REFUSED');
    }
    const child = spawn(executable, args, { env, windowsHide: true, detached: process.platform !== 'win32' });
    let stdout = '', stderr = '', timeoutError, reapFailure, closed;
    const failedReap = code => Object.assign(new AggregateError(
      [timeoutError, new Error(code)], 'OWNED_HELPER_TIMEOUT_AND_TREE_REAP_FAILED'), { cleanupUnsafe: true });
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    const timer = setTimeout(() => {
      timeoutError = Object.assign(new Error('OWNED_HELPER_TIMEOUT'), { code: 'ETIMEDOUT' });
      try {
        // Reap the captured live leader's tree before awaiting its close. Never
        // use spawnSync's timeout: it kills the leader without its descendants.
        if (process.platform === 'win32') {
          const killed = spawnSync('C:\\Windows\\System32\\taskkill.exe',
            ['/PID', String(child.pid), '/T', '/F'], { timeout: 5000, windowsHide: true, encoding: 'utf8' });
          if (killed.error || killed.status !== 0) throw new Error('OWNED_TREE_TERMINATION_FAILED');
        } else {
          process.kill(-child.pid, 'SIGKILL');
        }
      } catch (error) { reapFailure = error; }
      closed = setTimeout(() => reject(failedReap('OWNED_TREE_CLOSE_TIMEOUT')), 5000);
    }, timeout);
    child.on('error', error => { clearTimeout(timer); clearTimeout(closed); reject(error); });
    child.on('close', (status, signal) => {
      clearTimeout(timer); clearTimeout(closed);
      if (reapFailure) return reject(failedReap('OWNED_TREE_TERMINATION_FAILED'));
      if (!timeoutError && signal) return reject(Object.assign(new Error('UNEXPECTED_HELPER_SIGNAL'), { cleanupUnsafe: true }));
      resolve({ status, signal, stdout, stderr, pid: child.pid,
        error: timeoutError });
    });
  });
}
async function fixture(check) {
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
    const run = (name, args = [], executable = node, timeout = helperDeadline) => runHelper(executable,
      ['--require', preload, path.join(__dirname, name), ...args],
      { ...process.env, NODE_OPTIONS: '', GITHUB_ACTIONS: 'true', RUNNER_ENVIRONMENT: 'github-hosted' }, timeout);
    await check({ temp, root, bundle, node, run, preload });
  } catch (error) {
    primaryFailure = error;
    throw error;
  } finally {
    try {
      if (primaryFailure && primaryFailure.cleanupUnsafe) throw new Error('FIXTURE_RETAINED_TREE_CLEANUP_UNPROVED');
      // Allow brief Windows sharing delays after synchronous children exit.
      // Node handles readonly files; persistent denial must still fail the test.
      fs.rmSync(temp, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch (cleanupFailure) {
      if (primaryFailure) throw new AggregateError([primaryFailure, cleanupFailure], 'Fixture and cleanup both failed');
      throw cleanupFailure;
    }
  }
}
// Exercise this exact helper under a modeled Windows host on every platform.
// No Windows executable is invoked; real native qualification remains separate.
function windowsHelperModel(root = 'C:\\Windows', killStatus = 0) {
  const { EventEmitter } = require('node:events');
  const { PassThrough } = require('node:stream');
  const source = fs.readFileSync(__filename, 'utf8');
  const child = new EventEmitter();
  child.pid = 1234; child.stdout = new PassThrough(); child.stderr = new PassThrough();
  const calls = [], timers = [], host = { platform: 'win32', env: { SystemRoot: root } };
  const helper = require('node:vm').runInNewContext(
    source.slice(source.indexOf('function runHelper('), source.indexOf('async function fixture(')) + '\nrunHelper',
    { process: host, helperDeadline, path,
      spawn(...args) { calls.push(['spawn', ...args]); return child; },
      spawnSync(...args) { calls.push(['kill', ...args]); return { status: killStatus }; },
      setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {} });
  return { helper, child, calls, timers, host };
}
test('Windows helper refuses missing or unadmitted roots before any child exists', async () => {
  for (const root of [undefined, '', 'D:\\Windows', 'C:\\Windows\\evil', 'C:Windows', 'c:\\windows']) {
    const m = windowsHelperModel(root);
    // Explicit undefined must model a missing environment entry, not the default.
    m.host.env.SystemRoot = root;
    await assert.rejects(m.helper('unused', [], { SystemRoot: 'C:\\Windows' }), /WINDOWS_SYSTEM_ROOT_REFUSED/);
    assert.equal(m.calls.length, 0); assert.equal(m.timers.length, 0);
  }
  for (const env of [{}, { SystemRoot: 'D:\\Windows' }]) {
    const m = windowsHelperModel();
    await assert.rejects(m.helper('unused', [], env), /WINDOWS_SYSTEM_ROOT_REFUSED/);
    assert.equal(m.calls.length, 0); assert.equal(m.timers.length, 0);
  }
});
test('Windows timeout fixes taskkill identity and awaits captured child close', async () => {
  for (const killStatus of [0, 1]) {
    const m = windowsHelperModel('C:\\Windows', killStatus);
    let settled = false;
    const pending = m.helper('owned-node', [], { SystemRoot: 'C:\\Windows' });
    pending.then(() => { settled = true; }, () => { settled = true; });
    m.host.env.SystemRoot = 'D:\\changed-after-spawn';
    m.timers[0]();
    assert.equal(m.calls[1][1], 'C:\\Windows\\System32\\taskkill.exe');
    assert.deepEqual(Array.from(m.calls[1][2]), ['/PID', '1234', '/T', '/F']);
    assert.equal(m.calls[1][3].timeout, 5000);
    await Promise.resolve(); assert.equal(settled, false);
    m.child.emit('close', null, 'SIGKILL');
    if (killStatus) await assert.rejects(pending, error => error.cleanupUnsafe === true && error.errors.length === 2);
    else assert.equal((await pending).error.code, 'ETIMEDOUT');
  }
});
function passes(result) { assert.equal(result.error, undefined); assert.equal(result.signal, null); assert.equal(result.status, 0, result.stderr); }
function rejects(result) { assert.equal(result.error, undefined); assert.equal(result.signal, null); assert.notEqual(result.status, 0); }
async function assertExited(pid) {
  const deadline = Date.now() + 3000;
  while (true) {
    try { process.kill(pid, 0); }
    catch (error) { if (error.code === 'ESRCH') return; throw error; }
    assert(Date.now() < deadline, 'OWNED_PROCESS_SURVIVED');
    await new Promise(resolve => setTimeout(resolve, 25));
  }
}
test('outer budget lets three individually bounded slow CLI children finish', () => fixture(async ({ bundle, run }) => {
  fs.writeFileSync(path.join(bundle, 'app/index.js'), "const a=process.argv[2];setTimeout(()=>{console.log(a==='--version'?'0.0.2':a==='--help'?'--virtualMachine':'required option');process.exit(a?0:1);},3000);");
  const result = await run('native-bundle-smoke.cjs');
  passes(result);
  await assertExited(result.pid);
}));
test('outer timeout reaps its real descendant before copied runtime cleanup', async () => {
  let owned;
  await fixture(async ({ temp, preload, run }) => {
    owned = temp;
    const journal = path.join(temp, 'owned-child.json');
    const childSource = `require('node:fs').writeFileSync(${JSON.stringify(journal)},JSON.stringify({pid:process.pid}));setInterval(()=>{},1000);`;
    fs.appendFileSync(preload, `
      require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(childSource)}],{stdio:'inherit'});
      setInterval(()=>{},1000);
    `);
    const result = await run('native-report-fixture.cjs', [], undefined, 4000);
    assert.equal(result.error.code, 'ETIMEDOUT');
    const childPid = JSON.parse(fs.readFileSync(journal)).pid;
    assert(Number.isSafeInteger(childPid) && childPid > 0 && childPid !== result.pid);
    await assertExited(result.pid);
    await assertExited(childPid);
  });
  assert.equal(fs.existsSync(owned), false);
});
test('fixture cleans readonly files after a primary failure and preserves that failure', async () => {
  const primary = new Error('FIXTURE_PRIMARY_FAILURE');
  let owned;
  await assert.rejects(() => fixture(async ({ temp }) => {
    owned = temp;
    const file = path.join(temp, 'readonly');
    fs.writeFileSync(file, 'fixture'); fs.chmodSync(file, 0o400);
    throw primary;
  }), error => error === primary);
  assert.equal(fs.existsSync(owned), false);
});
for (const primaryFails of [false, true]) {
  test(`fixture preserves persistent cleanup failure with primary failure ${primaryFails}`, async context => {
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
      await assert.rejects(() => fixture(async ({ temp }) => {
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
test('real smoke helper uses the executable bundle without a path argument', () => fixture(async ({ run }) => {
  passes(await run('native-bundle-smoke.cjs'));
}));
test('real receiver writes only its fixed new state file without a path argument', () => fixture(async ({ root, run }) => {
  passes(await run('native-report-fixture.cjs'));
  const file = path.join(root, 'receiver.json');
  assert.equal(JSON.parse(fs.readFileSync(file)).port, 12345);
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
}));
test('smoke rejects an otherwise valid caller-selected bundle', () => fixture(async ({ bundle, run }) => {
  rejects(await run('native-bundle-smoke.cjs', [bundle]));
}));
test('receiver rejects an arbitrary overwrite target and preserves sentinel', () => fixture(async ({ temp, run }) => {
  const sentinel = path.join(temp, 'must-preserve.json');fs.writeFileSync(sentinel, 'sentinel');
  rejects(await run('native-report-fixture.cjs', [sentinel]));assert.equal(fs.readFileSync(sentinel, 'utf8'), 'sentinel');
}));
test('both helpers reject a non-staged runtime', () => fixture(async ({ run }) => {
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(await run(name, [], process.execPath));
}));
test('both helpers reject a wrong runtime filename', () => fixture(async ({ node, run }) => {
  const wrong = path.join(path.dirname(node), process.platform === 'win32' ? 'other.exe' : 'other');
  fs.copyFileSync(node, wrong);fs.chmodSync(wrong, 0o755);
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(await run(name, [], wrong));
}));
test('both helpers reject wrong bundle layout', () => fixture(async ({ bundle, node, run }) => {
  const wrong = path.join(path.dirname(bundle), 'other-bundle');fs.renameSync(bundle, wrong);
  for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(await run(name, [], path.join(wrong, 'runtime', path.basename(node))));
}));
for (const file of ['bundle-manifest.json', 'app/index.js']) {
  test('both helpers reject a symbolic link at ' + file, () => fixture(async ({ temp, bundle, run }) => {
    const target = path.join(bundle, file);const outside = path.join(temp, 'outside');fs.renameSync(target, outside);fs.symlinkSync(outside, target, 'file');
    for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(await run(name));
  }));
  test('both helpers reject a hard link at ' + file, () => fixture(async ({ temp, bundle, run }) => {
    fs.linkSync(path.join(bundle, file), path.join(temp, 'outside'));
    for (const name of ['native-bundle-smoke.cjs', 'native-report-fixture.cjs']) rejects(await run(name));
  }));
}
test('receiver refuses an existing regular file', () => fixture(async ({ root, run }) => {
  const target = path.join(root, 'receiver.json');fs.writeFileSync(target, 'sentinel');
  rejects(await run('native-report-fixture.cjs'));assert.equal(fs.readFileSync(target, 'utf8'), 'sentinel');
}));
test('receiver refuses a symbolic-link overwrite', () => fixture(async ({ temp, root, run }) => {
  const sentinel = path.join(temp, 'outside');fs.writeFileSync(sentinel, 'sentinel');
  fs.symlinkSync(sentinel, path.join(root, 'receiver.json'), 'file');
  rejects(await run('native-report-fixture.cjs'));assert.equal(fs.readFileSync(sentinel, 'utf8'), 'sentinel');
}));
test('replacing receiver pathname cannot redirect a subsequent report write', () => fixture(async ({ root, run, preload }) => {
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
  passes(await run('native-report-fixture.cjs'));
  assert.equal(fs.readFileSync(path.join(root, 'receiver.json'), 'utf8'), 'sentinel');
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, 'receiver.json.held'))).count, 1);
}));
