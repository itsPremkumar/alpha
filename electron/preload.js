'use strict';

/**
 * Alpha Desktop preload script.
 *
 * Runs in an isolated world before the page loads. Only exposes a minimal
 * API over IPC — no Node.js access is leaked to the renderer. Writes are
 * limited to explicit user toggles (currently: start-with-Windows).
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
};

contextBridge.exposeInMainWorld('agentWorkspace', bridgeApi);
contextBridge.exposeInMainWorld('alpha', bridgeApi);
