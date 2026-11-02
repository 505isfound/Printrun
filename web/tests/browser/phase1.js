const { chromium } = require('playwright');
const { spawn } = require('child_process');
const S = process.argv[2], repo = '/home/claude/printrun';
const env = { ...process.env, HOME: S + '/home6', PYTHONUNBUFFERED: '1', BROWSER: 'true' };
function waitFor(proc, re) {
  return new Promise((res, rej) => {
    let buf = ''; const t = setTimeout(() => rej(new Error('timeout ' + re + '\n' + buf.slice(-2000))), 30000);
    const on = (d) => { buf += d; const m = buf.match(re); if (m) { clearTimeout(t); res(m); } };
    proc.stdout.on('data', on); proc.stderr.on('data', on);
  });
}
const logHas = (page, text) => page.waitForFunction((t) => document.querySelector('#log').textContent.includes(t), text, { timeout: 10000 });
(async () => {
  const results = [];
  const check = (name, ok) => results.push((ok ? 'PASS ' : 'FAIL ') + name);
  const fp = spawn('/usr/bin/python3.12', ['-u', '-m', 'layerline.fake_printer', '5601'], { cwd: repo + '/web', env });
  await waitFor(fp, /listening on/);
  const app = spawn('/usr/bin/python3.12', ['-u', 'start_layerline.py', '--browser', '--port', '8101'], { cwd: repo, env });
  const [, url] = await waitFor(app, /running at (http\S+)/);
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
    const page = await ctx.newPage();
    const errors = []; page.on('pageerror', e => errors.push(String(e)));
    await page.goto(url);
    await page.waitForSelector('#port option', { state: 'attached' });
    check('safety buttons disabled before connecting', await page.isDisabled('#heaters-off'));
    await page.screenshot({ path: S + '/shots/p1-0-offline.png' });

    await page.selectOption('#port', 'other');
    await page.fill('#custom-port', '127.0.0.1:5601');
    await page.click('#connect');
    await page.waitForFunction(() => document.body.classList.contains('online'), null, { timeout: 15000 });
    check('connection rows collapse once connected', await page.isHidden('#port'));

    // set nozzle by typing
    await page.fill('#nozzle-target', '200');
    await page.click('form[data-heater="nozzle"] button[type="submit"]');
    await logHas(page, 'Setting hotend temperature to 200');
    check('set nozzle temperature', true);
    // preheat PETG
    await page.click('.preset:has-text("PETG")');
    await logHas(page, 'Preheating for PETG');
    await logHas(page, 'Setting bed temperature to 75');
    check('preheat PETG sets nozzle and bed', true);
    // high temp asks first, cancel sends nothing
    await page.fill('#nozzle-target', '265');
    await page.click('form[data-heater="nozzle"] button[type="submit"]');
    await page.waitForSelector('#confirm-sheet:not([hidden])');
    check('asks before heating above 250°', (await page.textContent('#confirm-title')).includes('265'));
    await page.click('#confirm-cancel');
    await page.waitForTimeout(800);
    check('cancel sends nothing', !(await page.textContent('#log')).includes('temperature to 265'));

    // temps + strip
    await page.waitForFunction(() => parseInt(document.querySelector('#s-nozzle').textContent) > 100, null, { timeout: 25000 });
    check('status strip shows live nozzle temp', (await page.textContent('#s-nozzle-t')).includes('235'));
    check('status strip state says Idle', (await page.textContent('#s-state')) === 'Idle');
    // graph drawn
    const inked = await page.evaluate(() => {
      const c = document.querySelector('#graph-small'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n;
    });
    check('small graph draws lines', inked > 2000);
    await page.screenshot({ path: S + '/shots/p1-1-heating.png' });

    // expanded graph
    await page.click('#graph-open');
    await page.waitForSelector('#graph-sheet:not([hidden])');
    await page.click('#range button[data-sec="60"]');
    await page.waitForTimeout(400);
    await page.screenshot({ path: S + '/shots/p1-2-graph.png' });
    await page.keyboard.press('Escape');
    check('expanded graph opens and closes', await page.isHidden('#graph-sheet'));

    // fan via console shows in graph data
    await page.fill('#cmd', 'M106 S255'); await page.press('#cmd', 'Enter');

    // settings: gauges + lock
    await page.click('#settings-btn');
    await page.waitForSelector('#settings-sheet:not([hidden])');
    await page.screenshot({ path: S + '/shots/p1-3-settings.png' });
    await page.click('[data-setting="tempgauges"]');
    await page.click('[data-setting="lockbox"]');
    await page.waitForFunction(() => !document.querySelector('#lock').hidden, null, { timeout: 5000 });
    check('settings toggle shows lock button', true);
    await page.click('#settings-close');
    check('gauges appear', await page.isVisible('#g-nozzle'));
    await page.click('#lock');
    await page.waitForFunction(() => document.body.classList.contains('locked'));
    const blocked = await page.evaluate(() => getComputedStyle(document.querySelector('.safety').closest('.lockable')).pointerEvents);
    check('lock disables controls', blocked === 'none');
    await page.screenshot({ path: S + '/shots/p1-4-locked.png' });
    await page.click('#lock');
    await page.waitForFunction(() => !document.body.classList.contains('locked'));

    // safety
    await page.click('#heaters-off');
    await logHas(page, 'Heaters off.');
    check('heaters off', true);
    await page.click('#everything-off');
    await page.waitForSelector('#confirm-sheet:not([hidden])');
    await page.click('#confirm-ok');
    await logHas(page, 'Power supply off');
    check('turn everything off (asks first)', true);

    // dark + phone
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(500);
    await page.screenshot({ path: S + '/shots/p1-5-dark.png' });
    await page.click('#graph-open'); await page.waitForTimeout(400);
    await page.screenshot({ path: S + '/shots/p1-6-graph-dark.png' });
    await page.keyboard.press('Escape');
    const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })).newPage();
    await phone.goto(url); await phone.waitForTimeout(1800);
    await phone.screenshot({ path: S + '/shots/p1-7-phone.png', fullPage: true });
    check('no page errors', errors.length === 0);
    if (errors.length) results.push(errors.join('\n'));
  } catch (e) {
    results.push('FAIL crashed: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close(); app.kill(); fp.kill();
    console.log(results.join('\n'));
  }
})();
