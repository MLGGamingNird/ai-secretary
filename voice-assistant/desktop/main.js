// Electron main process.
// Creates a transparent, always-on-top, frameless window -- the "HUD" look.
// Also registers a global keyboard shortcut so you can trigger VESPER from
// anywhere on your PC, not just by clicking into the overlay window.

const { app, BrowserWindow, globalShortcut, ipcMain, desktopCapturer, screen } = require("electron");
const os = require("os");
const http = require("http");
const fs = require("fs");
const path = require("path");

const HOTKEY = "CommandOrControl+Shift+Space";
let mainWindow;
let videoWindow = null;

function createOverlayWindow() {
  mainWindow = new BrowserWindow({
    width: 640,
    height: 580,
    minWidth: 640,
    minHeight: 580,
    maxWidth: 700,
    maxHeight: 750,
    x: 40,
    y: 40,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: true,
    skipTaskbar: true,
    webPreferences: {
      preload: `${__dirname}/preload.js`,
      contextIsolation: true,
    },
  });

  mainWindow.setAlwaysOnTop(true, "screen-saver");
  mainWindow.loadFile("index.html");
  // Uncomment while developing to inspect the overlay's DOM/console:
  mainWindow.webContents.openDevTools({ mode: "detach" });
}

// A tiny local server just for video-window.html. YouTube's embed player
// rejects pages loaded via file:// with error 153 (no valid referrer/origin
// to check) -- serving it over real http://127.0.0.1 gives it a genuine
// origin, which fixes that. Picks a random free port automatically.
let videoServerPort = null;
function ensureVideoServer() {
  return new Promise((resolve) => {
    if (videoServerPort) return resolve(videoServerPort);
    const server = http.createServer((req, res) => {
      fs.readFile(path.join(__dirname, "video-window.html"), (err, data) => {
        if (err) {
          res.writeHead(500);
          res.end("Failed to load video-window.html");
          return;
        }
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(data);
      });
    });
    server.listen(0, "127.0.0.1", () => {
      videoServerPort = server.address().port;
      resolve(videoServerPort);
    });
  });
}

// A small, separate floating window just for live video -- kept apart from
// the main HUD since video genuinely needs real screen space, unlike
// everything else in the compact overlay.
async function createVideoWindow(candidates) {
  if (videoWindow && !videoWindow.isDestroyed()) {
    videoWindow.close();
  }

  const port = await ensureVideoServer();

  const mainBounds = mainWindow.getBounds();
  videoWindow = new BrowserWindow({
    width: 400,
    height: 300,
    x: mainBounds.x + mainBounds.width + 12,
    y: mainBounds.y,
    frame: false,
    alwaysOnTop: true,
    resizable: true,
    minWidth: 280,
    minHeight: 210,
    skipTaskbar: true,
    webPreferences: {
      preload: `${__dirname}/video-preload.js`,
      contextIsolation: true,
    },
  });

  videoWindow.setAlwaysOnTop(true, "screen-saver");
  const query = new URLSearchParams({ candidates: JSON.stringify(candidates) }).toString();
  videoWindow.loadURL(`http://127.0.0.1:${port}/video-window.html?${query}`);
}

// A small, separate floating window for VESPER's own self-contained code
// display -- shows real code with specific lines flagged, never overlays
// or touches your actual screen/files. No local server needed here since
// there's no third-party embed with origin requirements, unlike video.
let codeBoxWindow = null;
function createCodeBoxWindow(reviewData) {
  if (codeBoxWindow && !codeBoxWindow.isDestroyed()) {
    codeBoxWindow.close();
  }

  const mainBounds = mainWindow.getBounds();
  codeBoxWindow = new BrowserWindow({
    width: 480,
    height: 420,
    x: mainBounds.x + mainBounds.width + 12,
    y: mainBounds.y,
    frame: false,
    alwaysOnTop: true,
    resizable: true,
    minWidth: 320,
    minHeight: 240,
    skipTaskbar: true,
    webPreferences: {
      preload: `${__dirname}/code-box-preload.js`,
      contextIsolation: true,
    },
  });

  codeBoxWindow.setAlwaysOnTop(true, "screen-saver");
  codeBoxWindow.loadFile("code-box.html", { query: { data: JSON.stringify(reviewData) } });
}

