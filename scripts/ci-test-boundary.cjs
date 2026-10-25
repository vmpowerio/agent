// Tests use synthetic configuration and mocked transports, never machine credentials.
const fs = require('fs');
const net = require('net');
const tls = require('tls');
const dns = require('dns');
const dgram = require('dgram');

function deny() {
  const error = new Error('CI transport or machine configuration access denied');
  error.code = 'EPERM';
  throw error;
}
net.Socket.prototype.connect = deny;
net.Server.prototype.listen = deny;
tls.connect = deny;
dgram.Socket.prototype.send = deny;
dgram.Socket.prototype.bind = deny;
for (const api of [dns, dns.promises]) {
  for (const name of Object.keys(api)) {
    if (/^(lookup|resolve|reverse)/.test(name) && typeof api[name] === 'function') api[name] = deny;
  }
}
if (globalThis.fetch) globalThis.fetch = deny;

function machineConfig(value) {
  if (typeof value !== 'string') return false;
  const name = value.replace(/\\/g, '/').toLowerCase();
  return name === '/etc/vmpower.conf' || name === '/private/etc/vmpower.conf' ||
    /^(?:[a-z]:)?\/programdata\/vmpower\/vmpower\.conf$/.test(name);
}
const exists = fs.existsSync;
fs.existsSync = function(value) { return machineConfig(value) ? false : exists.apply(this, arguments); };
for (const name of ['readFile', 'readFileSync', 'open', 'openSync']) {
  const original = fs[name];
  fs[name] = function(value) {
    if (machineConfig(value)) deny();
    return original.apply(this, arguments);
  };
}
