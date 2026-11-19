import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const [chrome, work] = process.argv.slice(2);
const browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--window-size=1400,900', '--remote-debugging-port=0', '--user-data-dir=' + work + '/profile', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let socket;
const timeout = setTimeout(() => { console.error('View checks timed out'); browser.kill(); process.exit(1); }, 45000);
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
  await call('Page.navigate', { url: pathToFileURL(work + '/view-test.html').href });
  await loaded;
  const result = await call('Runtime.evaluate', {
    expression: `new Promise(resolve => { const timer = setInterval(() => { const value = document.getElementById('results')?.textContent; if (value && !value.includes('RESULT: RUNNING')) { clearInterval(timer); resolve(value); } }, 30); })`,
    awaitPromise: true, returnByValue: true
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  const output = result.result.value;
  console.log(output);
  process.exitCode = output.includes('RESULT: PASS') && !output.includes('FAIL ') ? 0 : 1;
} catch (error) {
  console.error(error); process.exitCode = 1;
} finally {
  clearTimeout(timeout); socket?.close(); browser.kill();
}
