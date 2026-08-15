// Preload script -- runs in a privileged context before the renderer loads.
// Safely exposes just the one thing the overlay page needs: a way to react
// when the global hotkey is pressed, without giving the page full Node/IPC
// access (which would be a security risk).

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vesperAPI", {
  onToggleRecording: (callback) => ipcRenderer.on("toggle-recording", callback),
  captureScreen: () => ipcRenderer.invoke("capture-screen"),
  getSystemStats: () => ipcRenderer.invoke("get-system-stats"),
  toggleHudHeight: () => ipcRenderer.invoke("toggle-hud-height"),
  openVideoWindow: (candidates) => ipcRenderer.invoke("open-video-window", { candidates }),
  openCodeBox: (reviewData) => ipcRenderer.invoke("open-code-box", { reviewData }),
});
