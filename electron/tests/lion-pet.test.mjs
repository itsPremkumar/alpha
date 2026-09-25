import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const read = (name) => fs.readFileSync(new URL(`../${name}`, import.meta.url), 'utf8');
const main = read('main.js');
const preload = read('preload.js');
const petPreload = read('pet-preload.js');
const pet = read('pet.html');
const builder = read('electron-builder.yml');

test('the desktop shell wires a sanitized lion state channel', () => {
  assert.match(main, /LION_PET_STATES/);
  assert.match(main, /sanitizeLionPetState/);
  assert.match(main, /alpha:lion-pet-state/);
  assert.match(main, /alpha:lion-pet-visible/);
  assert.match(main, /alpha:lion-pet-visibility/);
  assert.match(main, /transparent: true/);
  assert.match(main, /alwaysOnTop: true/);
  assert.match(main, /skipTaskbar: true/);
  assert.match(main, /contextIsolation: true/);
  assert.match(main, /nodeIntegration: false/);
  assert.match(main, /pet\.html/);
});

test('the preload bridge exposes only bounded companion controls', () => {
  assert.match(preload, /reportLionPetState/);
  assert.match(preload, /setLionPetVisible/);
  assert.match(preload, /no Node\.js access is leaked/);
  assert.match(main, /pet-preload\.js/);
  assert.match(petPreload, /exposeInMainWorld\('alphaPet'/);
  assert.match(petPreload, /alpha:lion-pet-state/);
  assert.doesNotMatch(petPreload, /setAutoStart|openUserData|getStatus/);
});

test('the native companion is local, draggable, and returnable', () => {
  assert.match(pet, /-webkit-app-region: drag/);
  assert.match(pet, /-webkit-app-region: no-drag/);
  assert.match(pet, /alphaPet/);
  assert.match(pet, /onState/);
  assert.match(pet, /prefers-reduced-motion/);
  assert.doesNotMatch(pet, /https?:\/\//);
});

test('the packaged desktop build includes the native companion page', () => {
  assert.match(builder, /- pet\.html/);
  assert.match(builder, /- pet-preload\.js/);
});
