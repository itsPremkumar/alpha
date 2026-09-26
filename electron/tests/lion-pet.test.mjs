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
const require = createRequire(import.meta.url);
const { getLionPetMotionPlan } = require('../lib/lion-pet-window.js');

test('the desktop shell wires a sanitized lion state channel', () => {
  assert.match(main, /LionPetWindow/);
  assert.match(main, /lionPetController/);
  assert.match(controller, /LION_PET_STATES/);
  assert.match(controller, /LION_PET_ACTIONS/);
  assert.match(controller, /LION_PET_SKINS/);
  assert.match(controller, /sanitizeLionPetState/);
  assert.match(main, /alpha:lion-pet-state/);
  assert.match(main, /alpha:lion-pet-visible/);
  assert.match(main, /alpha:lion-pet-visibility/);
  assert.match(main, /alpha:lion-pet-perform/);
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
