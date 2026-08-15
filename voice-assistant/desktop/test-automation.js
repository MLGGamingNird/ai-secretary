// Standalone test -- NOT part of the main app yet. Just proves that
// @nut-tree-fork/nut-js can actually control the mouse and keyboard on
// this specific machine, before we build anything real on top of it.
//
// How to use:
//   1. Open Notepad (or any text editor) somewhere on screen.
//   2. Click into its text area so it has focus.
//   3. Run this script and DON'T touch your mouse/keyboard until it's done.
//   4. You should see the mouse trace a small square, then some text
//      get typed into Notepad automatically.

const { mouse, keyboard, straightTo, Point } = require("@nut-tree-fork/nut-js");

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  console.log("=== VESPER Automation Test ===");
  console.log("Open Notepad (or any text editor), click into the text area, and don't touch anything.");
  console.log("Test starts in 5 seconds...");
  await delay(5000);

  console.log("Moving the mouse in a small square...");
  const start = await mouse.getPosition();
  await mouse.move(straightTo(new Point(start.x + 120, start.y)));
  await delay(300);
  await mouse.move(straightTo(new Point(start.x + 120, start.y + 120)));
  await delay(300);
  await mouse.move(straightTo(new Point(start.x, start.y + 120)));
  await delay(300);
  await mouse.move(straightTo(new Point(start.x, start.y)));
  await delay(300);

  console.log("Typing a test message into whatever's focused...");
  await keyboard.type("VESPER automation test successful!");

  console.log("=== Test complete ===");
  console.log("If the mouse traced a small square AND that text appeared in your editor, the foundation works.");
}

main().catch((err) => {
  console.error("Automation test FAILED:", err);
  console.error("This tells us nut-js isn't working correctly on this machine yet -- paste this error back.");
});
