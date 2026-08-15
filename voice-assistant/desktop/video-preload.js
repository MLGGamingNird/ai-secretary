// Preload for the standalone video window. Only exposes closing itself --
// no other IPC access, keeping this window's privileges minimal.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("videoWindowAPI", {
  close: () => ipcRenderer.send("close-video-window"),
});
