/* dint – internationalisation (i18n).
 *
 * A tiny, dependency-free, extensible localisation layer.
 *
 * Adding a language:
 *   1. Add a new key to I18N below (e.g. `ja: { ... }`) mirroring the `en`
 *      keys. Missing keys fall back to English automatically.
 *   2. Add the language to LANG_NAMES so it shows up in the toggle.
 *
 * Adding a string:
 *   - Give it a stable dot-free key in every language dictionary.
 *   - In static HTML, tag the element with data-i18n="key" (text),
 *     data-i18n-ph="key" (placeholder) or data-i18n-title="key" (title attr).
 *   - In JS, call t("key") or t("key", { name: value }) for interpolation
 *     using {name} placeholders.
 */

const I18N = {
  en: {
    /* ---- auth ---- */
    "auth_login": "Sign in",
    "auth_register": "Register",
    "auth_username": "Username",
    "auth_password": "Password",
    "auth_signin": "SIGN IN",
    "auth_create": "CREATE ACCOUNT",
    "auth_missing": "Enter a username and password.",
    "auth_failed": "Sign-in failed. Check your credentials.",
    "logout": "SIGN OUT",

    /* ---- sidebar ---- */
    "new_session": "New session",
    "panels": "PANELS",
    "settings": "SETTINGS",
    "no_sessions": "No sessions yet — start one below.",
    "untitled_session": "Untitled session",
    "delete_session": "Delete session",
    "rename_session": "Rename session",

    /* ---- welcome ---- */
    "welcome_text":
      "A Socratic tutor. I don't hand you answers — I ask the questions that lead you there. Tell me what you want to learn and we'll build a plan together.",
    "chip_calculus": "Teach me calculus from scratch",
    "chip_recursion": "Explain recursion like I'm new to coding",
    "chip_history": "Quiz me on world history",

    /* ---- chat ---- */
    "input_placeholder": "Ask me anything…",
    "send": "Send",
    "stop": "Stop",
    "retry": "↻ Retry",
    "retry_title": "Regenerate this response",
    "copy": "Copy",
    "copied": "Copied ✓",
    "copy_title": "Copy message",

    /* ---- learner model ---- */
    "lm_title": "LEARNER MODEL",
    "close": "Close",
    "tab_progress": "PROGRESS",
    "tab_memory": "MEMORY",
    "tab_skills": "SKILLS",
    "tab_knowledge": "KNOWLEDGE",
    "add": "+ ADD",
    "zoom_out": "Zoom out",
    "zoom_reset": "Reset view",
    "zoom_in": "Zoom in",
    "lm_empty": "Nothing here yet.",
    "lm_open_session": "Open a session to see its concept progress.",
    "lm_no_concepts": "No concepts tracked in this session yet.",
    "lm_no_memory": "No long-term memories yet.",
    "lm_no_skills": "No skills estimated yet.",
    "lm_no_knowledge": "Knowledge graph is empty.",
    "stat_concepts": "{n} concept{s}",
    "stat_entries": "{n} entr{y}",
    "stat_skills": "{n} skill{s}",
    "stat_graph": "{nodes} nodes · {edges} edges",
    "status_demonstrated": "demonstrated",
    "status_next": "up next",
    "status_locked": "locked",
    "confidence": "{pct}% confidence",
    "notes": "{n} note{s}",
    "added": "added {when}",
    "edit": "Edit",
    "delete": "Delete",
    "rename": "✎ Rename",
    "delete_node": "✕ Delete",

    /* ---- item modal ---- */
    "modal_edit": "EDIT",
    "save": "SAVE",
    "title_edit_concept": "EDIT CONCEPT",
    "title_add_concept": "ADD CONCEPT",
    "title_edit_memory": "EDIT MEMORY",
    "title_add_memory": "ADD MEMORY",
    "title_edit_skill": "EDIT SKILL",
    "title_add_skill": "ADD SKILL",
    "title_rename_concept": "RENAME CONCEPT",
    "title_rename_session": "RENAME SESSION",
    "field_title": "Title",
    "ph_session_title": "e.g. Calculus — chain rule",
    "err_title_required": "Title required",
    "field_concept": "Concept",
    "field_status": "Status",
    "field_kind": "Kind",
    "field_content": "Content",
    "field_name": "Name",
    "field_domain": "Domain",
    "field_confidence": "Confidence (0–1)",
    "field_new_label": "New label",
    "ph_concept": "e.g. Chain rule",
    "ph_memory": "e.g. Prefers worked examples before exercises",
    "ph_skill": "e.g. Integration by parts",
    "ph_domain": "e.g. Calculus",

    /* ---- settings ---- */
    "settings_title": "SETTINGS",
    "set_api_key": "OpenAI API key",
    "set_base_url": "Base URL",
    "set_tutor_model": "Tutor model",
    "set_reflect_model": "Reflection model",
    "set_temperature": "Temperature",
    "set_max_rounds": "Max tool rounds",
    "set_max_calls": "Max tool calls / turn",
    "set_max_reflect": "Max reflect updates",    
    "set_web_results": "Web results",
    "set_api_key_ph": "sk-…  (leave blank to keep current)",
    "set_current": "current: {val}",
    "danger_zone": "Danger zone",
    "danger_text":
      "Wipe all learner data (sessions, messages, skills, memory, knowledge graph). Settings are kept.",
    "clear_data": "CLEAR ALL DATA",
    "saved": "Saved ✓",

    /* ---- toasts / confirms / errors ---- */
    "err_create_session": "Could not create session: {msg}",
    "err_delete": "Delete failed: {msg}",
    "err_save": "Save failed: {msg}",
    "err_stream": "Stream error: {msg}",
    "err_rename": "Rename failed: {msg}",
    "err_reset": "Reset failed: {msg}",
    "err_load_progress": "Failed to load progress",
    "err_load_memory": "Failed to load memory",
    "err_load_skills": "Failed to load skills",
    "err_load_knowledge": "Failed to load knowledge graph",
    "err_concept_required": "Concept name required",
    "err_content_required": "Content required",
    "err_skill_required": "Skill name required",
    "err_label_required": "Label required",
    "confirm_delete_session": "Delete this session and its messages?",
    "confirm_delete_concept": "Remove concept \"{name}\"?",
    "confirm_delete_memory": "Delete this memory?",
    "confirm_delete_skill": "Delete skill \"{name}\"?",
    "confirm_delete_node": "Delete concept \"{name}\" and its edges?",
    "confirm_reset":
      "This wipes ALL learner data: sessions, messages, skills, memory and the knowledge graph. Settings are kept. Continue?",
    "toast_renamed": "Renamed (edges preserved)",
    "toast_node_deleted": "Node deleted",
    "toast_cleared":
      "Cleared {sessions} sessions, {skills} skills, {memory} memories, {concepts} concepts",
    "consolidate_running": "Consolidating memory…",
    "consolidate_done": "Consolidation complete — {merged} merged, {pruned} pruned",
    "consolidate": "CONSOLIDATE MEMORY",
    "consolidate_text":
      "Merge duplicate / overlapping long-term memories into concise entries.",
    "consolidate_memory": "CONSOLIDATE MEMORY",
    "confirm_consolidate":
      "Merge similar memories and remove stale entries? This cannot be undone.",
    "toast_consolidated": "Consolidated: {merged} merged, {removed} removed",
    "err_consolidate": "Consolidation failed: {msg}",

    /* ---- language ---- */
    "language": "Language",
  },

  zh: {
    /* ---- auth ---- */
    "auth_login": "登录",
    "auth_register": "注册",
    "auth_username": "用户名",
    "auth_password": "密码",
    "auth_signin": "登录",
    "auth_create": "创建账户",
    "auth_missing": "请输入用户名和密码。",
    "auth_failed": "登录失败，请检查凭据。",
    "logout": "退出登录",

    /* ---- sidebar ---- */
    "new_session": "新建会话",
    "panels": "面板",
    "settings": "设置",
    "no_sessions": "还没有会话——在下方开始一个吧。",
    "untitled_session": "未命名会话",
    "delete_session": "删除会话",
    "rename_session": "重命名会话",

    /* ---- welcome ---- */
    "welcome_text":
      "一位苏格拉底式导师。我不会直接给你答案——我会用提问引导你自己找到答案。告诉我你想学什么，我们一起制定学习计划。",
    "chip_calculus": "从零开始教我微积分",
    "chip_recursion": "用新手能懂的方式讲解递归",
    "chip_history": "考我世界历史",

    /* ---- chat ---- */
    "input_placeholder": "随便问点什么…",
    "send": "发送",
    "stop": "停止",
    "retry": "↻ 重试",
    "retry_title": "重新生成此回复",
    "copy": "复制",
    "copied": "已复制 ✓",
    "copy_title": "复制消息",

    /* ---- learner model ---- */
    "lm_title": "学习者模型",
    "close": "关闭",
    "tab_progress": "进度",
    "tab_memory": "记忆",
    "tab_skills": "技能",
    "tab_knowledge": "知识",
    "add": "+ 添加",
    "zoom_out": "缩小",
    "zoom_reset": "重置视图",
    "zoom_in": "放大",
    "lm_empty": "这里还没有内容。",
    "lm_open_session": "打开一个会话以查看其概念进度。",
    "lm_no_concepts": "此会话尚未跟踪任何概念。",
    "lm_no_memory": "还没有长期记忆。",
    "lm_no_skills": "还没有评估任何技能。",
    "lm_no_knowledge": "知识图谱为空。",
    "stat_concepts": "{n} 个概念",
    "stat_entries": "{n} 条记忆",
    "stat_skills": "{n} 项技能",
    "stat_graph": "{nodes} 个节点 · {edges} 条边",
    "status_demonstrated": "已掌握",
    "status_next": "下一个",
    "status_locked": "未解锁",
    "confidence": "置信度 {pct}%",
    "notes": "{n} 条记录",
    "added": "添加于 {when}",
    "edit": "编辑",
    "delete": "删除",
    "rename": "✎ 重命名",
    "delete_node": "✕ 删除",

    /* ---- item modal ---- */
    "modal_edit": "编辑",
    "save": "保存",
    "title_edit_concept": "编辑概念",
    "title_add_concept": "添加概念",
    "title_edit_memory": "编辑记忆",
    "title_add_memory": "添加记忆",
    "title_edit_skill": "编辑技能",
    "title_add_skill": "添加技能",
    "title_rename_concept": "重命名概念",
    "title_rename_session": "重命名会话",
    "field_title": "标题",
    "ph_session_title": "例如：微积分 — 链式法则",
    "err_title_required": "需要标题",
    "field_concept": "概念",
    "field_status": "状态",
    "field_kind": "类型",
    "field_content": "内容",
    "field_name": "名称",
    "field_domain": "领域",
    "field_confidence": "置信度 (0–1)",
    "field_new_label": "新标签",
    "ph_concept": "例如：链式法则",
    "ph_memory": "例如：偏好先看例题再做练习",
    "ph_skill": "例如：分部积分",
    "ph_domain": "例如：微积分",

    /* ---- settings ---- */
    "settings_title": "设置",
    "set_api_key": "OpenAI API 密钥",
    "set_base_url": "基础 URL",
    "set_tutor_model": "导师模型",
    "set_reflect_model": "反思模型",
    "set_temperature": "温度",
    "set_max_rounds": "最大工具轮数",
    "set_max_calls": "每轮最大工具调用数",
    "set_max_reflect": "最大反思更新次数",    
    "set_web_results": "网页结果数",
    "set_api_key_ph": "sk-…（留空则保留当前值）",
    "set_current": "当前：{val}",
    "danger_zone": "危险区域",
    "danger_text":
      "清除所有学习者数据（会话、消息、技能、记忆、知识图谱）。设置将被保留。",
    "clear_data": "清除所有数据",
    "saved": "已保存 ✓",

    /* ---- toasts / confirms / errors ---- */
    "err_create_session": "无法创建会话：{msg}",
    "err_delete": "删除失败：{msg}",
    "err_save": "保存失败：{msg}",
    "err_stream": "流式错误：{msg}",
    "err_rename": "重命名失败：{msg}",
    "err_reset": "重置失败：{msg}",
    "err_load_progress": "加载进度失败",
    "err_load_memory": "加载记忆失败",
    "err_load_skills": "加载技能失败",
    "err_load_knowledge": "加载知识图谱失败",
    "err_concept_required": "需要概念名称",
    "err_content_required": "需要内容",
    "err_skill_required": "需要技能名称",
    "err_label_required": "需要标签",
    "confirm_delete_session": "删除此会话及其消息？",
    "confirm_delete_concept": "移除概念“{name}”？",
    "confirm_delete_memory": "删除这条记忆？",
    "confirm_delete_skill": "删除技能“{name}”？",
    "confirm_delete_node": "删除概念“{name}”及其关联边？",
    "confirm_reset":
      "这将清除所有学习者数据：会话、消息、技能、记忆和知识图谱。设置将被保留。继续？",
    "toast_renamed": "已重命名（边已保留）",
    "toast_node_deleted": "节点已删除",
    "toast_cleared":
      "已清除 {sessions} 个会话、{skills} 项技能、{memory} 条记忆、{concepts} 个概念",
    "consolidate_running": "正在整合记忆…",
    "consolidate_done": "整合完成 — 合并 {merged} 条，修剪 {pruned} 条",
    "consolidate": "整合记忆",
    "consolidate_text": "将重复/重叠的长期记忆合并为简洁条目。",
    "consolidate_memory": "整合记忆",
    "confirm_consolidate": "合并相似记忆并移除过时条目？此操作不可撤销。",
    "toast_consolidated": "整合完成：合并 {merged} 条，移除 {removed} 条",
    "err_consolidate": "整合失败：{msg}",

    /* ---- language ---- */
    "language": "语言",
  },
};

