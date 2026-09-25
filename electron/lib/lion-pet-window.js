'use strict';

const path = require('node:path');
const { BrowserWindow, screen } = require('electron');

const STATES = new Set([
  'idle',
  'thinking',
  'working',
  'waiting',
  'success',
  'error',
  'sleeping',
]);
const ACTIONS = new Set([
  'idle',
  'walk',
  'run',
  'jump',
  'roar',
  'pounce',
  'play',
  'sleep',
  'stretch',
  'prowl',
  'hunt',
  'shake',
  'spin',
]);
const SKINS = new Set(['golden', 'midnight', 'ember', 'frost', 'royal', 'moss']);

const MOTION_RULES = Object.freeze({
  walk: Object.freeze({ distance: 150, duration: 1500 }),
  run: Object.freeze({ distance: 280, duration: 1200 }),
  prowl: Object.freeze({ distance: 90, duration: 2200 }),
  hunt: Object.freeze({ distance: 55, duration: 1900 }),
});
const MOTION_TICK_MS = 16;
const MOTION_EDGE_MARGIN = 12;

const DEFAULT_MESSAGE = "The desk is quiet. I'm here when you need me.";

function getMotionPlan(bounds, workArea, action) {
  const rule = MOTION_RULES[action];
  if (!rule || !bounds || !workArea) return null;
  const leftSpace = Math.max(0, bounds.x - workArea.x - MOTION_EDGE_MARGIN);
  const rightSpace = Math.max(0, workArea.x + workArea.width - (bounds.x + bounds.width) - MOTION_EDGE_MARGIN);
  let direction = rightSpace >= leftSpace ? 1 : -1;
  if (direction > 0 && rightSpace < rule.distance && leftSpace > rightSpace) direction = -1;
  if (direction < 0 && leftSpace < rule.distance && rightSpace > leftSpace) direction = 1;
  const availableSpace = direction > 0 ? rightSpace : leftSpace;
  const distance = Math.min(rule.distance, availableSpace);
  if (distance < 8) return null;
  return { direction, distance, duration: rule.duration };
}

function sanitize(payload, previousMessage = DEFAULT_MESSAGE) {
  const candidate = payload && typeof payload === 'object' ? payload : {};
  const message = typeof candidate.message === 'string'
    ? candidate.message.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160)
    : previousMessage;
  return {
    state: STATES.has(candidate.state) ? candidate.state : 'idle',
    action: ACTIONS.has(candidate.action) ? candidate.action : 'idle',
    skin: SKINS.has(candidate.skin) ? candidate.skin : 'golden',
    message: message || DEFAULT_MESSAGE,
    visible: candidate.visible !== false,
  };
}

class LionPetWindow {
  constructor({ log, onVisibilityChange } = {}) {
    this.log = typeof log === 'function' ? log : () => {};
    this.onVisibilityChange = typeof onVisibilityChange === 'function' ? onVisibilityChange : () => {};
    this.mainWindow = null;
    this.window = null;
    this.motionTimer = null;
    this.state = sanitize({ state: 'idle', action: 'idle', skin: 'golden', message: DEFAULT_MESSAGE });
  }

  setMainWindow(window) {
    this.mainWindow = window || null;
  }

  isTrustedSender(sender) {
    return Boolean(
      (this.mainWindow && !this.mainWindow.isDestroyed() && sender === this.mainWindow.webContents)
      || (this.window && !this.window.isDestroyed() && sender === this.window.webContents),
    );
  }

  sendState() {
    if (!this.window || this.window.isDestroyed()) return;
    try {
      this.window.webContents.send('alpha:lion-pet-state', this.state);
    } catch {
      // The companion window may be closing; its visual state is not durable state.
    }
  }

  stopMotion() {
    if (this.motionTimer) {
      clearInterval(this.motionTimer);
      this.motionTimer = null;
    }
  }

