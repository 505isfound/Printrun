const { chromium } = require('playwright');
const { spawn } = require('child_process');
const S = process.argv[2], repo = '/home/claude/printrun';
const env = { ...process.env, HOME: S + '/home2', PYTHONUNBUFFERED: '1', BROWSER: 'true' };
function waitFor(proc, re) {
  return new Promise((res, rej) => {
    let buf = ''; const t = setTimeout(() => rej(new Error('timeout waiting for ' + re + '\n' + buf)), 30000);
    const on = (d) => { buf += d; const m = buf.match(re); if (m) { clearTimeout(t); res(m); } };
    proc.stdout.on('data', on); proc.stderr.on('data', on);
  });
}
(async () => {
  const results = [];
  const check = (name, ok) => { results.push((ok ? 'PASS ' : 'FAIL ') + name); };
  const fp = spawn('/usr/bin/python3.12', ['-u', '-m', 'layerline.fake_printer', '5599'], { cwd: repo + '/web', env });
  await waitFor(fp, /listening on (\S+)/);
  const app = spawn('/usr/bin/python3.12', ['-u', 'start_layerline.py', '--browser', '--port', '8099'], { cwd: repo, env });
  const [, url] = await waitFor(app, /running at (http\S+)/);
  const browser = await chromium.launch();
  try {
    // security: no key -> refused
    const anon = await (await browser.newContext()).newPage();
    const r = await anon.goto(url.split('?')[0]);
    check('page refused without access key', r.status() === 403);

    const ctx = await browser.newContext({ viewport: { width: 1280, height: 820 } });
    const page = await ctx.newPage();
    const errors = []; page.on('pageerror', e => errors.push(String(e)));
    await page.goto(url);
    await page.waitForSelector('#port option', { state: 'attached' });
    check('address bar no longer shows the key', !page.url().includes('key='));
    await page.screenshot({ path: S + '/shots/1-start.png' });

    // websocket from a different site is refused
    const evil = await (await browser.newContext()).newPage();
    await evil.goto('about:blank');
    const evilResult = await evil.evaluate((port) => new Promise(res => {
      const ws = new WebSocket(`ws://127.0.0.1:${port}/ws`);
      ws.onopen = () => res('opened'); ws.onerror = () => res('refused'); setTimeout(() => res('timeout'), 3000);
    }), 8099);
    check('other websites cannot connect', evilResult !== 'opened');

    // connect via "Other" address
    await page.selectOption('#port', 'other');
    await page.fill('#custom-port', '127.0.0.1:5599');
    await page.click('#connect');
    await page.waitForFunction(() => document.querySelector('#conn').dataset.state === 'online', null, { timeout: 15000 });
    check('connects and shows Connected', (await page.textContent('#conn-text')).includes('Connected to 127.0.0.1:5599'));
    check('connect button turns into Disconnect', (await page.textContent('#connect')) === 'Disconnect');

    // console
    await page.fill('#cmd', 'M115'); await page.press('#cmd', 'Enter');
    await page.waitForFunction(() => document.querySelector('#log').textContent.includes('MACHINE_TYPE:Ender-5 Pro'), null, { timeout: 8000 });
    check('console command reaches printer and reply shows', true);
    await page.fill('#cmd', 'M104 S200'); await page.press('#cmd', 'Enter');
    await page.fill('#cmd', 'M140 S60'); await page.press('#cmd', 'Enter');
    await page.fill('#cmd', 'help'); await page.press('#cmd', 'Enter');
    await page.waitForFunction(() => document.querySelector('#log').textContent.includes('Documented commands'), null, { timeout: 8000 });
    check('Pronterface commands work (help)', true);

    // history
    await page.press('#cmd', 'ArrowUp');
    check('up arrow recalls last command', (await page.inputValue('#cmd')) === 'help');
    await page.fill('#cmd', '');

    // temps update live
    await page.waitForFunction(() => {
      const n = parseInt(document.querySelector('#s-nozzle').textContent); return n > 120;
    }, null, { timeout: 25000 });
    check('nozzle temperature updates live', true);
    check('heating status shown', (await page.textContent('#nozzle-now')).includes('heating') && (await page.textContent('#s-nozzle-t')).includes('200'));
    await page.screenshot({ path: S + '/shots/2-connected.png' });

    // debug traffic toggle
    await page.check('#debug');
    await page.waitForFunction(() => document.querySelector('#log').textContent.includes('RECV:'), null, { timeout: 10000 });
    check('"All printer traffic" shows raw lines', true);
    await page.uncheck('#debug');

    // dark mode
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(400);
    await page.screenshot({ path: S + '/shots/3-dark.png' });

    // narrow / phone
    const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })).newPage();
    await phone.goto(url); await phone.waitForTimeout(1500);
    await phone.screenshot({ path: S + '/shots/4-phone.png', fullPage: true });

    // disconnect leaves heaters alone
    await page.click('#connect');
    await page.waitForFunction(() => document.querySelector('#conn').dataset.state === 'offline', null, { timeout: 8000 });
    check('disconnects', true);
    check('no page errors', errors.length === 0);
    if (errors.length) results.push(errors.join('\n'));
  } catch (e) {
    results.push('FAIL crashed: ' + e.message);
  } finally {
    await browser.close(); app.kill(); fp.kill();
    console.log(results.join('\n'));
  }
})();
