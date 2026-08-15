// Preload for the standalone code box window. Only exposes closing itself
// -- no other IPC access, keeping this window's privileges minimal.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("codeBoxAPI", {
  close: () => ipcRenderer.send("close-code-box"),
});
