const assert = require('assert');
require('./ci-test-boundary.cjs');
const fs = require('fs');
const blocked = fn => assert.throws(fn, error => error.code === 'EPERM');
blocked(() => require('net').connect(443, '192.0.2.1'));
blocked(() => require('net').createServer().listen(0, '127.0.0.1'));
blocked(() => require('tls').connect(443, '192.0.2.1'));
blocked(() => require('dns').lookup('synthetic.invalid', () => {}));
blocked(() => require('dgram').createSocket('udp4').send('synthetic', 53, '192.0.2.1'));
for (const filename of ['/etc/vmpower.conf', '/private/etc/vmpower.conf', 'C:\\ProgramData\\vmpower\\vmpower.conf']) {
  assert.strictEqual(fs.existsSync(filename), false);
  blocked(() => fs.readFileSync(filename));
}
assert(fs.readFileSync(__filename, 'utf8').includes('synthetic.invalid'));
console.log('CI transport/config negative controls PASS');
