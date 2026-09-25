'use strict';

/**
 * Alpha Desktop preload script.
 *
 * Runs in an isolated world before the page loads. Only exposes a minimal
 * API over IPC — no Node.js access is leaked to the renderer. The bridge is
 * limited to explicit user toggles plus a bounded, local-only lion companion
 * state/action channel; it never exposes prompts, conversation text, or Node APIs.
 */

const { contextBridge, ipcRenderer } = require('electron');

const bridgeApi = {
  platform: process.platform,

  /**
   * Subscribe to lifecycle/status broadcasts from the main process.
   * @param {(payload: { message: string, detail?: string }) => void} callback
   * @returns {() => void} unsubscribe function
   */
  onStatus(callback) {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('agent-workspace:status', listener);
    ipcRenderer.on('alpha:status', listener);
    return () => {
      ipcRenderer.removeListener('agent-workspace:status', listener);
      ipcRenderer.removeListener('alpha:status', listener);
    };
  },

  /** Current orchestration state (URLs, child PIDs, mode). */
  getStatus() {
    return ipcRenderer.invoke('agent-workspace:status');
  },

  /** Open the per-user data folder (config, homes, logs) in Explorer. */
  openUserData() {
    return ipcRenderer.invoke('agent-workspace:open-user-data');
  },

  /**
   * Start-with-Windows login item state.
   * @returns {Promise<{ supported: boolean, enabled: boolean, active: boolean }>}
   */
  getAutoStart() {
    return ipcRenderer.invoke('agent-workspace:get-auto-start');
  },

  /**
   * Register or clear the Windows login item (installed app only).
   * @param {boolean} enabled
   * @returns {Promise<boolean>} the OS-reported state after applying.
   */
  setAutoStart(enabled) {
    return ipcRenderer.invoke('agent-workspace:set-auto-start', Boolean(enabled));
  },

  /**
   * Report only the bounded companion state. The main process sanitizes and
   * forwards this to the optional transparent desktop window; prompt text is
   * never part of this channel.
   */
  reportLionPetState(payload) {
    ipcRenderer.send('alpha:lion-pet-state', payload && typeof payload === 'object' ? payload : {});
  },

  /** Show or hide the optional native desktop companion window. */
  setLionPetVisible(visible) {
    return ipcRenderer.invoke('alpha:lion-pet-visible', Boolean(visible));
  },

  /** Observe explicit native-window toggles made from the desktop menu. */
  onLionPetVisibility(callback) {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('alpha:lion-pet-visibility', listener);
    return () => ipcRenderer.removeListener('alpha:lion-pet-visibility', listener);
  },
};

contextBridge.exposeInMainWorld('agentWorkspace', bridgeApi);
contextBridge.exposeInMainWorld('alpha', bridgeApi);