/* Human-readable names for the language toggle. Add an entry per language. */
const LANG_NAMES = {
  en: "EN",
  zh: "中文",
};

const LANG_STORAGE_KEY = "dint.lang";
const FALLBACK_LANG = "en";

const i18n = {
  lang: FALLBACK_LANG,

  /* Resolve the initial language: stored choice > browser language > fallback. */
  detect() {
    const stored = localStorage.getItem(LANG_STORAGE_KEY);
    if (stored && I18N[stored]) return stored;
    const nav = (navigator.language || "en").toLowerCase();
    if (nav.startsWith("zh") && I18N.zh) return "zh";
    return FALLBACK_LANG;
  },

  setLang(lang) {
    if (!I18N[lang]) lang = FALLBACK_LANG;
    this.lang = lang;
    localStorage.setItem(LANG_STORAGE_KEY, lang);
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    applyI18n();
  },

  /* Cycle to the next available language (used by the toggle button). */
  cycle() {
    const langs = Object.keys(I18N);
    const idx = langs.indexOf(this.lang);
    const next = langs[(idx + 1) % langs.length];
    this.setLang(next);
    return next;
  },

  label() {
    return LANG_NAMES[this.lang] || this.lang.toUpperCase();
  },
};

/* Look up a translated string with optional {placeholder} interpolation.
 * Falls back to English, then to the raw key, if a translation is missing. */
function t(key, params) {
  const dict = I18N[i18n.lang] || {};
  let str = dict[key];
  if (str === undefined) str = I18N[FALLBACK_LANG][key];
  if (str === undefined) return key;
  if (params) {
    str = str.replace(/\{(\w+)\}/g, (m, name) =>
      params[name] !== undefined ? params[name] : m
    );
  }
  return str;
}

/* Re-translate every element tagged with a data-i18n* attribute. */
function applyI18n() {
  document
    .querySelectorAll("[data-i18n]")
    .forEach((el) => (el.textContent = t(el.dataset.i18n)));
  document
    .querySelectorAll("[data-i18n-ph]")
    .forEach((el) => (el.placeholder = t(el.dataset.i18nPh)));
  document
    .querySelectorAll("[data-i18n-title]")
    .forEach((el) => (el.title = t(el.dataset.i18nTitle)));
  // Refresh the language toggle label if present.
  const toggle = document.getElementById("lang-btn");
  if (toggle) toggle.textContent = i18n.label();
}

window.i18n = i18n;
window.t = t;
window.applyI18n = applyI18n;