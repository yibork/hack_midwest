// Read-only visual check in a fresh profile. No API writes, clicks, stored-session access or LLMs.
import {spawn} from 'node:child_process';
import {mkdtemp, mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';

const [base = 'http://127.0.0.1:5174', phase = 'after'] = process.argv.slice(2);
const directory = fileURLToPath(new URL('../../docs/screenshots/task-b/', import.meta.url));
await mkdir(directory, {recursive: true});
const profile = await mkdtemp('/private/tmp/closurecall-capture-');
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
   `--user-data-dir=${profile}`, 'about:blank'], {stdio: ['ignore', 'ignore', 'pipe']});
const endpoint = await new Promise((resolve, reject) => {
  let text = '';
  const timer = setTimeout(() => reject(new Error('Browser startup timed out')), 15000);
  chrome.stderr.on('data', chunk => {
    text += chunk;
    const match = text.match(/DevTools listening on (ws:\/\/[^\s]+)/);
    if (match) {clearTimeout(timer); resolve(new URL(match[1]));}
  });
  chrome.on('exit', code => {clearTimeout(timer); reject(new Error(`Browser exited: ${code}`));});
});
try {
  const targets = await (await fetch(`http://${endpoint.host}/json/list`)).json();
  const socket = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, {once: true}));
  let sequence = 0;
  let loaded;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Page.loadEventFired') loaded?.();
    if (pending.has(message.id)) {const {resolve, reject} = pending.get(message.id); pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message)); else resolve(message.result);}
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence; pending.set(id, {resolve, reject}); socket.send(JSON.stringify({id, method, params}));
  });
  await command('Page.enable');
  const cases = [
    ['facility', '/facility', 1280, 720, 'Normal operations'],
    ['decide', '/decide?recommendation=9b1a7f2b15ad4852a1ac78b118189db6', 1280, 720, 'Manager name'],
    ...(phase === 'after' ? [['facility-phone', '/facility', 390, 844, 'Normal operations'],
      ['decide-phone', '/decide?recommendation=9b1a7f2b15ad4852a1ac78b118189db6', 390, 844, 'Manager name']] : []),
    ['my-status', '/me?as=emp-033', 390, 844, 'Hello,'],
    ...(phase === 'after' ? [['my-status-desktop', '/me?as=emp-033', 1280, 720, 'Hello,']] : []),
  ];
  for (const [name, path, width, height, ready] of cases) {
    await command('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width === 390});
    const pageLoaded = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`${name}: page load timed out`)), 20000);
      loaded = () => {clearTimeout(timer); loaded = null; resolve();};
    });
    await command('Page.navigate', {url: base + path});
    await pageLoaded;
    const deadline = Date.now() + 20000;
    let settled = false;
    while (Date.now() < deadline) {
      const state = await command('Runtime.evaluate', {expression: `document.body?.innerText.includes(${JSON.stringify(phase === 'before' && ready === 'Normal operations' ? 'NORMAL' : ready)})`, returnByValue: true});
      if (state.result.value) {settled = true; break;}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (!settled) throw new Error(`${name}: expected page did not load`);
    await command('Runtime.evaluate', {expression: 'document.fonts.ready', awaitPromise: true});
    await new Promise(resolve => setTimeout(resolve, 500));
    const measurements = await command('Runtime.evaluate', {expression: '({width: innerWidth, content: document.documentElement.scrollWidth})', returnByValue: true});
    const screenshot = await command('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
    await writeFile(join(directory, `${name}-${phase}.png`), Buffer.from(screenshot.data, 'base64'));
    console.log(`${name} ${phase}: ${width}×${height}; viewport ${measurements.result.value.width}, content ${measurements.result.value.content}`);
    if (phase === 'after' && measurements.result.value.content > width + 1) {
      const overflow = await command('Runtime.evaluate', {expression: `Array.from(document.querySelectorAll('body *')).filter(e => e.getBoundingClientRect().right > ${width} + 1 && !e.children.length).map(e => ({tag: e.tagName, classes: e.className, text: e.textContent.slice(0, 100), right: e.getBoundingClientRect().right})).slice(0, 8)`, returnByValue: true});
      console.log('Overflowing visible elements:', JSON.stringify(overflow.result.value));
      throw new Error(`${name}: horizontal page overflow`);
    }
  }
  socket.close();
} finally {
  chrome.kill('SIGTERM'); // only the browser this script started; keep its temporary profile recoverable
}
