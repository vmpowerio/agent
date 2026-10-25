const assert = require('node:assert/strict');
const { config, validReport } = require('./native-report-fixture.cjs');
const headers = {'x-api-key': config.key, 'x-api-secret': config.secret};
const body = {data: ['synthetic row'], systemTime: Date.now(), platform: 'linux', version: '0.0.2'};
const check = (method='POST', url='/login-data?vm=fixture-native', h=headers, b=body, p='linux') => validReport(method,url,h,b,p);
assert.equal(check(), true);
for (const args of [['GET'], ['POST','/login-data?vm=other'], ['POST','/login-data?vm=fixture-native',{}],
  ['POST','/login-data?vm=fixture-native',headers,{...body,data: 'not rows'}],
  ['POST','/login-data?vm=fixture-native',headers,{...body,platform:'win32'}],
  ['POST','/login-data?vm=fixture-native',headers,{...body,version:'wrong'}]]) assert.equal(Boolean(check(...args)),false);
console.log(JSON.stringify({fixtureChecks:7,status:'PASS',networkListeners:0}));
