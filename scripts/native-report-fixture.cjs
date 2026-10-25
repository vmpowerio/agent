// Owned hosted-runner loopback receiver. Never persists login rows or headers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const config = { key: 'fixture "key"', secret: 'fixture\\secret$(literal)', vm_id: 'fixture-native' };
function validReport(method, url, headers, body, platform) {
  return method === 'POST' && url === '/login-data?vm=fixture-native' &&
    headers['x-api-key'] === config.key && headers['x-api-secret'] === config.secret &&
    body && Array.isArray(body.data) && body.data.every(row => typeof row === 'string') &&
    body.platform === platform && body.version === '0.0.2' && Number.isFinite(body.systemTime);
}
module.exports = { config, validReport };
if (require.main === module) {
  assert.equal(process.env.GITHUB_ACTIONS, 'true');
  assert.equal(process.env.RUNNER_ENVIRONMENT, 'github-hosted');
  assert(['linux', 'win32'].includes(process.platform));
  assert.equal(process.arch, 'x64');
  const output = process.argv[2];
  const state = { pid: process.pid, count: 0, rejected: 0, port: null, firstReportAt: null, lastReportAt: null };
  // Readers retry incomplete JSON; avoid Windows rename/share-delete races.
  function save() { fs.writeFileSync(output, JSON.stringify(state)); }
  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/probe') { res.end('fixture'); return; }
    let raw = ''; let oversized = false;
    req.on('data', chunk => { if (raw.length + chunk.length > 65536) oversized = true; else raw += chunk; });
    req.on('end', () => {
      let body; try { body = JSON.parse(raw); } catch { body = null; }
      if (!oversized && validReport(req.method, req.url, req.headers, body, process.platform)) {
        state.count++; state.lastReportAt = Date.now();
        if (state.firstReportAt === null) state.firstReportAt = state.lastReportAt;
        res.writeHead(200); res.end('{}');
      } else { state.rejected++; res.writeHead(400); res.end('{}'); }
      save();
    });
  });
  server.listen(0, '127.0.0.1', () => { state.port = server.address().port; save(); });
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {
    server.close(() => process.exit(0)); server.closeAllConnections();
  });
}
