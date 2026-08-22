// Fetch proxy: content scripts can't reach the local backend directly
// (Gmail's page CORS/CSP doesn't apply to the service worker, which uses
// host_permissions instead), so every API call funnels through here.

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

async function backendUrl() {
  const { backendUrl } = await chrome.storage.sync.get("backendUrl");
  return (backendUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "api") return false;

  (async () => {
    const base = await backendUrl();
    try {
      const response = await fetch(base + message.path, {
        method: message.method || "GET",
        headers: message.body ? { "Content-Type": "application/json" } : undefined,
        body: message.body ? JSON.stringify(message.body) : undefined,
      });
      let data = null;
      try {
        data = await response.json();
      } catch (_e) {
        // Non-JSON body (empty 204s etc.) — status alone is enough.
      }
      sendResponse({ ok: response.ok, status: response.status, data });
    } catch (error) {
      sendResponse({ ok: false, status: 0, data: null, error: String(error) });
    }
  })();

  return true; // keep the message channel open for the async response
});
