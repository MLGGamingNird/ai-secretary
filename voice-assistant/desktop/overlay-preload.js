// Preload for the fullscreen annotation canvas window. This window has no
// interactive UI of its own -- it only ever receives draw commands from the
// main HUD window and renders them.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("overlayAPI", {
  onDraw: (callback) => ipcRenderer.on("draw", (event, cmd) => callback(cmd)),
});