app.whenReady().then(() => {
  createOverlayWindow();

  const registered = globalShortcut.register(HOTKEY, () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("toggle-recording");
    }
  });
  if (!registered) {
    console.error(
      `Could not register global hotkey ${HOTKEY} -- another app may already be using it.`
    );
  }
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});

// Captures the primary screen and returns it as a base64 PNG string, used
// by Screen Help.
//
// VESPER's own window is briefly hidden first, so its own HUD panel never
// shows up in the screenshot it's asked to look at.
ipcMain.handle("capture-screen", async () => {
  const wasMainVisible = mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible();

  if (wasMainVisible) mainWindow.hide();

  // Give the OS compositor a moment to actually redraw without our window
  // before we capture -- hiding isn't instantaneous from the screen's
  // perspective.
  await new Promise((r) => setTimeout(r, 120));

  try {
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width, height } = primaryDisplay.size;

    const sources = await desktopCapturer.getSources({
      types: ["screen"],
      thumbnailSize: { width, height },
    });

    if (!sources.length) {
      throw new Error("No screen sources available to capture.");
    }

    return sources[0].thumbnail.toPNG().toString("base64");
  } finally {
    // Always restore visibility, even if capture failed.
    if (wasMainVisible && !mainWindow.isDestroyed()) mainWindow.show();
  }
});

// Real system stats -- no fake gauges. CPU usage needs two samples a moment
// apart (Windows doesn't expose a simple "current load" the way Linux does).
function getCpuUsagePercent() {
  return new Promise((resolve) => {
    const start = os.cpus();
    setTimeout(() => {
      const end = os.cpus();
      let idleDiff = 0, totalDiff = 0;
      for (let i = 0; i < start.length; i++) {
        const s = start[i].times, e = end[i].times;
        const sTotal = s.user + s.nice + s.sys + s.idle + s.irq;
        const eTotal = e.user + e.nice + e.sys + e.idle + e.irq;
        idleDiff += e.idle - s.idle;
        totalDiff += eTotal - sTotal;
      }
      resolve(totalDiff > 0 ? Math.round((1 - idleDiff / totalDiff) * 100) : 0);
    }, 200);
  });
}

function getMemUsagePercent() {
  const total = os.totalmem();
  const free = os.freemem();
  return Math.round(((total - free) / total) * 100);
}

ipcMain.handle("get-system-stats", async () => {
  const [cpu, mem] = [await getCpuUsagePercent(), getMemUsagePercent()];
  return { cpu, mem };
});

// Height toggle -- lets you get a bit more vertical breathing room, not
// tied to revealing any hidden content (there isn't any by height).
const SHORT_HEIGHT = 580, TALL_HEIGHT = 750;

ipcMain.handle("toggle-hud-height", () => {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const [w, h] = mainWindow.getSize();
  const newHeight = h < (SHORT_HEIGHT + TALL_HEIGHT) / 2 ? TALL_HEIGHT : SHORT_HEIGHT;
  mainWindow.setSize(w, newHeight, true);
});

ipcMain.handle("open-video-window", async (event, { candidates }) => {
  await createVideoWindow(candidates);
});

ipcMain.on("close-video-window", () => {
  if (videoWindow && !videoWindow.isDestroyed()) videoWindow.close();
});

ipcMain.handle("open-code-box", (event, { reviewData }) => {
  createCodeBoxWindow(reviewData);
});

ipcMain.on("close-code-box", () => {
  if (codeBoxWindow && !codeBoxWindow.isDestroyed()) codeBoxWindow.close();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// Extraction previously only ran when the listening window closed
// naturally -- if you said something and quit right after, it never got
// saved at all. This makes sure it always gets one last chance before the
// app is actually gone, with a timeout so a slow/unreachable brain can't
// make quitting hang forever.
let isQuitting = false;
app.on("before-quit", async (event) => {
  if (isQuitting) return;
  event.preventDefault();
  isQuitting = true;
  try {
    await Promise.race([
      fetch("http://127.0.0.1:8001/reset_conversation", { method: "POST" }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 3000)),
    ]);
  } catch (err) {
    console.warn("[vesper] couldn't save memory before quit:", err.message);
  }
  app.quit();
});
