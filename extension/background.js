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

// Streaming transport for the agent chat endpoint: chrome.runtime.sendMessage
// (above) can't stream a response, and with up to 8 tool turns a chat reply
// can take 20+ seconds — a blocking spinner for that long reads as broken.
// The content script opens a port; this fetches the SSE endpoint and posts
// each parsed event over the port as it arrives, rather than waiting for the
// whole response like every other call above does.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "agent-chat") return;

  port.onMessage.addListener(async (message) => {
    if (message?.type !== "start") return;
    const base = await backendUrl();
    try {
      const response = await fetch(base + "/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: message.message,
          conversationId: message.conversationId || undefined,
        }),
      });
      if (!response.ok || !response.body) {
        port.postMessage({ type: "error", error: "HTTP " + response.status });
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop(); // the last piece may be an incomplete chunk
        for (const chunk of chunks) {
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          try {
            port.postMessage(JSON.parse(line.slice("data: ".length)));
          } catch (_e) {
            // Malformed SSE chunk — skip it rather than kill the stream.
          }
        }
      }
    } catch (error) {
      port.postMessage({ type: "error", error: String(error) });
    } finally {
      try {
        port.disconnect();
      } catch (_e) {
        // Already disconnected (e.g. the content script tore down the panel).
      }
    }
  });
});
