const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

const urls = [
  // try multiple hostnames for the frontend (localhost, loopback, LAN IP)
  {name: 'frontend-localhost', url: 'http://localhost:3000'},
  {name: 'frontend-127', url: 'http://127.0.0.1:3000'},
  {name: 'frontend-lan', url: 'http://192.168.1.34:3000'},
  {name: 'gateway', url: 'http://localhost:8001'},
  {name: 'proxy', url: 'http://localhost:2026'}
];

(async () => {
  if (!fs.existsSync(path.join(__dirname, '..', 'screenshots'))) fs.mkdirSync(path.join(__dirname, '..', 'screenshots'));
  const browser = await puppeteer.launch({args: ['--no-sandbox','--disable-setuid-sandbox']});
  const page = await browser.newPage();
  for (const u of urls) {
    try {
      // allow longer timeout for slow dev server
      await page.goto(u.url, {waitUntil: 'networkidle2', timeout: 60000});
      await page.screenshot({path: path.join(__dirname, '..', 'screenshots', `${u.name}.png`), fullPage: true});
      const html = await page.content();
      fs.writeFileSync(path.join(__dirname, '..', 'screenshots', `${u.name}.html`), html);
      console.log('OK', u.url);
    } catch (err) {
      console.error('ERR', u.url, err.message);
    }
  }
  await browser.close();
})();
