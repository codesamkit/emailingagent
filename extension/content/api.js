// Shared API client + processed-email index for the Gmail content scripts.
// Loaded first (see manifest content_scripts order); inbox.js and detail.js
// use the `EmailAgent` global it defines.

const EmailAgent = (() => {
  const INDEX_REFRESH_MS = 60_000; // re-read the processed-email index
  const PIPELINE_REFRESH_MS = 120_000; // ingest + process new mail via the backend
  const CALENDAR_TTL_MS = 300_000; // reuse a calendar read for 5 minutes

  // threadId -> email summary row, emailId -> email summary row
  const byThread = new Map();
  const byEmailId = new Map();
  let lastRefresh = 0;
  let backendReachable = null; // null = unknown, then true/false
  let pipelineRunning = false;
  let calendarCache = null; // { days, at, result } -- see getCalendar below
  const refreshListeners = [];

  function call(path, method = "GET", body = null) {
    return chrome.runtime.sendMessage({ type: "api", path, method, body });
  }

  async function refreshIndex() {
    const result = await call("/api/emails?limit=1000");
    backendReachable = !!result?.ok;
    if (!result?.ok) return false;

    byThread.clear();
    byEmailId.clear();
    for (const email of result.data.emails || []) {
      byEmailId.set(email.emailId, email);
      // Keep the most important email as the thread's representative so the
      // inbox badge reflects the strongest signal in the thread.
      const existing = byThread.get(email.threadId);
      if (!existing || (email.importanceScore ?? -1) > (existing.importanceScore ?? -1)) {
        byThread.set(email.threadId, email);
      }
    }
    lastRefresh = Date.now();
    refreshListeners.forEach((fn) => fn());
    return true;
  }

  // Ask the backend to ingest new mail + process what changed, then re-read
  // the index. A 409 means another caller's refresh is mid-flight — its
  // results land in the same database, so just re-read the index after a beat.
  async function refreshPipeline() {
    if (pipelineRunning) return { ok: false, status: 0, busy: true };
    pipelineRunning = true;
    try {
      const result = await call("/api/refresh", "POST");
      if (result?.status === 409) {
        await new Promise((r) => setTimeout(r, 10_000));
      }
      await refreshIndex();
      return result;
    } finally {
      pipelineRunning = false;
    }
  }

  async function ensureFresh() {
    if (Date.now() - lastRefresh > INDEX_REFRESH_MS) await refreshIndex();
  }

  refreshIndex().then(() => refreshPipeline());
  setInterval(refreshIndex, INDEX_REFRESH_MS);
  setInterval(refreshPipeline, PIPELINE_REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) ensureFresh();
  });

  return {
    call,
    refreshIndex,
    refreshPipeline,
    onRefresh: (fn) => refreshListeners.push(fn),
    forThread: (threadId) => byThread.get(threadId),
    forEmail: (emailId) => byEmailId.get(emailId),
    allEmails: () => [...byEmailId.values()],
    isReachable: () => backendReachable,
    getDetail: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}`),
    // Fast path: re-fetch one message (picks up read flips / brand-new mail)
    // and run only the stages it needs — seconds, vs minutes for /api/refresh.
    refreshEmail: async (emailId) => {
      const result = await call(`/api/emails/${encodeURIComponent(emailId)}/refresh`, "POST");
      if (result?.ok) await refreshIndex();
      return result;
    },
    expandDraft: (emailId) => call(`/api/emails/${encodeURIComponent(emailId)}/expand`, "POST"),
    // To-do list: extracted action items + "needs a reply" markers.
    getTodos: () => call("/api/todos"),
    completeTodo: (todoId) => call(`/api/todos/${encodeURIComponent(todoId)}/complete`, "POST"),
    // The user's whole calendar for the Calendar tab. Every call hits the
    // Google Calendar API server-side, so a successful read is cached briefly
    // -- switching tabs shouldn't re-fetch. `force` is the Refresh button.
    getCalendar: async (days = 7, { force = false } = {}) => {
      const fresh =
        calendarCache &&
        calendarCache.days === days &&
        Date.now() - calendarCache.at < CALENDAR_TTL_MS;
      if (fresh && !force) return calendarCache.result;

      const result = await call(`/api/calendar?days=${days}`);
      // Only cache successes: a failure should be retried, not remembered.
      if (result?.ok) calendarCache = { days, at: Date.now(), result };
      return result;
    },
    // Human-approval flow for proposed calendar events. With auto-add on
    // (CALENDAR_AUTO_ADD, the default) the pipeline creates plausible events
    // itself, so these are the path for the ones it skipped — past dates and
    // multi-day spans — plus retries after a failure.
    approveEvent: (emailId) =>
      call(`/api/emails/${encodeURIComponent(emailId)}/calendar-event/approve`, "POST"),
    declineEvent: (emailId) =>
      call(`/api/emails/${encodeURIComponent(emailId)}/calendar-event/decline`, "POST"),
    sendFeedback: (emailId, payload) =>
      call(`/api/emails/${encodeURIComponent(emailId)}/feedback`, "POST", payload),
    // Agent chat over the background.js port transport (background.js can't
    // use `call()`'s sendMessage proxy here since that can't stream). Opens
    // one port per message; onEvent fires for every SSE event the backend
    // sends (text_delta / tool_start / tool_end / done / error), in order.
    // Returns a function that closes the port early if the caller navigates
    // away mid-stream.
    chatStream: (message, conversationId, onEvent) => {
      const port = chrome.runtime.connect({ name: "agent-chat" });
      port.onMessage.addListener(onEvent);
      port.postMessage({ type: "start", message, conversationId });
      return () => port.disconnect();
    },
    getConversation: (conversationId) =>
      call(`/api/agent/conversations/${encodeURIComponent(conversationId)}`),
  };
})();
