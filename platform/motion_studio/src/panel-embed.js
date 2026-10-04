const PANEL_ORIGIN = "https://gosha.website";
const STATUS = "gosha-motion-studio:status";
const REQUEST = "gosha-motion-studio:request";

// The panel only observes connection state and can request an orderly exit.
// Angles, keys, packages and motor commands remain inside the existing editor.
export function mountPanelEmbed({ target = window, embedded, readState, disconnect, signal }) {
  if (!embedded || target.parent === target) return { publish() {} };
  let previous = "";
  function publish(force = false) {
    const state = readState() || "disconnected";
    if (!force && previous === state) return;
    previous = state;
    target.parent.postMessage({ type: STATUS, state }, PANEL_ORIGIN);
  }
  target.addEventListener("message", (event) => {
    if (event.origin !== PANEL_ORIGIN || event.source !== target.parent || event.data?.type !== REQUEST) return;
    if (event.data.action === "disconnect") disconnect();
    if (["status", "disconnect"].includes(event.data.action)) publish(true);
  }, { signal });
  publish(true);
  return { publish };
}
