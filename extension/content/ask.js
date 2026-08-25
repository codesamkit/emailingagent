// The "Ask" tab: a chat panel over the in-app agent (agent/loop.py via
// POST /api/agent/chat). Exposes EmailAgentAsk.mount(container), called once
// by detail.js when it builds the panel shell (detail.js owns the tabs/panel
// itself; this owns only what's inside the "ask" tab's container) — same
// loose-coupling shape as the EmailAgent global content/api.js exposes.
//
// All model output goes through textContent, never innerHTML — this renders
// arbitrary text inside the user's mail client, an injection surface (see
// the note in detail.js). Assistant text is markdown, so it goes through
// content/markdown.js, which builds nodes and keeps that same rule.

const EmailAgentAsk = (() => {
  let container = null;
  let listEl = null;
  let inputEl = null;
  let sendBtn = null;
  let conversationId = null;
  let streaming = false;
  let stopStream = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function addMessage(role, text) {
    const row = el("div", `ea-ask-msg ea-ask-msg-${role}`);
    row.appendChild(el("div", "ea-ask-msg-text", text));
    listEl.appendChild(row);
    listEl.scrollTop = listEl.scrollHeight;
    return row;
  }

  function addSourcesLine(row, emailIds) {
    if (!emailIds.length) return;
    const details = document.createElement("details");
    details.className = "ea-ask-sources";
    const summary = el("summary", null, `used ${emailIds.length} source${emailIds.length === 1 ? "" : "s"}`);
    details.appendChild(summary);
    const list = el("ul", "ea-list");
    for (const emailId of emailIds) {
      const known = EmailAgent.forEmail(emailId);
      const item = document.createElement("li");
      const link = el("button", "ea-ask-source-link", known?.subject || emailId);
      link.addEventListener("click", () => {
        if (!known) return;
        location.hash = `#all/${known.threadId}`;
      });
      item.appendChild(link);
      list.appendChild(item);
    }
    details.appendChild(list);
    row.appendChild(details);
  }

  // Best-effort, tool-shape-agnostic: collect any *_email_id(s) value out of
  // a tool result rather than hardcoding a case per tool — agent/tools.py's
  // result shapes are free to evolve without this needing to change too.
  function extractEmailIds(value, out) {
    if (value == null) return;
    if (Array.isArray(value)) {
      value.forEach((v) => extractEmailIds(v, out));
      return;
    }
    if (typeof value !== "object") return;
    for (const [key, val] of Object.entries(value)) {
      const k = key.toLowerCase();
      if (k === "email_id" || k === "emailid") {
        if (typeof val === "string") out.add(val);
      } else if (k.endsWith("email_ids") || k.endsWith("emailids")) {
        if (Array.isArray(val)) val.forEach((v) => typeof v === "string" && out.add(v));
      } else {
        extractEmailIds(val, out);
      }
    }
  }

  function setStreaming(isStreaming) {
    streaming = isStreaming;
    inputEl.disabled = isStreaming;
    sendBtn.disabled = isStreaming;
    sendBtn.textContent = isStreaming ? "…" : "Send";
  }

  function send() {
    const message = inputEl.value.trim();
    if (!message || streaming) return;
    // The empty state is the greeting; the first real turn replaces it.
    const empty = listEl.querySelector(".ea-ask-empty");
    if (empty) empty.remove();
    inputEl.value = "";
    addMessage("user", message);
    setStreaming(true);

    const assistantRow = addMessage("assistant", "");
    const textEl = assistantRow.querySelector(".ea-ask-msg-text");
    const statusEl = el("div", "ea-ask-status", "thinking…");
    assistantRow.appendChild(statusEl);

    let text = "";
    const sourceIds = new Set();

    stopStream = EmailAgent.chatStream(message, conversationId, (event) => {
      if (event.type === "text_delta") {
        text += event.text || "";
        // The agent answers in markdown (headings, bullets, bold); textContent
        // showed the literal "## " and "**" to the user. Re-rendered from the
        // full accumulated text each delta rather than appended to, so a run
        // spanning two deltas still resolves.
        EmailAgentMarkdown.render(text, textEl);
        listEl.scrollTop = listEl.scrollHeight;
      } else if (event.type === "tool_start") {
        statusEl.textContent = `using ${event.tool}…`;
      } else if (event.type === "tool_end") {
        extractEmailIds(event.toolResult, sourceIds);
      } else if (event.type === "error") {
        statusEl.remove();
        if (!text) textEl.textContent = "Something went wrong.";
        assistantRow.appendChild(el("div", "ea-ask-error", event.error || "Unknown error"));
        setStreaming(false);
      } else if (event.type === "done") {
        conversationId = event.conversationId || conversationId;
        statusEl.remove();
        addSourcesLine(assistantRow, [...sourceIds]);
        setStreaming(false);
        stopStream = null;
      }
    });
  }

  function mount(target) {
    if (container) return; // idempotent — detail.js's panel is built once
    container = target;

    listEl = el("div", "ea-ask-list");
    container.appendChild(listEl);

    // Empty state: the logo mark over the greeting, centered in the pane —
    // shown until the first message, then removed by send().
    const empty = el("div", "ea-ask-empty");
    empty.appendChild(el("div", "ea-ask-empty-mark"));
    empty.appendChild(
      el(
        "div",
        "ea-ask-empty-text",
        "Ask me about your mailbox — “what's still open with Henderson”, “anything urgent today”, “draft a reply to Alex”."
      )
    );
    listEl.appendChild(empty);

    const bar = el("div", "ea-ask-bar");
    inputEl = document.createElement("input");
    inputEl.type = "text";
    inputEl.className = "ea-ask-input";
    inputEl.placeholder = "Ask about your mailbox…";
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send();
    });
    sendBtn = el("button", "ea-button", "Send");
    sendBtn.addEventListener("click", send);
    bar.appendChild(inputEl);
    bar.appendChild(sendBtn);
    container.appendChild(bar);
  }

  window.addEventListener("beforeunload", () => stopStream && stopStream());

  return { mount };
})();
