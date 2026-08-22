// Open-email view: a floating panel with the agent's take on the message —
// summary, importance, no-reply flag, calendar slots, and the reply outline
// with expand-to-draft.
//
// Each open message in a Gmail thread renders a node carrying
// data-legacy-message-id (hex id == our ProcessedEmail.email_id). We follow
// the newest message in the thread. The panel is fixed-position rather than
// woven into Gmail's DOM so Gmail markup changes can't break the layout.

(() => {
  let currentEmailId = null;
  let panel = null;

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

  // --- panel shell -----------------------------------------------------------
  function ensurePanel() {
    if (panel) return panel;
    panel = el("div", "ea-panel ea-hidden");

    const header = el("div", "ea-panel-header");
    header.appendChild(el("span", "ea-panel-title", "Email Agent"));
    const toggle = el("button", "ea-toggle", "–");
    toggle.addEventListener("click", () => {
      panel.classList.toggle("ea-collapsed");
      toggle.textContent = panel.classList.contains("ea-collapsed") ? "+" : "–";
    });
    header.appendChild(toggle);
    panel.appendChild(header);
    panel.appendChild(el("div", "ea-panel-body"));

    document.body.appendChild(panel);
    return panel;
  }

  function showMessage(text) {
    const body = ensurePanel().querySelector(".ea-panel-body");
    body.replaceChildren(el("div", "ea-empty", text));
    panel.classList.remove("ea-hidden");
  }

  // --- rendering -------------------------------------------------------------
  function render(email) {
    const body = ensurePanel().querySelector(".ea-panel-body");
    body.replaceChildren();

    const head = el("div", "ea-section");
    head.appendChild(el("div", "ea-subject", email.subject || "(no subject)"));
    head.appendChild(el("div", "ea-sender", email.sender || ""));
    body.appendChild(head);

    const chips = el("div", "ea-chips");
    if (email.importanceLevel) {
      const chip = el(
        "span",
        `ea-badge ea-level-${email.importanceLevel}`,
        email.importanceScore != null
          ? `${email.importanceLevel} ${Math.round(email.importanceScore)}`
          : email.importanceLevel
      );
      if (email.importanceJustification) chip.title = email.importanceJustification;
      chips.appendChild(chip);
    }
    if (email.category) chips.appendChild(el("span", "ea-chip", email.category));
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

    const slots = email.calendarContext?.suggestedSlots || [];
    if (slots.length) {
      const sec = section("Suggested times");
      const list = el("ul", "ea-list");
      slots.forEach((slot) => list.appendChild(el("li", null, formatSlot(slot))));
      sec.appendChild(list);
      body.appendChild(sec);
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
      body.appendChild(el("div", "ea-empty", "Reply outline appears once the email is read."));
    }

    panel.classList.remove("ea-hidden");
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

  // --- track which message is open -------------------------------------------
  async function sync() {
    const nodes = document.querySelectorAll("[data-legacy-message-id]");
    if (!nodes.length) {
      currentEmailId = null;
      panel?.classList.add("ea-hidden");
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
      showMessage("This email hasn't been processed yet — run the pipeline and refresh.");
    } else {
      showMessage("Backend unreachable — start it with: uvicorn api.main:app");
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