  startMotion(action) {
    this.stopMotion();
    if (!this.window || this.window.isDestroyed() || !MOTION_RULES[action]) return false;
    let bounds;
    let workArea;
    try {
      bounds = this.window.getBounds();
      workArea = screen.getDisplayMatching(bounds).workArea;
    } catch {
      return false;
    }
    const plan = getMotionPlan(bounds, workArea, action);
    if (!plan) return false;
    const startedAt = Date.now();
    this.motionTimer = setInterval(() => {
      if (!this.window || this.window.isDestroyed()) {
        this.stopMotion();
        return;
      }
      const progress = Math.min(1, Math.max(0, (Date.now() - startedAt) / plan.duration));
      const eased = progress < 0.5
        ? 2 * progress * progress
        : 1 - ((-2 * progress + 2) ** 2) / 2;
      const nextX = Math.round(bounds.x + plan.distance * plan.direction * eased);
      try {
        this.window.setPosition(nextX, bounds.y, false);
      } catch {
        this.stopMotion();
        return;
      }
      if (progress >= 1) this.stopMotion();
    }, MOTION_TICK_MS);
    return true;
  }

  create() {
    if (this.window && !this.window.isDestroyed()) {
      try {
        this.window.show();
        this.sendState();
      } catch {
        // The renderer may still be loading; ready-to-show will show it.
      }
      return this.window;
    }

    const workArea = screen.getPrimaryDisplay().workArea;
    const width = 230;
    const height = 285;
    this.window = new BrowserWindow({
      width,
      height,
      x: Math.max(workArea.x, workArea.x + workArea.width - width - 18),
      y: Math.max(workArea.y, workArea.y + workArea.height - height - 18),
      frame: false,
      transparent: true,
      backgroundColor: '#00000000',
      hasShadow: false,
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      focusable: false,
      show: false,
      webPreferences: {
        preload: path.resolve(__dirname, '..', 'pet-preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    this.window.setAlwaysOnTop(true, 'floating');
    try {
      this.window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    } catch {
      // Some shells expose only the primary always-on-top behavior.
    }
    this.window.loadFile(path.resolve(__dirname, '..', 'pet.html'));
    this.window.once('ready-to-show', () => {
      if (this.window && !this.window.isDestroyed()) {
        this.window.show();
        this.sendState();
      }
    });
    this.window.on('closed', () => {
      this.window = null;
    });
    this.log('Desktop lion companion enabled');
    return this.window;
  }

  close() {
    this.stopMotion();
    const windowToClose = this.window;
    this.window = null;
    if (!windowToClose || windowToClose.isDestroyed()) return;
    try {
      windowToClose.destroy();
    } catch {
      // Best effort during shutdown.
    }
  }

  setVisible(visible) {
    const nextVisible = Boolean(visible);
    if (nextVisible) this.create();
    else {
      this.close();
      this.log('Desktop lion companion hidden');
    }
    this.onVisibilityChange(nextVisible, Boolean(this.window && !this.window.isDestroyed()));
    return { visible: nextVisible };
  }

  isVisible() {
    return Boolean(this.window && !this.window.isDestroyed());
  }

  handleState(sender, payload) {
    if (!this.isTrustedSender(sender)) return false;
    this.state = sanitize(payload, this.state.message);
    this.sendState();
    this.startMotion(this.state.action);
    return true;
  }

  handleAction(sender, action) {
    if (!this.isTrustedSender(sender) || !ACTIONS.has(action)) {
      return { action: this.state.action, moving: false };
    }
    return { action, moving: this.startMotion(action) };
  }

  handleVisibility(sender, visible) {
    if (!this.isTrustedSender(sender)) return { visible: this.isVisible() };
    return this.setVisible(visible);
  }

  dispose() {
    this.close();
    this.mainWindow = null;
  }
}

module.exports = {
  LionPetWindow,
  LION_PET_ACTIONS: ACTIONS,
  LION_PET_MOTION_RULES: MOTION_RULES,
  LION_PET_SKINS: SKINS,
  LION_PET_STATES: STATES,
  getLionPetMotionPlan: getMotionPlan,
  sanitizeLionPetState: sanitize,
};
