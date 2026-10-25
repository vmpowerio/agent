// Run before the preload inside the same Docker --network none namespace.
const net = require('net');
const os = require('os');
if (Object.values(os.networkInterfaces()).flat().some(value => !value.internal)) process.exit(2);
const socket = net.connect(443, '192.0.2.1');
socket.on('connect', () => process.exit(3));
socket.on('error', error => {
  if (!['ENETUNREACH', 'EHOSTUNREACH', 'EPERM'].includes(error.code)) process.exit(4);
  console.log('OS network-denial negative control PASS');
});
socket.setTimeout(2000, () => { socket.destroy(); process.exit(5); });
