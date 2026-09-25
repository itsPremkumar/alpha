'use strict';

/**
 * The native lion window gets an even smaller bridge than the main Alpha
 * renderer: it can receive sanitized state, request a bounded local action,
 * and close itself, nothing else.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('alphaPet', {
  onState(callback) {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('alpha:lion-pet-state', listener);
    return () => ipcRenderer.removeListener('alpha:lion-pet-state', listener);
  },
  performAction(action) {
    return ipcRenderer.invoke('alpha:lion-pet-perform', typeof action === 'string' ? action : 'idle');
  },
  close() {
    return ipcRenderer.invoke('alpha:lion-pet-visible', false);
  },
});
