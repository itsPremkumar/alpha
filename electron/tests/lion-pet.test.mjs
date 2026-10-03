import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const read = (name) => fs.readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const main = read('main.js');
const controller = read('lib/lion-pet-window.js');
const preload = read('preload.js');
const petPreload = read('pet-preload.js');
const pet = read('pet.html');
const builder = read('electron-builder.yml');
const routes = read('lib/ipc-routes.js');
const require = createRequire(import.meta.url);
const { getLionPetMotionPlan } = require('../lib/lion-pet-window.js');
const { CHANNELS, EVENTS, ROUTES } = require('../lib/ipc-routes.js');

test('the desktop shell wires a sanitized lion state channel', () => {
  assert.match(main, /LionPetWindow/);
  assert.match(main, /lionPetController/);
  assert.match(controller, /LION_PET_STATES/);
  assert.match(controller, /LION_PET_ACTIONS/);
  assert.match(controller, /LION_PET_SKINS/);
  assert.match(controller, /sanitizeLionPetState/);
  assert.match(controller, /MOTION_RULES/);
  assert.match(controller, /setPosition/);
  assert.match(controller, /transparent: true/);
  assert.match(controller, /alwaysOnTop: true/);
  assert.match(controller, /skipTaskbar: true/);
  assert.match(controller, /contextIsolation: true/);
  assert.match(controller, /nodeIntegration: false/);
  assert.match(controller, /pet\.html/);
  assert.match(main, /--show-lion-pet/);
  assert.match(main, /args\.showLionPet/);
});

test('the companion channel names live in the one route table', () => {
  // The channel names moved out of main.js and into lib/ipc-routes.js, because
  // `ipcMain.handle` throws on a duplicate and main.js had four channels
  // registered twice — which exited the app at module load with no window and no
  // log line. One table, one registration loop, and a test that can see it.
  assert.equal(CHANNELS.lionPetState, 'alpha:lion-pet-state');
  assert.equal(CHANNELS.lionPetPerform, 'alpha:lion-pet-perform');
  assert.equal(CHANNELS.lionPetVisible, 'alpha:lion-pet-visible');
  assert.equal(EVENTS.lionPetVisibility, 'alpha:lion-pet-visibility');

  // Each is registered exactly once, and the state channel is the fire-and-
  // forget one while the other two are awaited.
  const routeByChannel = new Map(ROUTES.map((route) => [route.channel, route]));
  assert.equal(routeByChannel.get('alpha:lion-pet-state').kind, 'on');
  assert.equal(routeByChannel.get('alpha:lion-pet-perform').kind, 'invoke');
  assert.equal(routeByChannel.get('alpha:lion-pet-visible').kind, 'invoke');

  // main.js registers from the table and never names a channel literally, so a
  // second spelling of one cannot appear.
  assert.match(main, /require\('\.\/lib\/ipc-routes'\)/);
  for (const channel of ['alpha:lion-pet-state', 'alpha:lion-pet-visible', 'alpha:lion-pet-perform']) {
    assert.doesNotMatch(main, new RegExp(`ipcMain\\.\\w+\\('${channel}'`));
  }
});

test('the preload bridge exposes only bounded companion controls', () => {
  assert.match(preload, /reportLionPetState/);
  assert.match(preload, /setLionPetVisible/);
  assert.match(preload, /no Node\.js access is leaked/);
  assert.match(controller, /pet-preload\.js/);
  assert.match(petPreload, /exposeInMainWorld\('alphaPet'/);
  assert.match(petPreload, /alpha:lion-pet-state/);
  assert.match(petPreload, /performAction/);
  assert.match(petPreload, /alpha:lion-pet-perform/);
  assert.doesNotMatch(petPreload, /setAutoStart|openUserData|getStatus/);
});

test('the native companion is local, draggable, and returnable', () => {
  assert.match(pet, /-webkit-app-region: drag/);
  assert.match(pet, /-webkit-app-region: no-drag/);
  assert.match(pet, /alphaPet/);
  assert.match(pet, /onState/);
  assert.match(pet, /prefers-reduced-motion/);
  assert.match(pet, /data-action/);
  assert.match(pet, /data-skin/);
  assert.match(pet, /id="perform"/);
  assert.match(pet, /id="walk"/);
  assert.match(pet, /id="run"/);
  assert.match(pet, /id="skin"/);
  assert.match(pet, /actionCycle/);
  assert.match(pet, /skinCycle/);
  assert.match(pet, /keyframes walk/);
  assert.match(pet, /keyframes run/);
  assert.match(pet, /keyframes jump/);
  assert.match(pet, /leg-step-a/);
  assert.match(pet, /performAction/);
  assert.match(pet, /"prowl"/);
  assert.match(pet, /"hunt"/);
  assert.doesNotMatch(pet, /https?:\/\//);
});

test('walk and run motion stays inside the native work area', () => {
  const workArea = { x: 0, y: 0, width: 1000, height: 800 };
  assert.deepEqual(
    getLionPetMotionPlan({ x: 800, y: 500, width: 230, height: 285 }, workArea, 'run'),
    { direction: -1, distance: 280, duration: 1200 },
  );
  assert.deepEqual(
    getLionPetMotionPlan({ x: 0, y: 500, width: 230, height: 285 }, workArea, 'walk'),
    { direction: 1, distance: 150, duration: 1500 },
  );
  assert.equal(
    getLionPetMotionPlan({ x: 12, y: 500, width: 230, height: 285 }, { x: 0, y: 0, width: 254, height: 800 }, 'walk'),
    null,
  );
});

test('the packaged desktop build includes the native companion page', () => {
  assert.match(builder, /- pet\.html/);
  assert.match(builder, /- pet-preload\.js/);
});
