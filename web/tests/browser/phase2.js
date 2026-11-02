// Phase 2 browser click-through: a full simulated print.
// Usage: node phase2.js <scratch dir>   (expects <scratch>/cube.gcode, see web/tests/sample_gcode.py)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const S = process.argv[2], repo = '/home/claude/printrun';
const env = { ...process.env, HOME: S + '/home11', PYTHONUNBUFFERED: '1', BROWSER: 'true' };
function waitFor(proc, re) {
  return new Promise((res, rej) => {
    let buf = ''; const t = setTimeout(() => rej(new Error('timeout ' + re + '\n' + buf.slice(-1500))), 30000);
    const on = (d) => { buf += d; const m = buf.match(re); if (m) { clearTimeout(t); res(m); } };
    proc.stdout.on('data', on); proc.stderr.on('data', on);
  });
}
const text = (page, sel) => page.textContent(sel);
const until = (page, fn, arg, timeout = 15000) => page.waitForFunction(fn, arg, { timeout });
(async () => {
  const results = [];
  const check = (name, ok) => results.push((ok ? 'PASS ' : 'FAIL ') + name);
  const fp = spawn('/usr/bin/python3.12', ['-u', '-m', 'layerline.fake_printer', '5603', '0.05'], { cwd: repo + '/web', env });
  await waitFor(fp, /listening on/);
  const app = spawn('/usr/bin/python3.12', ['-u', 'start_layerline.py', '--browser', '--port', '8103'], { cwd: repo, env });
  const [, url] = await waitFor(app, /running at (http\S+)/);
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1360, height: 900 } })).newPage();
    const errors = []; page.on('pageerror', e => errors.push(String(e)));
    await page.goto(url);
    await page.waitForSelector('#port option', { state: 'attached' });
    check('Print disabled with no file', await page.isDisabled('#print-btn'));
    await page.selectOption('#port', 'other');
    await page.fill('#custom-port', '127.0.0.1:5603');
    await page.click('#connect');
    await until(page, () => document.body.classList.contains('online'));

    // choose a file (browser mode = upload)
    await page.setInputFiles('#file-input', S + '/cube.gcode');
    await until(page, () => document.querySelector('#file-name').textContent === 'cube.gcode');
    const info = await text(page, '#file-info');
    check('file info shows time, filament, layers, height', /About .*filament.*30 layers.*6 mm tall/.test(info));
    check('status says Ready to print', (await text(page, '#s-state')) === 'Ready to print');
    await page.screenshot({ path: S + '/shots/p2-1-loaded.png' });

    // print
    await page.click('#print-btn');
    await until(page, () => parseInt(document.querySelector('#s-progress').textContent) >= 15, null, 30000);
    check('strip shows Printing with progress', (await text(page, '#s-state')) === 'Printing');
    check('time left shown', !/—/.test(await text(page, '#s-left')));
    check('height shown', /mm$/.test(await text(page, '#s-height')));
    check('Pause and Cancel shown, Print hidden', await page.isVisible('#pause-btn') && await page.isVisible('#cancel-btn') && await page.isHidden('#print-btn'));
    check('file can\'t be changed while printing', await page.isDisabled('#choose-file'));
    await page.screenshot({ path: S + '/shots/p2-2-printing.png' });

    // pause / resume
    await page.click('#pause-btn');
    await until(page, () => document.querySelector('#s-state').textContent === 'Paused');
    check('paused: Resume + Restart + Cancel', (await text(page, '#print-btn')) === 'Resume' && await page.isVisible('#restart-btn'));
    const pausedAt = await text(page, '#s-progress');
    await page.waitForTimeout(1500);
    check('progress holds while paused', (await text(page, '#s-progress')) === pausedAt);
    await page.screenshot({ path: S + '/shots/p2-3-paused.png' });
    await page.click('#print-btn');
    await until(page, () => document.querySelector('#s-state').textContent === 'Printing');
    check('resume continues', true);

    // cancel (asks first)
    await page.click('#cancel-btn');
    await page.waitForSelector('#confirm-sheet:not([hidden])');
    check('cancel asks first ("Keep printing" / "Cancel print")', (await text(page, '#confirm-cancel')) === 'Keep printing');
    await page.click('#confirm-ok');
    await until(page, () => document.querySelector('#s-state').textContent === 'Ready to print');
    check('after cancel the file is still loaded', (await text(page, '#file-name')) === 'cube.gcode');
    check('heaters untouched by cancel', !(await text(page, '#log')).includes('temperature to 0'));

    // recent files
    const opts = await page.$$eval('#recent option', os => os.map(o => o.textContent));
    check('recent files lists the file', opts.includes('cube.gcode'));

    // interrupted print + recovery
    await page.click('#print-btn');
    await until(page, () => parseInt(document.querySelector('#s-progress').textContent) >= 20, null, 30000);
    await page.click('#connect');              // Disconnect mid-print -> asks
    await page.waitForSelector('#confirm-sheet:not([hidden])');
    await page.click('#confirm-ok');
    await until(page, () => !document.querySelector('#recover').hidden);
    check('recovery card appears after a disconnect', /stopped at \d+%/.test(await text(page, '#recover-text')));
    check('Resume print disabled until reconnected', await page.isDisabled('#recover-btn'));
    await page.screenshot({ path: S + '/shots/p2-4-recover.png' });
    await page.click('#connect');
    await until(page, () => document.body.classList.contains('online'));
    await page.click('#recover-btn');
    await until(page, () => document.querySelector('#s-state').textContent === 'Printing');
    check('resume after disconnect continues printing', parseInt(await text(page, '#s-progress')) >= 15);
    check('recovery card goes away', await page.isHidden('#recover'));

    // dark + phone while printing
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.waitForTimeout(500);
    await page.screenshot({ path: S + '/shots/p2-5-dark.png' });
    const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })).newPage();
    await phone.goto(url); await phone.waitForTimeout(1500);
    await phone.screenshot({ path: S + '/shots/p2-6-phone.png' });

    // let it finish
    await until(page, () => document.querySelector('#s-state').textContent === 'Ready to print', null, 120000);
    check('print finishes', (await text(page, '#log')).includes('Print finished.'));
    check('no page errors', errors.length === 0);
    if (errors.length) results.push(errors.join('\n'));
  } catch (e) {
    results.push('FAIL crashed: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close(); app.kill(); fp.kill();
    console.log(results.join('\n'));
  }
})();
