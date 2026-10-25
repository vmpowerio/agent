// Direct sockets, no application mock. Firewall rules must already be installed.
const net = require('node:net');
const assert = require('node:assert/strict');
assert.equal(process.env.GITHUB_ACTIONS, 'true');
assert.equal(process.env.RUNNER_ENVIRONMENT, 'github-hosted');
function connect(host, port) {
  return new Promise(resolve => {
    const socket = net.connect({host, port});
    socket.setTimeout(5000);
    socket.once('connect', () => { socket.destroy(); resolve('CONNECTED'); });
    socket.once('error', error => resolve(error.code));
    socket.once('timeout', () => { socket.destroy(); resolve('TIMEOUT'); });
  });
}
(async () => {
  assert.equal(await connect('127.0.0.1', Number(process.argv[2])), 'CONNECTED', 'Owned loopback unavailable');
  const code = await connect('1.1.1.1', 443);
  // Windows must positively attribute the rejection, not pass on an arbitrary timeout.
  const allowed = process.platform === 'win32' ? ['EACCES', 'EPERM'] : ['EACCES', 'EPERM', 'EHOSTUNREACH', 'ENETUNREACH', 'ECONNREFUSED'];
  assert(allowed.includes(code), 'Direct nonloopback denial not established: ' + code);
  console.log(JSON.stringify({loopback: 'PASS', external: 'DENIED', code}));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
