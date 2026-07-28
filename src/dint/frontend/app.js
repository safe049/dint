/* dint – frontend logic.
 *
 * Talks to the FastAPI backend over REST + SSE:
 *   - sessions / messages / chat (streaming)
 *   - learner model: progress, memory, skills (categorised lists with CRUD)
 *     and knowledge (interactive force graph)
 *   - settings
 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  sessionId: null,
  sessions: [],
  sending: false,
  abort: null, // AbortController for the active stream (if any)
  autoScroll: true, // stick to bottom unless the user scrolls up
};

/* ------------------------------------------------------------------ */
/* API helpers                                                        */
/* ------------------------------------------------------------------ */

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

function toast(msg, kind = "info") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.classList.add("show"), 10);
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, 3200);
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* Remove fenced code blocks that contain model tool-call payloads
 * (e.g. ```json {"tool_calls": [...]} ```) so they are not shown to the
 * user as ordinary code. Handles both complete blocks and an unterminated
 * trailing block that may appear mid-stream. */
function stripToolCallBlocks(text) {
  if (!text) return text;
  // Complete fenced blocks (``` ... ```) whose body mentions tool_calls.
  let out = text.replace(/```[^\n]*\n[\s\S]*?```/g, (block) =>
    /"tool_calls"\s*:|tool_calls/.test(block) ? "" : block
  );
  // Unterminated trailing fence (still streaming) that mentions tool_calls.
  out = out.replace(/```[^\n]*\n[\s\S]*$/, (block) =>
    /"tool_calls"\s*:|tool_calls/.test(block) ? "" : block
  );
  return out;
}

function renderMarkdown(text) {
  text = stripToolCallBlocks(text);
  if (window.marked && window.DOMPurify) {
    const html = marked.parse(text || "", { breaks: true });
    return DOMPurify.sanitize(html);
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

/* ------------------------------------------------------------------ */
/* Sessions                                                           */
/* ------------------------------------------------------------------ */

async function loadSessions() {
  try {
    state.sessions = await api("GET", "/api/sessions");
  } catch (e) {
    state.sessions = [];
  }
  renderSessionList();
}

function renderSessionList() {
  const list = $("#session-list");
  list.innerHTML = "";
  if (!state.sessions.length) {
    const empty = document.createElement("div");
    empty.className = "no-sessions";
    empty.textContent = t("no_sessions");
    list.appendChild(empty);
    return;
  }
  for (const s of state.sessions) {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === state.sessionId ? " active" : "");
    item.dataset.id = s.id;

    const title = document.createElement("div");
    title.className = "session-title";
    title.textContent = s.title || t("untitled_session");

    const meta = document.createElement("div");
    meta.className = "session-meta";
    meta.textContent =
      (s.subject ? s.subject + " · " : "") + fmtDate(s.updated_at);

    const del = document.createElement("button");
    del.className = "session-del";
    del.textContent = "✕";
    del.title = t("delete_session");
    del.onclick = (ev) => {
      ev.stopPropagation();
      deleteSession(s.id);
    };

    item.appendChild(title);
    item.appendChild(meta);
    item.appendChild(del);
    item.onclick = () => openSession(s.id);
    list.appendChild(item);
  }
}

function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

async function newSession() {
  try {
    const res = await api("POST", "/api/sessions", {});
    state.sessionId = res.id;
    await loadSessions();
    await openSession(res.id);
  } catch (e) {
    toast(t("err_create_session", { msg: e.message }), "error");
  }
}

async function deleteSession(id) {
  if (!confirm(t("confirm_delete_session"))) return;
  try {
    await api("DELETE", `/api/sessions/${id}`);
    if (state.sessionId === id) {
      state.sessionId = null;
      $("#messages").innerHTML = "";
      $("#welcome").classList.remove("hidden");
    }
    await loadSessions();
    if (lmState.tab === "progress") renderLearnerModel();
  } catch (e) {
    toast(t("err_delete", { msg: e.message }), "error");
  }
}

async function openSession(id) {
  state.sessionId = id;
  state.autoScroll = true;
  renderSessionList();
  $("#welcome").classList.add("hidden");
  const msgs = await api("GET", `/api/sessions/${id}/messages`);
  renderMessages(msgs);
  if (lmState.tab === "progress") renderLearnerModel();
}

function renderMessages(msgs) {
  const box = $("#messages");
  box.innerHTML = "";
  for (const m of msgs) {
    if (m.role === "user") {
      appendMessage("user", m.content, m.created_at);
    } else if (m.role === "assistant") {
      appendMessage("assistant", m.content, m.created_at);
    }
    // tool / system rows are skipped in the transcript
  }
  scrollChat(true);
}

/* ------------------------------------------------------------------ */
/* Chat                                                               */
/* ------------------------------------------------------------------ */

function appendMessage(role, content, ts) {
  const box = $("#messages");
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble md";
  if (role === "assistant") {
    bubble.innerHTML = renderMarkdown(content);
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const retry = document.createElement("button");
    retry.className = "msg-action-btn";
    retry.textContent = t("retry");
    retry.title = t("retry_title");
    retry.onclick = () => regenerateReply();
    const copy = document.createElement("button");
    copy.className = "msg-action-btn";
    copy.textContent = t("copy");
    copy.title = t("copy_title");
    copy.onclick = () => copyText(bubble.innerText, copy);
    actions.appendChild(retry);
    actions.appendChild(copy);
    wrap.appendChild(bubble);
    wrap.appendChild(actions);
  } else {
    bubble.textContent = content;
    wrap.appendChild(bubble);
  }
  // Timestamp footer.
  const time = document.createElement("div");
  time.className = "msg-time";
  time.textContent = fmtMsgTime(ts);
  wrap.appendChild(time);
  box.appendChild(wrap);
  return bubble;
}

function fmtMsgTime(ts) {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function appendToolChip(bubble, name, args) {
  let tools = bubble.querySelector(".tool-chips");
  if (!tools) {
    tools = document.createElement("div");
    tools.className = "tool-chips";
    bubble.appendChild(tools);
  }
  const chip = document.createElement("span");
  chip.className = "tool-chip";
  let argStr = "";
  try {
    const a = typeof args === "string" ? JSON.parse(args) : args;
    const keys = Object.keys(a || {});
    argStr = keys.length ? " " + keys.map((k) => `${k}=${short(a[k])}`).join(" ") : "";
  } catch (_) { /* ignore */ }
  chip.textContent = `⚙ ${name}${argStr}`;
  tools.appendChild(chip);
}

function short(v) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 24 ? s.slice(0, 24) + "…" : s;
}

function scrollChat(force) {
  const box = $("#messages");
  if (force || state.autoScroll) box.scrollTop = box.scrollHeight;
}

/* True when the user is parked near the bottom of the transcript. */
function nearBottom(box, threshold = 80) {
  return box.scrollHeight - box.scrollTop - box.clientHeight < threshold;
}

/* Abort the in-flight stream (Stop button / Escape). */
function stopStream() {
  if (state.abort) {
    state.abort.abort();
    state.abort = null;
  }
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // Fallback for non-secure contexts.
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  if (btn) {
    const prev = btn.textContent;
    btn.textContent = t("copied");
    setTimeout(() => (btn.textContent = prev), 1200);
  }
}

async function sendMessage() {
  const input = $("#user-input");
  const text = input.value.trim();
  if (!text || state.sending) return;

  if (!state.sessionId) {
    const res = await api("POST", "/api/sessions", {});
    state.sessionId = res.id;
    await loadSessions();
    $("#welcome").classList.add("hidden");
  }

  state.sending = true;
  state.autoScroll = true;
  setSendStopMode(true);
  input.value = "";
  autoresize(input);

  appendMessage("user", text);
  scrollChat(true);

  const bubble = appendMessage("assistant", "");
  bubble.classList.add("streaming");
  let acc = "";

  state.abort = new AbortController();
  try {
    await streamChat(text, {
      onToken: (tok) => {
        acc += tok;
        bubble.innerHTML = renderMarkdown(acc);
        scrollChat();
      },
      onToolCall: (name, args) => appendToolChip(bubble, name, args),
      onDone: (data) => {
        if (!acc && data && data.reply) {
          acc = data.reply;
          bubble.innerHTML = renderMarkdown(acc);
        }
      },
    }, state.abort.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      if (!acc) bubble.innerHTML = renderMarkdown("…");
    } else if (!acc) {
      bubble.innerHTML = renderMarkdown("⚠️ " + e.message);
    } else {
      toast(t("err_stream", { msg: e.message }), "error");
    }
  } finally {
    bubble.classList.remove("streaming");
    state.sending = false;
    state.abort = null;
    setSendStopMode(false);
    scrollChat(true);
    loadSessions();
    renderLearnerModel();
  }
}

/* Swap the send button between send (→) and stop (■) while streaming. */
function setSendStopMode(stopping) {
  const btn = $("#send-btn");
  if (stopping) {
    btn.textContent = "■";
    btn.title = t("stop");
    btn.disabled = false;
    btn.classList.add("stop-mode");
    btn.onclick = stopStream;
  } else {
    btn.textContent = "→";
    btn.title = t("send");
    btn.disabled = false;
    btn.classList.remove("stop-mode");
    btn.onclick = sendMessage;
  }
}

async function regenerateReply() {
  if (state.sending || !state.sessionId) return;

  // The backend always regenerates the most recent assistant reply, so stream
  // the replacement into the last assistant bubble on screen.
  const bubbles = $$("#messages .msg.assistant .bubble");
  if (!bubbles.length) return;
  const bubble = bubbles[bubbles.length - 1];

  state.sending = true;
  state.autoScroll = true;
  setSendStopMode(true);
  bubble.innerHTML = "";
  bubble.classList.add("streaming");
  let acc = "";

  state.abort = new AbortController();
  try {
    await streamRegenerate({
      onToken: (tok) => {
        acc += tok;
        bubble.innerHTML = renderMarkdown(acc);
        scrollChat();
      },
      onToolCall: (name, args) => appendToolChip(bubble, name, args),
      onDone: (data) => {
        if (!acc && data && data.reply) {
          acc = data.reply;
          bubble.innerHTML = renderMarkdown(acc);
        }
      },
    }, state.abort.signal);
  } catch (e) {
    if (e.name === "AbortError") {
      if (!acc) bubble.innerHTML = renderMarkdown("…");
    } else if (!acc) {
      bubble.innerHTML = renderMarkdown("⚠️ " + e.message);
    } else {
      toast(t("err_stream", { msg: e.message }), "error");
    }
  } finally {
    bubble.classList.remove("streaming");
    state.sending = false;
    state.abort = null;
    setSendStopMode(false);
    scrollChat(true);
    loadSessions();
    renderLearnerModel();
  }
}

async function streamRegenerate(handlers, signal) {
  const res = await fetch("/api/chat/regenerate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  await consumeSseStream(res, handlers);
}

async function streamChat(message, handlers, signal) {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, message }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }

  await consumeSseStream(res, handlers);
}

async function consumeSseStream(res, handlers) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      handleSseBlock(raw, handlers);
    }
  }
  if (buf.trim()) handleSseBlock(buf, handlers);
}

function handleSseBlock(block, handlers) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch (_) {
    data = dataLines.join("\n");
  }
  if (event === "token" && handlers.onToken)
    handlers.onToken(typeof data === "string" ? data : data.token || "");
  else if (event === "tool_call" && handlers.onToolCall)
    handlers.onToolCall(data.name, data.arguments);
  else if (event === "done" && handlers.onDone) handlers.onDone(data);
  else if (event === "error") throw new Error(data.detail || "stream error");
}

/* ------------------------------------------------------------------ */
/* Learner model dashboard                                            */
/* ------------------------------------------------------------------ */

const lmState = {
  tab: "progress",
  // knowledge graph view state
  nodes: [],
  edges: [],
  pos: new Map(),
  transform: { x: 0, y: 0, k: 1 },
  dragging: null,
  panning: null,
};

const KIND_ORDER = ["goal", "preference", "fact", "note"];
const SKILL_STATUS_ORDER = ["demonstrated", "emerging", "locked"];
const PROGRESS_STATUS_ORDER = ["demonstrated", "next", "locked"];

function lmStat(text) {
  $("#lm-stat").textContent = text;
}

function showLmEmpty(show, text) {
  const el = $("#lm-empty");
  if (text) $("#lm-empty-text").textContent = text;
  el.classList.toggle("hidden", !show);
}

function groupBy(items, keyFn) {
  const out = {};
  for (const it of items) {
    const k = keyFn(it) || "—";
    (out[k] = out[k] || []).push(it);
  }
  return out;
}

function switchTab(tab) {
  lmState.tab = tab;
  $$(".panel-tabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab)
  );
  hideNodeMenu();
  renderLearnerModel();
}

async function renderLearnerModel() {
  if ($("#panels").classList.contains("hidden")) return;
  const tab = lmState.tab;
  const isGraph = tab === "knowledge";

  // Toggle graph vs list chrome.
  $("#lm-svg").classList.toggle("hidden", !isGraph);
  $("#lm-list").classList.toggle("hidden", isGraph);
  $("#lm-zoom-group").classList.toggle("hidden", !isGraph);
  $("#lm-add-btn").classList.toggle("hidden", isGraph);
  showLmEmpty(false);

  if (tab === "progress") return renderProgressList();
  if (tab === "memory") return renderMemoryList();
  if (tab === "skills") return renderSkillsList();
  return renderKnowledgeGraph();
}

/* ---- generic list builders ---- */

function buildSection(headerText, items, rowBuilder) {
  const sec = document.createElement("div");
  sec.className = "lm-section";
  const h = document.createElement("div");
  h.className = "lm-section-title";
  h.textContent = `${headerText} · ${items.length}`;
  sec.appendChild(h);
  for (const it of items) sec.appendChild(rowBuilder(it));
  return sec;
}

function buildRow(titleText, metaText, badgeText, badgeClass, onEdit, onDelete) {
  const row = document.createElement("div");
  row.className = "lm-row";

  const main = document.createElement("div");
  main.className = "lm-row-main";
  const title = document.createElement("div");
  title.className = "lm-row-title";
  title.textContent = titleText;
  main.appendChild(title);
  if (metaText) {
    const meta = document.createElement("div");
    meta.className = "lm-row-meta";
    meta.textContent = metaText;
    main.appendChild(meta);
  }
  row.appendChild(main);

  if (badgeText) {
    const b = document.createElement("span");
    b.className = "lm-badge " + (badgeClass || "");
    b.textContent = badgeText;
    row.appendChild(b);
  }

  const actions = document.createElement("div");
  actions.className = "lm-row-actions";
  const edit = document.createElement("button");
  edit.className = "lm-row-btn";
  edit.textContent = "✎";
  edit.title = t("edit");
  edit.onclick = onEdit;
  const del = document.createElement("button");
  del.className = "lm-row-btn danger";
  del.textContent = "✕";
  del.title = t("delete");
  del.onclick = onDelete;
  actions.appendChild(edit);
  actions.appendChild(del);
  row.appendChild(actions);
  return row;
}

/* ---- Progress list (per current session) ---- */

async function renderProgressList() {
  const list = $("#lm-list");
  list.innerHTML = "";
  if (!state.sessionId) {
    lmStat("");
    showLmEmpty(true, t("lm_open_session"));
    return;
  }
  let items;
  try {
    items = await api("GET", `/api/sessions/${state.sessionId}/progress`);
  } catch (e) {
    toast(t("err_load_progress"), "error");
    return;
  }
  lmStat(t("stat_concepts", { n: items.length, s: items.length === 1 ? "" : "s" }));
  if (!items.length) {
    showLmEmpty(true, t("lm_no_concepts"));
    return;
  }
  showLmEmpty(false);
  const groups = groupBy(items, (i) => i.status);
  for (const status of PROGRESS_STATUS_ORDER) {
    if (!groups[status]) continue;
    list.appendChild(
      buildSection(statusLabel(status), groups[status], progressRow)
    );
  }
}

function statusLabel(s) {
  if (s === "demonstrated") return t("status_demonstrated");
  if (s === "next") return t("status_next");
  return t("status_locked");
}

function progressRow(item) {
  return buildRow(
    item.concept,
    null,
    statusLabel(item.status),
    "st-" + item.status,
    () => openProgressModal(item),
    () => deleteProgress(item)
  );
}

async function deleteProgress(item) {
  if (!confirm(t("confirm_delete_concept", { name: item.concept }))) return;
  try {
    await api("DELETE", `/api/sessions/${state.sessionId}/progress`, {
      concept: item.concept,
    });
    renderLearnerModel();
  } catch (e) {
    toast(t("err_delete", { msg: e.message }), "error");
  }
}

function openProgressModal(existing) {
  const isEdit = !!existing;
  openItemModal(
    isEdit ? t("title_edit_concept") : t("title_add_concept"),
    [
      {
        name: "concept",
        label: t("field_concept"),
        type: "text",
        value: isEdit ? existing.concept : "",
        placeholder: t("ph_concept"),
      },
      {
        name: "status",
        label: t("field_status"),
        type: "select",
        options: ["locked", "next", "demonstrated"],
        value: isEdit ? existing.status : "next",
      },
    ],
    async (v) => {
      const concept = v.concept.trim();
      if (!concept) {
        toast(t("err_concept_required"), "error");
        return;
      }
      try {
        // If the concept name changed, remove the old row first.
        if (isEdit && concept !== existing.concept) {
          await api("DELETE", `/api/sessions/${state.sessionId}/progress`, {
            concept: existing.concept,
          });
        }
        await api("PUT", `/api/sessions/${state.sessionId}/progress`, {
          concept,
          status: v.status,
        });
        closeItemModal();
        renderLearnerModel();
      } catch (e) {
        toast(t("err_save", { msg: e.message }), "error");
      }
    }
  );
}

/* ---- Memory list ---- */

async function renderMemoryList() {
  const list = $("#lm-list");
  list.innerHTML = "";
  let items;
  try {
    items = await api("GET", "/api/memory");
  } catch (e) {
    toast(t("err_load_memory"), "error");
    return;
  }
  lmStat(t("stat_entries", { n: items.length, y: items.length === 1 ? "y" : "ies" }));
  if (!items.length) {
    showLmEmpty(true, t("lm_no_memory"));
    return;
  }
  showLmEmpty(false);
  const groups = groupBy(items, (m) => m.kind);
  const seen = new Set();
  for (const kind of KIND_ORDER) {
    if (!groups[kind]) continue;
    list.appendChild(buildSection(kind, groups[kind], memoryRow));
    seen.add(kind);
  }
  for (const kind of Object.keys(groups)) {
    if (seen.has(kind)) continue;
    list.appendChild(buildSection(kind, groups[kind], memoryRow));
  }
}

function memoryRow(item) {
  return buildRow(
    item.content,
    item.created_at ? t("added", { when: fmtDate(item.created_at) }) : null,
    item.kind,
    "kind-" + item.kind,
    () => openMemoryModal(item),
    () => deleteMemory(item)
  );
}

async function deleteMemory(item) {
  if (!confirm(t("confirm_delete_memory"))) return;
  try {
    await api("DELETE", `/api/memory/${item.id}`);
    renderLearnerModel();
  } catch (e) {
    toast(t("err_delete", { msg: e.message }), "error");
  }
}

function openMemoryModal(existing) {
  const isEdit = !!existing;
  openItemModal(
    isEdit ? t("title_edit_memory") : t("title_add_memory"),
    [
      {
        name: "kind",
        label: t("field_kind"),
        type: "select",
        options: ["note", "fact", "preference", "goal"],
        value: isEdit ? existing.kind : "note",
      },
      {
        name: "content",
        label: t("field_content"),
        type: "textarea",
        value: isEdit ? existing.content : "",
        placeholder: t("ph_memory"),
      },
    ],
    async (v) => {
      const content = v.content.trim();
      if (!content) {
        toast(t("err_content_required"), "error");
        return;
      }
      try {
        if (isEdit) {
          await api("PUT", `/api/memory/${existing.id}`, {
            kind: v.kind,
            content,
          });
        } else {
          await api("POST", "/api/memory", { kind: v.kind, content });
        }
        closeItemModal();
        renderLearnerModel();
      } catch (e) {
        toast(t("err_save", { msg: e.message }), "error");
      }
    }
  );
}

/* ---- Skills list ---- */

async function renderSkillsList() {
  const list = $("#lm-list");
  list.innerHTML = "";
  let items;
  try {
    items = await api("GET", "/api/skills");
  } catch (e) {
    toast(t("err_load_skills"), "error");
    return;
  }
  lmStat(t("stat_skills", { n: items.length, s: items.length === 1 ? "" : "s" }));
  if (!items.length) {
    showLmEmpty(true, t("lm_no_skills"));
    return;
  }
  showLmEmpty(false);
  const groups = groupBy(items, (s) => s.domain || "general");
  for (const domain of Object.keys(groups).sort()) {
    const rows = groups[domain]
      .slice()
      .sort(
        (a, b) =>
          SKILL_STATUS_ORDER.indexOf(a.status) -
            SKILL_STATUS_ORDER.indexOf(b.status) || b.confidence - a.confidence
      );
    list.appendChild(buildSection(domain, rows, skillRow));
  }
}

function skillRow(item) {
  const pct = Math.round((item.confidence || 0) * 100);
  const evidence = (item.evidence || []).length;
  let meta = t("confidence", { pct });
  if (evidence) meta += " · " + t("notes", { n: evidence, s: evidence === 1 ? "" : "s" });
  return buildRow(
    item.name,
    meta,
    item.status,
    "st-" + item.status,
    () => openSkillModal(item),
    () => deleteSkill(item)
  );
}

async function deleteSkill(item) {
  if (!confirm(t("confirm_delete_skill", { name: item.name }))) return;
  try {
    await api("DELETE", `/api/skills/${encodeURIComponent(item.name)}`);
    renderLearnerModel();
  } catch (e) {
    toast(t("err_delete", { msg: e.message }), "error");
  }
}

function openSkillModal(existing) {
  const isEdit = !!existing;
  openItemModal(
    isEdit ? t("title_edit_skill") : t("title_add_skill"),
    [
      {
        name: "name",
        label: t("field_name"),
        type: "text",
        value: isEdit ? existing.name : "",
        placeholder: t("ph_skill"),
      },
      {
        name: "domain",
        label: t("field_domain"),
        type: "text",
        value: isEdit ? existing.domain || "" : "",
        placeholder: t("ph_domain"),
      },
      {
        name: "status",
        label: t("field_status"),
        type: "select",
        options: ["locked", "emerging", "demonstrated"],
        value: isEdit ? existing.status : "emerging",
      },
      {
        name: "confidence",
        label: t("field_confidence"),
        type: "number",
        step: "0.05",
        min: "0",
        max: "1",
        value: isEdit ? existing.confidence : 0.3,
      },
    ],
    async (v) => {
      const name = v.name.trim();
      if (!name) {
        toast(t("err_skill_required"), "error");
        return;
      }
      const confidence = Math.max(0, Math.min(1, parseFloat(v.confidence) || 0));
      try {
        // Rename first if the name changed.
        if (isEdit && name !== existing.name) {
          await api("PUT", "/api/skills/rename", {
            old_name: existing.name,
            new_name: name,
          });
        }
        await api("PUT", "/api/skills", {
          name,
          domain: v.domain.trim() || null,
          status: v.status,
          confidence,
        });
        closeItemModal();
        renderLearnerModel();
      } catch (e) {
        toast(t("err_save", { msg: e.message }), "error");
      }
    }
  );
}

/* ---- Knowledge graph (unchanged interactive force layout) ---- */

async function renderKnowledgeGraph() {
  let data;
  try {
    data = await api("GET", "/api/knowledge");
  } catch (e) {
    toast(t("err_load_knowledge"), "error");
    return;
  }
  lmState.nodes = data.nodes || [];
  lmState.edges = data.edges || [];
  lmStat(
    t("stat_graph", { nodes: lmState.nodes.length, edges: lmState.edges.length })
  );
  if (!lmState.nodes.length) {
    showLmEmpty(true, t("lm_no_knowledge"));
    $("#lm-nodes").innerHTML = "";
    $("#lm-edges").innerHTML = "";
    return;
  }
  showLmEmpty(false);
  layoutGraph();
  drawGraph();
}

function layoutGraph() {
  const stage = $("#lm-stage");
  const W = stage.clientWidth || 800;
  const H = stage.clientHeight || 600;
  const cx = W / 2;
  const cy = H / 2;
  const n = lmState.nodes.length;
  const radius = Math.min(W, H) * 0.36;

  // Seed new nodes on a circle; keep existing positions stable.
  lmState.nodes.forEach((node, i) => {
    if (!lmState.pos.has(node.id)) {
      const angle = (i / Math.max(1, n)) * Math.PI * 2;
      lmState.pos.set(node.id, {
        x: cx + radius * Math.cos(angle) + (Math.random() - 0.5) * 20,
        y: cy + radius * Math.sin(angle) + (Math.random() - 0.5) * 20,
        vx: 0,
        vy: 0,
      });
    }
  });
  // Drop positions for nodes that no longer exist.
  const ids = new Set(lmState.nodes.map((n) => n.id));
  for (const key of lmState.pos.keys()) {
    if (!ids.has(key)) lmState.pos.delete(key);
  }

  const idSet = ids;
  const edges = lmState.edges.filter(
    (e) => idSet.has(e.src_id) && idSet.has(e.dst_id)
  );

  // Simple force simulation.
  const iterations = 220;
  const repulsion = 5200;
  const springLen = 120;
  const springK = 0.02;
  const centerK = 0.008;
  const damping = 0.85;

  for (let it = 0; it < iterations; it++) {
    const nodes = lmState.nodes;
    // Repulsion.
    for (let i = 0; i < nodes.length; i++) {
      const a = lmState.pos.get(nodes[i].id);
      for (let j = i + 1; j < nodes.length; j++) {
        const b = lmState.pos.get(nodes[j].id);
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const d = Math.sqrt(d2);
        const f = repulsion / d2;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
    }
    // Springs along edges.
    for (const e of edges) {
      const a = lmState.pos.get(e.src_id);
      const b = lmState.pos.get(e.dst_id);
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - springLen) * springK;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }
    // Gravity to center + integrate.
    for (const node of nodes) {
      const p = lmState.pos.get(node.id);
      p.vx += (cx - p.x) * centerK;
      p.vy += (cy - p.y) * centerK;
      p.vx *= damping;
      p.vy *= damping;
      p.x += p.vx;
      p.y += p.vy;
    }
  }
}

function drawGraph() {
  const svg = $("#lm-svg");
  const edgesG = $("#lm-edges");
  const nodesG = $("#lm-nodes");
  edgesG.innerHTML = "";
  nodesG.innerHTML = "";
  const ns = "http://www.w3.org/2000/svg";
  const idSet = new Set(lmState.nodes.map((n) => n.id));
  const nodeById = new Map(lmState.nodes.map((n) => [n.id, n]));

  // Edges.
  for (const e of lmState.edges) {
    if (!idSet.has(e.src_id) || !idSet.has(e.dst_id)) continue;
    const a = lmState.pos.get(e.src_id);
    const b = lmState.pos.get(e.dst_id);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", a.x);
    line.setAttribute("y1", a.y);
    line.setAttribute("x2", b.x);
    line.setAttribute("y2", b.y);
    line.setAttribute("class", "lm-edge");
    line.setAttribute("marker-end", "url(#lm-arrow)");
    const title = document.createElementNS(ns, "title");
    title.textContent = `${nodeById.get(e.src_id).label} —${e.relation}→ ${nodeById.get(e.dst_id).label}`;
    line.appendChild(title);
    edgesG.appendChild(line);
  }

  // Nodes.
  for (const node of lmState.nodes) {
    const p = lmState.pos.get(node.id);
    const g = document.createElementNS(ns, "g");
    g.setAttribute("class", "lm-node");
    g.setAttribute("transform", `translate(${p.x}, ${p.y})`);
    g.dataset.id = node.id;
    g.dataset.label = node.label;

    const label = node.label || "?";
    const w = Math.max(46, label.length * 7.2 + 22);
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", -w / 2);
    rect.setAttribute("y", -15);
    rect.setAttribute("width", w);
    rect.setAttribute("height", 30);
    rect.setAttribute("rx", 15);
    rect.setAttribute("class", "lm-node-box");

    const text = document.createElementNS(ns, "text");
    text.setAttribute("class", "lm-node-label");
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "central");
    text.textContent = label;

    const tip = document.createElementNS(ns, "title");
    tip.textContent =
      (node.subject ? `[${node.subject}] ` : "") + (node.summary || label);

    g.appendChild(rect);
    g.appendChild(text);
    g.appendChild(tip);

    // Drag + context menu.
    g.addEventListener("pointerdown", (ev) => startNodeDrag(ev, node.id));
    g.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      showNodeMenu(ev, node);
    });

    nodesG.appendChild(g);
  }
  applyTransform();
}

/* ---- pan / zoom ---- */

function applyTransform() {
  const { x, y, k } = lmState.transform;
  $("#lm-viewport").setAttribute(
    "transform",
    `translate(${x}, ${y}) scale(${k})`
  );
}

function zoomBy(factor) {
  const stage = $("#lm-stage");
  const cx = (stage.clientWidth || 800) / 2;
  const cy = (stage.clientHeight || 600) / 2;
  const t = lmState.transform;
  const newK = Math.max(0.25, Math.min(3, t.k * factor));
  // Zoom about the center of the viewport.
  t.x = cx - ((cx - t.x) * newK) / t.k;
  t.y = cy - ((cy - t.y) * newK) / t.k;
  t.k = newK;
  applyTransform();
}

function resetView() {
  lmState.transform = { x: 0, y: 0, k: 1 };
  applyTransform();
}

function startNodeDrag(ev, id) {
  if (ev.button !== 0) return;
  ev.stopPropagation();
  const svg = $("#lm-svg");
  svg.setPointerCapture(ev.pointerId);
  const t = lmState.transform;
  lmState.dragging = {
    id,
    startX: ev.clientX,
    startY: ev.clientY,
    origX: lmState.pos.get(id).x,
    origY: lmState.pos.get(id).y,
    t,
  };
}

function initGraphInteractions() {
  const svg = $("#lm-svg");

  svg.addEventListener("pointerdown", (ev) => {
    if (lmState.dragging) return;
    if (ev.button !== 0) return;
    svg.setPointerCapture(ev.pointerId);
    lmState.panning = {
      startX: ev.clientX,
      startY: ev.clientY,
      origX: lmState.transform.x,
      origY: lmState.transform.y,
    };
  });

  svg.addEventListener("pointermove", (ev) => {
    if (lmState.dragging) {
      const d = lmState.dragging;
      const dx = (ev.clientX - d.startX) / d.t.k;
      const dy = (ev.clientY - d.startY) / d.t.k;
      const p = lmState.pos.get(d.id);
      p.x = d.origX + dx;
      p.y = d.origY + dy;
      const g = $("#lm-nodes").querySelector(`[data-id="${d.id}"]`);
      if (g) g.setAttribute("transform", `translate(${p.x}, ${p.y})`);
      updateEdgesFor(d.id);
    } else if (lmState.panning) {
      const p = lmState.panning;
      lmState.transform.x = p.origX + (ev.clientX - p.startX);
      lmState.transform.y = p.origY + (ev.clientY - p.startY);
      applyTransform();
    }
  });

  const endInteraction = () => {
    lmState.dragging = null;
    lmState.panning = null;
  };
  svg.addEventListener("pointerup", endInteraction);
  svg.addEventListener("pointercancel", endInteraction);

  svg.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      zoomBy(ev.deltaY < 0 ? 1.12 : 0.89);
    },
    { passive: false }
  );

  // Click on empty canvas hides the node context menu.
  svg.addEventListener("pointerdown", () => hideNodeMenu());
}

function updateEdgesFor(id) {
  const nodeById = new Map(lmState.nodes.map((n) => [n.id, n]));
  const lines = $("#lm-edges").querySelectorAll("line");
  let i = 0;
  const idSet = new Set(lmState.nodes.map((n) => n.id));
  const validEdges = lmState.edges.filter(
    (e) => idSet.has(e.src_id) && idSet.has(e.dst_id)
  );
  for (const e of validEdges) {
    const line = lines[i++];
    if (!line) break;
    if (e.src_id === id || e.dst_id === id) {
      const a = lmState.pos.get(e.src_id);
      const b = lmState.pos.get(e.dst_id);
      line.setAttribute("x1", a.x);
      line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x);
      line.setAttribute("y2", b.y);
    }
  }
}

/* ---- node context menu (rename / delete) ---- */

let nodeMenuEl = null;

function ensureNodeMenu() {
  if (nodeMenuEl) return nodeMenuEl;
  nodeMenuEl = document.createElement("div");
  nodeMenuEl.className = "node-menu hidden";
  document.body.appendChild(nodeMenuEl);
  document.addEventListener("pointerdown", (ev) => {
    if (nodeMenuEl && !nodeMenuEl.contains(ev.target)) hideNodeMenu();
  });
  return nodeMenuEl;
}

function showNodeMenu(ev, node) {
  const menu = ensureNodeMenu();
  menu.innerHTML = "";
  menu.dataset.label = node.label;

  const rename = document.createElement("button");
  rename.textContent = t("rename");
  rename.onclick = () => {
    hideNodeMenu();
    openRenameNodeModal(node);
  };
  const del = document.createElement("button");
  del.textContent = t("delete_node");
  del.className = "danger";
  del.onclick = () => {
    hideNodeMenu();
    deleteNode(node);
  };
  menu.appendChild(rename);
  menu.appendChild(del);

  menu.classList.remove("hidden");
  const mw = 140;
  const mh = 70;
  menu.style.left = Math.min(ev.clientX, window.innerWidth - mw) + "px";
  menu.style.top = Math.min(ev.clientY, window.innerHeight - mh) + "px";
}

function hideNodeMenu() {
  if (nodeMenuEl) nodeMenuEl.classList.add("hidden");
}

function openRenameNodeModal(node) {
  openItemModal(
    t("title_rename_concept"),
    [
      {
        name: "new_label",
        label: t("field_new_label"),
        type: "text",
        value: node.label,
      },
    ],
    async (v) => {
      const newLabel = v.new_label.trim();
      if (!newLabel) {
        toast(t("err_label_required"), "error");
        return;
      }
      try {
        await api("PUT", "/api/knowledge/nodes/rename", {
          old_label: node.label,
          new_label: newLabel,
        });
        closeItemModal();
        // Reset positions so the renamed node re-lays out cleanly.
        lmState.pos.clear();
        renderLearnerModel();
        toast(t("toast_renamed"), "success");
      } catch (e) {
        toast(t("err_rename", { msg: e.message }), "error");
      }
    }
  );
}

async function deleteNode(node) {
  if (!confirm(t("confirm_delete_node", { name: node.label }))) return;
  try {
    await api("DELETE", "/api/knowledge/nodes", { label: node.label });
    lmState.pos.delete(node.id);
    renderLearnerModel();
    toast(t("toast_node_deleted"), "success");
  } catch (e) {
    toast(t("err_delete", { msg: e.message }), "error");
  }
}

/* ------------------------------------------------------------------ */
/* Generic add/edit modal                                             */
/* ------------------------------------------------------------------ */

let itemModalSave = null;

function openItemModal(title, fields, onSave) {
  $("#item-modal-title").textContent = title;
  const body = $("#item-modal-body");
  body.innerHTML = "";
  const inputs = {};

  for (const f of fields) {
    const label = document.createElement("label");
    label.textContent = f.label;
    let input;
    if (f.type === "select") {
      input = document.createElement("select");
      for (const opt of f.options) {
        const o = document.createElement("option");
        o.value = opt;
        o.textContent = opt;
        input.appendChild(o);
      }
    } else if (f.type === "textarea") {
      input = document.createElement("textarea");
      input.rows = 3;
    } else {
      input = document.createElement("input");
      input.type = f.type || "text";
      if (f.step) input.step = f.step;
      if (f.min !== undefined) input.min = f.min;
      if (f.max !== undefined) input.max = f.max;
    }
    input.id = "item-field-" + f.name;
    if (f.value !== undefined && f.value !== null) input.value = f.value;
    if (f.placeholder) input.placeholder = f.placeholder;
    label.appendChild(input);
    body.appendChild(label);
    inputs[f.name] = input;
  }

  itemModalSave = async () => {
    const values = {};
    for (const f of fields) values[f.name] = inputs[f.name].value;
    await onSave(values);
  };

  $("#item-modal").classList.remove("hidden");
  const first = body.querySelector("input, textarea, select");
  if (first) first.focus();
}

function closeItemModal() {
  $("#item-modal").classList.add("hidden");
  itemModalSave = null;
}

/* ------------------------------------------------------------------ */
/* Settings                                                           */
/* ------------------------------------------------------------------ */

const SETTING_KEYS = [
  "openai_api_key",
  "openai_base_url",
  "dint_model",
  "reflect_model",
  "dint_temperature",
  "max_tool_rounds",
  "web_search_results",
];

async function openSettings() {
  const cfg = await api("GET", "/api/settings");
  for (const key of SETTING_KEYS) {
    const el = $("#set-" + key);
    if (!el) continue;
    if (key === "openai_api_key") {
      el.value = "";
      el.placeholder = cfg.openai_api_key
        ? t("set_current", { val: cfg.openai_api_key })
        : t("set_api_key_ph");
    } else {
      el.value = cfg[key] ?? "";
    }
  }
  $("#settings-status").textContent = "";
  $("#settings-modal").classList.remove("hidden");
}

function closeSettings() {
  $("#settings-modal").classList.add("hidden");
}

async function saveSettings() {
  const payload = {};
  for (const key of SETTING_KEYS) {
    const el = $("#set-" + key);
    if (!el) continue;
    let val = el.value;
    if (key === "openai_api_key" && !val.trim()) continue; // keep existing
    if (
      key === "dint_temperature" ||
      key === "max_tool_rounds" ||
      key === "web_search_results"
    ) {
      val = val === "" ? null : Number(val);
    }
    payload[key] = val;
  }
  const status = $("#settings-status");
  try {
    await api("PUT", "/api/settings", { settings: payload });
    status.textContent = t("saved");
    status.className = "status ok";
    setTimeout(closeSettings, 500);
  } catch (e) {
    status.textContent = "Error: " + e.message;
    status.className = "status err";
  }
}

async function consolidateMemory() {
  if (!confirm(t("confirm_consolidate"))) return;
  const overlay = $("#consolidate-overlay");
  overlay.classList.remove("hidden");
  try {
    const res = await api("POST", "/api/consolidate");
    toast(
      t("toast_consolidated", {
        merged: res.merged || 0,
        removed: res.removed || 0,
      }),
      "success"
    );
    renderLearnerModel();
  } catch (e) {
    toast(t("err_consolidate", { msg: e.message }), "error");
  } finally {
    overlay.classList.add("hidden");
  }
}

async function clearAllData() {
  if (!confirm(t("confirm_reset"))) return;
  try {
    const res = await api("POST", "/api/reset");
    const d = res.deleted || {};
    toast(
      t("toast_cleared", {
        sessions: d.sessions || 0,
        skills: d.skills || 0,
        memory: d.memory || 0,
        concepts: d.knowledge_nodes || 0,
      }),
      "success"
    );
    state.sessionId = null;
    $("#messages").innerHTML = "";
    $("#welcome").classList.remove("hidden");
    lmState.pos.clear();
    await loadSessions();
    renderLearnerModel();
    closeSettings();
  } catch (e) {
    toast(t("err_reset", { msg: e.message }), "error");
  }
}

/* ------------------------------------------------------------------ */
/* Input handling                                                     */
/* ------------------------------------------------------------------ */

function autoresize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

function bindEvents() {
  $("#new-session-btn").onclick = newSession;
  $("#send-btn").onclick = sendMessage;

  const input = $("#user-input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  input.addEventListener("input", () => autoresize(input));

  // Smart auto-scroll: stick to the bottom only while the user is near it.
  const box = $("#messages");
  box.addEventListener("scroll", () => {
    state.autoScroll = nearBottom(box);
  });

  // Escape stops an in-flight stream.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.sending) stopStream();
  });

  // Language toggle.
  const langBtn = $("#lang-btn");
  if (langBtn) {
    langBtn.textContent = i18n.label();
    langBtn.title = t("language");
    langBtn.onclick = () => {
      i18n.cycle();
      // Re-render dynamic text that isn't covered by data-i18n attributes.
      renderSessionList();
      renderLearnerModel();
      setSendStopMode(state.sending);
    };
  }

  // Welcome chips.
  $$(".welcome-chips .chip").forEach((chip) => {
    chip.onclick = () => {
      input.value = chip.textContent;
      autoresize(input);
      sendMessage();
    };
  });

  // Learner model panel.
  $("#toggle-panels-btn").onclick = () => {
    const panels = $("#panels");
    panels.classList.toggle("hidden");
    if (!panels.classList.contains("hidden")) renderLearnerModel();
  };
  $("#lm-close").onclick = () => $("#panels").classList.add("hidden");
  $$(".panel-tabs .tab").forEach((btn) => {
    btn.onclick = () => switchTab(btn.dataset.tab);
  });
  $("#lm-add-btn").onclick = () => {
    if (lmState.tab === "progress") openProgressModal(null);
    else if (lmState.tab === "memory") openMemoryModal(null);
    else if (lmState.tab === "skills") openSkillModal(null);
  };
  $$(".lm-zoom").forEach((btn) => {
    btn.onclick = () => {
      const z = btn.dataset.zoom;
      if (z === "in") zoomBy(1.2);
      else if (z === "out") zoomBy(0.83);
      else resetView();
    };
  });

  // Settings.
  $("#settings-btn").onclick = openSettings;
  $("#settings-close").onclick = closeSettings;
  $("#settings-save").onclick = saveSettings;
  $("#clear-data-btn").onclick = clearAllData;
  const consolidateBtn = $("#consolidate-btn");
  if (consolidateBtn) consolidateBtn.onclick = consolidateMemory;
  $$("#settings-modal .modal-backdrop").forEach((b) => (b.onclick = closeSettings));

  // Item modal.
  $("#item-modal-close").onclick = closeItemModal;
  $("#item-modal-save").onclick = () => {
    if (itemModalSave) itemModalSave();
  };
  $$("#item-modal .modal-backdrop").forEach((b) => (b.onclick = closeItemModal));
}

async function init() {
  // Resolve language and translate static markup before first paint of text.
  i18n.setLang(i18n.detect());
  bindEvents();
  initGraphInteractions();
  await loadSessions();
  if (state.sessions.length) {
    await openSession(state.sessions[0].id);
  } else {
    $("#welcome").classList.remove("hidden");
  }
}

init();
