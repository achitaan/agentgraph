import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
import { availableParallelism } from 'node:os';

// The view checks run as SHARDS pages at once, each in its own Chrome (own profile, so nothing a page stores
// reaches another, and every page is a visible foreground tab with real animation frames). Page k runs every
// SHARDS-th test from the k-th (view-test.html); the lines are merged into one result.
const [chrome, work] = process.argv.slice(2);
// Four pages at least, eight at most, two thirds of the processors between (272 checks: 81 s at 4, 47 s at 8).
const SHARDS = Math.max(1, Number(process.env.AGENTGRAPH_VIEW_SHARDS) || Math.min(8, Math.max(4, Math.floor(availableParallelism() * 2 / 3))));
const browsers = [];
const timeout = setTimeout(() => { console.error('View checks timed out'); browsers.forEach(b => b.kill()); process.exit(1); }, 150000);  // a hang is cut off here

async function runPage(shard) {
  const browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--window-size=1400,900', '--remote-debugging-port=0', '--user-data-dir=' + work + '/profile-' + shard, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  browsers.push(browser);
  let socket;
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let log = '';
      browser.stderr.on('data', chunk => {
        log += chunk;
        const match = log.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) resolve(match[1]);
      });
      browser.once('error', reject);
      browser.once('exit', code => reject(new Error('Chrome exited early: ' + code)));
    });
    const address = new URL(endpoint);
    const targets = await fetch('http://' + address.host + '/json/list').then(r => r.json());
    socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    let id = 0;
    const pending = new Map();
    socket.addEventListener('message', event => {
      const response = JSON.parse(event.data);
      if (!pending.has(response.id)) return;
      const { resolve, reject } = pending.get(response.id); pending.delete(response.id);
      if (response.error) reject(new Error(response.error.message)); else resolve(response.result);
    });
    const call = (method, params = {}) => new Promise((resolve, reject) => {
      const key = ++id; pending.set(key, { resolve, reject }); socket.send(JSON.stringify({ id: key, method, params }));
    });
    await call('Page.enable');
    const loaded = new Promise(resolve => socket.addEventListener('message', function onLoad(event) {
      if (JSON.parse(event.data).method === 'Page.loadEventFired') { socket.removeEventListener('message', onLoad); resolve(); }
    }));
    await call('Page.navigate', { url: pathToFileURL(work + '/view-test.html').href + '?shard=' + shard + '&shards=' + SHARDS + (process.env.AGENTGRAPH_VIEW_ONLY ? '&only=' + encodeURIComponent(process.env.AGENTGRAPH_VIEW_ONLY) : '') });
    await loaded;
    const result = await call('Runtime.evaluate', {
      expression: `new Promise(resolve => { const timer = setInterval(() => { const value = document.getElementById('results')?.textContent; if (value && !value.includes('RESULT: RUNNING')) { clearInterval(timer); resolve(value); } }, 30); })`,
      awaitPromise: true, returnByValue: true
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
    const checks = await call('Runtime.evaluate', { expression: 'JSON.stringify(window.agentgraphChecks || null)', returnByValue: true });
    return { output: result.result.value, checks: JSON.parse(checks.result.value) || { declared: [], ran: [] } };
  } finally {
    socket?.close(); browser.kill();
  }
}

try {
  const pages = await Promise.all(Array.from({ length: SHARDS }, (_, shard) => runPage(shard).catch(error => { throw new Error('page ' + (shard + 1) + ' of ' + SHARDS + ': ' + (error?.stack || error)); })));
  const lines = pages.flatMap(page => page.output.split('\n'));
  const tests = lines.filter(line => line.startsWith('PASS ') || line.startsWith('FAIL '));
  const broken = lines.filter(line => line.startsWith('RESULT: FAIL') && !/^RESULT: FAIL \(\d+\/\d+\)$/.test(line));
  const passed = tests.filter(line => line.startsWith('PASS ')).length;
  // The split must cover every check exactly once: as many as the page declares (counted in its source), each
  // name run on one page only, and no page without a check.
  const declared = (readFileSync(work + '/view-test.html', 'utf8').match(/await test\(/g) || []).length;
  const ran = new Map();
  pages.forEach(page => page.checks.ran.forEach(name => ran.set(name, (ran.get(name) || 0) + 1)));
  const split = [];
  pages.forEach((page, shard) => { if (!page.checks.ran.length) split.push('SPLIT page ' + (shard + 1) + ' of ' + SHARDS + ' ran no check'); });
  ran.forEach((count, name) => { if (count > 1) split.push('SPLIT doubled (' + count + ' times): ' + name); });
  const named = new Set(pages.flatMap(page => page.checks.declared));
  named.forEach(name => { if (!ran.has(name)) split.push('SPLIT missing: ' + name); });
  const total = [...ran.values()].reduce((sum, count) => sum + count, 0);
  if (total !== declared || tests.length !== declared) split.push('SPLIT ' + total + ' checks run and ' + tests.length + ' reported, but the page declares ' + declared);
  const ok = !broken.length && !split.length && passed === tests.length && tests.length > 0;
  console.log([...tests, ...broken, ...split, 'RESULT: ' + (ok ? 'PASS' : 'FAIL') + ' (' + passed + '/' + declared + ')'].join('\n'));
  process.exitCode = ok ? 0 : 1;
} catch (error) {
  console.error(error); process.exitCode = 1;
} finally {
  // Exit explicitly: a page that failed may leave a socket or a request open that would keep node running.
  clearTimeout(timeout); browsers.forEach(browser => browser.kill());
  process.exit(process.exitCode ?? 1);
}
