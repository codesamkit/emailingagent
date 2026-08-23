// The floating agent panel, two tabs:
//  - Email: the open message's analysis — summary, importance, effort,
//    no-reply flag, calendar context, reply outline with expand-to-draft.
//    Opening an unread email triggers a fast single-message refresh, so the
//    read flip is picked up and the outline appears without a manual run.
//  - Inbox: the whole processed inbox in OUR order (importance / effort /
//    category / newest) — Gmail's own rows can't be reordered, so the sorted
//    view lives here; clicking a row opens that thread in Gmail.
//
// Each open message renders a node carrying data-legacy-message-id (hex id
// == ProcessedEmail.email_id). The panel is fixed-position rather than woven
// into Gmail's DOM so Gmail markup changes can't break the layout.

(() => {
  let currentEmailId = null;
  let panel = null;
  let activeTab = "email";
  let inboxSort = "importance";

  const EFFORT_RANK = { quick: 0, moderate: 1, involved: 2 };

  // --- tiny DOM helpers (all API data goes through textContent, never HTML) ---
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function section(title) {
    const wrap = el("div", "ea-section");
    wrap.appendChild(el("div", "ea-section-title", title));
    return wrap;
  }

  function formatSlot(slot) {
    const opts = { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
    const start = new Date(slot.start).toLocaleString(undefined, opts);
    const end = new Date(slot.end).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    return `${start} – ${end}`;
  }

  function levelBadge(email) {
    const level = email.importanceLevel || "unscored";
    const badge = el("span", `ea-badge ea-level-${level}`);
    badge.textContent =
      email.importanceScore != null ? `${level} ${Math.round(email.importanceScore)}` : level;
    if (email.importanceJustification) badge.title = email.importanceJustification;
    return badge;
  }

  // --- panel shell -----------------------------------------------------------
  function ensurePanel() {
    if (panel) return panel;
    panel = el("div", "ea-panel");

    const header = el("div", "ea-panel-header");
    header.appendChild(el("span", "ea-panel-title", "Valence"));

    const tabs = el("div", "ea-tabs");
    for (const [key, label] of [["email", "Email"], ["inbox", "Inbox"], ["ask", "Ask"], ["todo", "To-Do"]]) {
      const tab = el("button", "ea-tab", label);
      tab.dataset.tab = key;
      tab.addEventListener("click", () => setTab(key));
      tabs.appendChild(tab);
    }
    header.appendChild(tabs);

    const toggle = el("button", "ea-toggle", "–");
    toggle.addEventListener("click", () => {
      panel.classList.toggle("ea-collapsed");
      toggle.textContent = panel.classList.contains("ea-collapsed") ? "+" : "–";
    });
    header.appendChild(toggle);

    panel.appendChild(header);
    panel.appendChild(el("div", "ea-panel-body"));
    panel.appendChild(el("div", "ea-panel-inbox"));
    const askPane = el("div", "ea-panel-ask");
    panel.appendChild(askPane);
    panel.appendChild(el("div", "ea-panel-todo"));
    document.body.appendChild(panel);

    EmailAgentAsk.mount(askPane);
    setTab("email");
    renderInbox();
    EmailAgent.onRefresh(renderInbox);
    return panel;
  }

  function setTab(key) {
    activeTab = key;
    ensurePanel();
    panel.querySelectorAll(".ea-tab").forEach((tab) =>
      tab.classList.toggle("ea-tab-active", tab.dataset.tab === key)
    );
    panel.querySelector(".ea-panel-body").hidden = key !== "email";
    panel.querySelector(".ea-panel-inbox").hidden = key !== "inbox";
    panel.querySelector(".ea-panel-ask").hidden = key !== "ask";
    panel.querySelector(".ea-panel-todo").hidden = key !== "todo";
    if (key === "todo") renderTodo();
  }

  function showMessage(text) {
    const body = ensurePanel().querySelector(".ea-panel-body");
    body.replaceChildren(el("div", "ea-empty", text));
  }

  // --- Email tab -------------------------------------------------------------
  function render(email) {
    const body = ensurePanel().querySelector(".ea-panel-body");
    body.replaceChildren();

    const head = el("div", "ea-section");
    head.appendChild(el("div", "ea-subject", email.subject || "(no subject)"));
    head.appendChild(el("div", "ea-sender", email.sender || ""));
    body.appendChild(head);

    const chips = el("div", "ea-chips");
    if (email.importanceLevel) chips.appendChild(levelBadge(email));
    if (email.category) chips.appendChild(el("span", "ea-chip", email.category));
    if (email.replyEffort) {
      const chip = el("span", "ea-chip ea-chip-effort", `${email.replyEffort} reply`);
      chip.title = "Estimated effort to reply, from the outline";
      chips.appendChild(chip);
    }
    if (email.isNoReply) {
      const chip = el("span", "ea-chip ea-chip-noreply", "no-reply");
      if (email.noReplyReason) chip.title = email.noReplyReason;
      chips.appendChild(chip);
    }
    if (email.isSchedulingRelated) chips.appendChild(el("span", "ea-chip", "scheduling"));
    if (chips.childElementCount) body.appendChild(chips);

    if (email.summary) {
      const sec = section("Summary");
      sec.appendChild(el("p", "ea-text", email.summary));
      body.appendChild(sec);
    }

    if (email.importanceJustification) {
      const sec = section("Why this ranking");
      sec.appendChild(el("p", "ea-text ea-muted", email.importanceJustification));
      body.appendChild(sec);
    }

    renderFeedback(body, email);
    renderContext(body, email);
    renderProposedEvent(body, email);

    const ctx = email.calendarContext;
    if (ctx) {
      const slots = ctx.suggestedSlots || [];
      if (slots.length) {
        const sec = section("Suggested times");
        const list = el("ul", "ea-list");
        slots.forEach((slot) => list.appendChild(el("li", null, formatSlot(slot))));
        sec.appendChild(list);
        body.appendChild(sec);
      }

      const events = (ctx.events || []).slice(0, 5);
      if (events.length) {
        const sec = section(`Your calendar (${ctx.eventCount ?? events.length} events)`);
        const list = el("ul", "ea-list");
        events.forEach((event) => {
          const when = event.allDay
            ? new Date(event.start).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
            : formatSlot(event);
          list.appendChild(el("li", null, `${event.summary || "(busy)"} — ${when}`));
        });
        sec.appendChild(list);
        if ((ctx.busyBlocks || []).length) {
          sec.appendChild(el("p", "ea-text ea-muted", `${ctx.busyBlocks.length} busy blocks in the checked window.`));
        }
        body.appendChild(sec);
      }
    }

    if (email.replyOutline?.length) {
      const sec = section("Reply outline");
      const list = el("ul", "ea-list");
      email.replyOutline.forEach((line) => list.appendChild(el("li", null, line)));
      sec.appendChild(list);

      const button = el("button", "ea-button", "Expand to full draft");
      button.addEventListener("click", () => expand(email.emailId, button, sec));
      sec.appendChild(button);
      body.appendChild(sec);
    } else if (email.isNoReply) {
      body.appendChild(el("div", "ea-empty", "No reply needed — automated sender."));
    } else if (email.readStatus === "unread") {
      // The user is literally reading it, so Gmail has flipped it to read —
      // pick that up now instead of waiting for the next bulk refresh.
      body.appendChild(el("div", "ea-empty", "Generating reply outline…"));
      generateOutlineNow(email.emailId);
    }
  }

  // --- sender-priors feedback loop: the ONLY UI for this feature now that
  // the webapp (api/static/index.html) is being dropped — see commit
  // 698aba4. Ported logic, not reimplemented: same endpoint, same payload
  // shapes ({level} / {isNoReply}), same "applies to every future email
  // from this sender" behavior. ------------------------------------------
  function renderFeedback(body, email) {
    const sec = section("Correct this");
    const seg = el("span", "ea-feedback-seg");
    const buttons = [];
    for (const level of ["low", "medium", "high", "urgent"]) {
      const btn = el("button", "ea-feedback-level", level);
      if (email.importanceLevel === level) btn.classList.add("ea-feedback-level-on");
      btn.addEventListener("click", () => submitFeedback(email.emailId, { level }, buttons));
      seg.appendChild(btn);
      buttons.push(btn);
    }
    sec.appendChild(seg);

    const noReplyBtn = el(
      "button",
      "ea-button ea-button-quiet",
      email.isNoReply ? "This sender is a real correspondent" : "This sender is automated"
    );
    noReplyBtn.addEventListener("click", () =>
      submitFeedback(email.emailId, { isNoReply: !email.isNoReply }, buttons)
    );
    buttons.push(noReplyBtn);
    sec.appendChild(noReplyBtn);

    sec.appendChild(
      el(
        "p",
        "ea-text ea-muted",
        `Applies to every email from ${email.sender || "this sender"}, now and on every future run.`
      )
    );
    body.appendChild(sec);
  }

  async function submitFeedback(emailId, payload, buttons) {
    buttons.forEach((b) => (b.disabled = true));
    const result = await EmailAgent.sendFeedback(emailId, payload);
    if (emailId !== currentEmailId) return;
    if (result?.ok) {
      render(result.data.email);
    } else {
      buttons.forEach((b) => (b.disabled = false));
      showMessage(result?.data?.detail || "Feedback failed — check the backend logs.");
    }
  }

  // --- context-graph neighborhood: what makes cross-thread correlation
  // VISIBLE rather than merely felt. Empty until Track A's extraction
  // pipeline has actually run (api/main.py's relatedContext is real
  // wiring against real tables, just naturally empty right now). ---------
  function renderContext(body, email) {
    const ctx = email.relatedContext;
    if (!ctx || (!ctx.entities?.length && !ctx.relatedEmailIds?.length)) return;

    const sec = section("Context");
    if (ctx.entities?.length) {
      const chips = el("div", "ea-chips");
      ctx.entities.forEach((entity) => chips.appendChild(el("span", "ea-chip", entity.name)));
      sec.appendChild(chips);
    }
    if (ctx.relatedEmailIds?.length) {
      const list = el("div", "ea-inbox-list");
      ctx.relatedEmailIds.forEach((relatedId) => {
        const known = EmailAgent.forEmail(relatedId);
        const row = el("button", "ea-inbox-row");
        row.appendChild(el("span", "ea-inbox-subject", known?.subject || relatedId));
        if (known?.sender) row.appendChild(el("span", "ea-inbox-sender", known.sender));
        row.addEventListener("click", () => {
          if (!known) return;
          // Same navigation the Inbox tab's own rows use.
          location.hash = `#all/${known.threadId}`;
          setTab("email");
        });
        list.appendChild(row);
      });
      sec.appendChild(list);
    }
    body.appendChild(sec);
  }

  // --- proposed calendar event: extracted by the pipeline, created on
  // Google Calendar ONLY when the user clicks Add here (or in Valence). ----
  function renderProposedEvent(body, email) {
    const event = email.proposedEvent;
    const status = email.proposedEventStatus;
    if (!event || status === "none" || status === "declined") return;

    const sec = section("Proposed calendar event");
    const card = el("div", "ea-event-card");
    card.appendChild(el("div", "ea-event-title", event.title));
    card.appendChild(el("div", "ea-event-when", formatSlot(event)));
    if (event.location) card.appendChild(el("div", "ea-text ea-muted", `📍 ${event.location}`));
    if (event.attendees?.length) {
      card.appendChild(el("div", "ea-text ea-muted", `With: ${event.attendees.join(", ")}`));
    }
    if (event.description) card.appendChild(el("p", "ea-text", event.description));

    if (status === "approved") {
      card.appendChild(el("div", "ea-event-ok", "✓ On your Google Calendar"));
    } else {
      if (status === "failed") {
        card.appendChild(el("div", "ea-empty", `Creating it failed: ${event.error || "unknown error"}`));
      }
      const actions = el("div", "ea-event-actions");
      const approve = el("button", "ea-button", status === "failed" ? "Retry" : "Add to calendar");
      const decline = el("button", "ea-button ea-button-quiet", "Dismiss");
      approve.addEventListener("click", () => decideEvent(email.emailId, "approve", approve, decline));
      decline.addEventListener("click", () => decideEvent(email.emailId, "decline", approve, decline));
      actions.appendChild(approve);
      actions.appendChild(decline);
      card.appendChild(actions);
    }
    sec.appendChild(card);
    body.appendChild(sec);
  }

  async function decideEvent(emailId, action, approveBtn, declineBtn) {
    approveBtn.disabled = declineBtn.disabled = true;
    approveBtn.textContent = action === "approve" ? "Creating…" : approveBtn.textContent;
    const result =
      action === "approve"
        ? await EmailAgent.approveEvent(emailId)
        : await EmailAgent.declineEvent(emailId);
    if (emailId !== currentEmailId) return;
    if (result?.ok) {
      render(result.data);
    } else {
      // 502 = the Calendar API call failed; the backend already recorded
      // FAILED, so re-fetch to show the error + Retry state.
      const detail = await EmailAgent.getDetail(emailId);
      if (detail?.ok) render(detail.data);
      else showMessage(result?.data?.detail || "Calendar action failed — check the backend logs.");
    }
  }

  const readFlipAttempted = new Set();
  async function generateOutlineNow(emailId) {
    if (readFlipAttempted.has(emailId)) return;
    readFlipAttempted.add(emailId);
    const result = await EmailAgent.refreshEmail(emailId);
    if (emailId !== currentEmailId) return;
    if (result?.ok) {
      render(result.data);
    } else {
      showMessage("Couldn't refresh this email — check the backend logs.");
    }
  }

  async function expand(emailId, button, sec) {
    button.disabled = true;
    button.textContent = "Expanding…";
    const result = await EmailAgent.expandDraft(emailId);
    button.textContent = "Expand to full draft";
    button.disabled = false;

    sec.querySelector(".ea-draft")?.remove();
    const box = el("div", "ea-draft");
    if (result?.ok) {
      box.appendChild(el("pre", "ea-draft-text", result.data.draft));
      const copy = el("button", "ea-button", "Copy draft");
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(result.data.draft);
        copy.textContent = "Copied ✓";
        setTimeout(() => (copy.textContent = "Copy draft"), 1500);
      });
      box.appendChild(copy);
    } else {
      box.appendChild(
        el("div", "ea-empty", result?.data?.detail || "Draft expansion failed — is the backend running?")
      );
    }
    sec.appendChild(box);
  }

  // --- Inbox tab: the agent-sorted list ---------------------------------------
  // Mirrors the server's sort semantics (api/filters.py): unknowns last,
  // ties broken by importance.
  function sortedEmails() {
    const emails = EmailAgent.allEmails();
    const byImportance = (a, b) => (b.importanceScore ?? -1) - (a.importanceScore ?? -1);
    if (inboxSort === "newest") {
      return emails.sort((a, b) => new Date(b.receivedAt) - new Date(a.receivedAt));
    }
    if (inboxSort === "category") {
      return emails.sort((a, b) => {
        if (!a.category && !b.category) return byImportance(a, b);
        if (!a.category) return 1;
        if (!b.category) return -1;
        return a.category.localeCompare(b.category) || byImportance(a, b);
      });
    }
    if (inboxSort === "effort") {
      return emails.sort((a, b) => {
        const ra = EFFORT_RANK[a.replyEffort] ?? 3;
        const rb = EFFORT_RANK[b.replyEffort] ?? 3;
        return ra - rb || byImportance(a, b);
      });
    }
    return emails.sort(byImportance);
  }

  function renderInbox() {
    if (!panel) return;
    const wrap = panel.querySelector(".ea-panel-inbox");
    wrap.replaceChildren();

    const bar = el("div", "ea-inbox-bar");
    const select = document.createElement("select");
    select.className = "ea-select";
    for (const [value, label] of [
      ["importance", "Sort: importance"],
      ["effort", "Sort: effort (quick first)"],
      ["category", "Sort: category"],
      ["newest", "Sort: newest"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = value === inboxSort;
      select.appendChild(option);
    }
    select.addEventListener("change", () => {
      inboxSort = select.value;
      renderInbox();
    });
    bar.appendChild(select);
    wrap.appendChild(bar);

    const emails = sortedEmails();
    if (!emails.length) {
      wrap.appendChild(el("div", "ea-empty", "No processed emails yet — is the backend running?"));
      return;
    }
    const list = el("div", "ea-inbox-list");
    for (const email of emails.slice(0, 100)) {
      const row = el("button", "ea-inbox-row");
      const chips = el("span", "ea-inbox-row-chips");
      chips.appendChild(levelBadge(email));
      if (email.replyEffort) chips.appendChild(el("span", "ea-chip ea-chip-effort", email.replyEffort));
      if (email.category) chips.appendChild(el("span", "ea-chip", email.category));
      if (email.proposedEventStatus === "suggested") {
        const chip = el("span", "ea-chip", "📅 pending");
        chip.title = "Has a proposed calendar event awaiting your decision";
        chips.appendChild(chip);
      }
      row.appendChild(chips);
      row.appendChild(el("span", "ea-inbox-subject", email.subject || "(no subject)"));
      row.appendChild(el("span", "ea-inbox-sender", email.sender || ""));
      row.addEventListener("click", () => {
        // Gmail still resolves legacy hex thread ids in the location hash.
        location.hash = `#all/${email.threadId}`;
        setTab("email");
      });
      list.appendChild(row);
    }
    wrap.appendChild(list);
  }

  // --- To-Do tab: extracted action items + "needs a reply" markers -----------
  async function renderTodo() {
    if (!panel) return;
    const wrap = panel.querySelector(".ea-panel-todo");
    wrap.replaceChildren(el("div", "ea-empty", "Loading…"));

    const result = await EmailAgent.getTodos();
    if (activeTab !== "todo") return; // user switched tabs mid-fetch
    if (!result?.ok) {
      wrap.replaceChildren(el("div", "ea-empty", "Couldn't load the to-do list — is the backend running?"));
      return;
    }

    const todos = result.data.todos || [];
    wrap.replaceChildren();
    if (!todos.length) {
      wrap.appendChild(el("div", "ea-empty", "Nothing outstanding — you're caught up."));
      return;
    }

    for (const kind of ["action_item", "needs_reply"]) {
      const items = todos.filter((t) => t.kind === kind);
      if (!items.length) continue;
      const sec = section(kind === "action_item" ? "Action items" : "Needs your reply");
      const list = el("div", "ea-todo-list");
      items.forEach((item) => list.appendChild(todoRow(item)));
      sec.appendChild(list);
      wrap.appendChild(sec);
    }
  }

  function todoRow(item) {
    const row = el("div", "ea-todo-row");
    const check = el("button", "ea-todo-check");
    check.title = "Mark complete";
    check.addEventListener("click", () => completeTodo(item.todoId, row, check));
    row.appendChild(check);

    const text = el("div", "ea-todo-text-wrap");
    text.appendChild(el("div", "ea-todo-text", item.text));
    text.appendChild(el("div", "ea-todo-meta", `${item.sender || ""} — ${item.subject || "(no subject)"}`));
    row.appendChild(text);

    row.addEventListener("click", (e) => {
      if (e.target === check) return;
      // Gmail resolves legacy hex thread ids in the location hash, same as
      // the Inbox tab's rows.
      location.hash = `#all/${item.threadId}`;
      setTab("email");
    });
    return row;
  }

  async function completeTodo(todoId, row, check) {
    check.disabled = true;
    row.classList.add("ea-todo-completing");
    const result = await EmailAgent.completeTodo(todoId);
    if (result?.ok) {
      row.remove();
    } else {
      check.disabled = false;
      row.classList.remove("ea-todo-completing");
    }
  }

  // --- track which message is open -------------------------------------------
  async function sync() {
    ensurePanel();
    const nodes = document.querySelectorAll("[data-legacy-message-id]");
    if (!nodes.length) {
      // Drive the empty state off the actual condition (no message open), not
      // off a state transition -- on first load currentEmailId is already null,
      // so a transition guard here left the default Email pane blank.
      currentEmailId = null;
      showMessage("Open an email to see its analysis — or use the Inbox tab.");
      return;
    }
    const emailId = nodes[nodes.length - 1].getAttribute("data-legacy-message-id");
    if (emailId === currentEmailId) return;
    currentEmailId = emailId;

    const result = await EmailAgent.getDetail(emailId);
    if (emailId !== currentEmailId) return; // user moved on mid-fetch
    if (result?.ok) {
      render(result.data);
    } else if (result?.status === 404) {
      // The newest message isn't in the database — try the other messages of
      // this open thread (the agent may have processed an earlier one).
      const sibling = [...nodes]
        .map((n) => EmailAgent.forEmail(n.getAttribute("data-legacy-message-id")))
        .find(Boolean);
      if (sibling) {
        const fallback = await EmailAgent.getDetail(sibling.emailId);
        if (fallback?.ok) return render(fallback.data);
      }
      await refreshAndRetry(emailId);
    } else {
      showMessage("Backend unreachable — start it with: uvicorn api.main:app");
    }
  }

  // New mail the pipeline hasn't seen: fetch + process just this message,
  // once per message id per page load.
  const refreshAttempted = new Set();
  async function refreshAndRetry(emailId) {
    if (refreshAttempted.has(emailId)) {
      showMessage("This email couldn't be processed — check the backend logs.");
      return;
    }
    refreshAttempted.add(emailId);
    showMessage("New email — processing it now…");
    const result = await EmailAgent.refreshEmail(emailId);
    if (emailId !== currentEmailId) return; // user moved on while we worked
    if (result?.ok) {
      render(result.data);
    } else {
      showMessage(result?.data?.detail || "This email couldn't be processed — check the backend logs.");
    }
  }

  let scheduled = null;
  function scheduleSync() {
    if (scheduled) return;
    scheduled = setTimeout(() => {
      scheduled = null;
      sync();
    }, 300);
  }

  new MutationObserver(scheduleSync).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("hashchange", scheduleSync);
  scheduleSync();
})();
