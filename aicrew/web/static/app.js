/* AiCrewStudio — фронтенд: slug-роутинг, дружелюбные формы для агентов. */

// ---------- helpers ---------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k === "on") for (const [evt, fn] of Object.entries(v)) node.addEventListener(evt, fn);
    else if (k.startsWith("data-")) node.setAttribute(k, v);
    else node[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text || res.statusText}`);
  }
  return res.json();
}

const stamp = () => $("#last-update").textContent =
  "обновлено в " + new Date().toLocaleTimeString("ru-RU");

let _toastTimer = null;
function toast(message, kind = "success") {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast show " + kind;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { t.className = "toast"; }, 3500);
}

// ---------- словари ---------------------------------------------------------

const ROLE_RU = {
  topic_generator: "Генератор тем",
  topic_validator: "Проверяльщик тем",
  topic_ranker: "Ранжировщик тем",
  researcher: "Исследователь",
  research_validator: "Проверяльщик исследований",
  article_writer: "Автор статьи",
  headline_writer: "Создатель заголовков",
  image_prompt_writer: "Промт-инженер картинок",
  qa_editorial: "Редакторский QA",
  qa_visual: "Визуальный QA",
  channel_rewriter: "Адаптер для канала",
  video_scenarist: "Сценарист видео",
};

const ROLE_DESC = {
  topic_generator: "Придумывает свежие темы для постов с учётом ниши и истории.",
  topic_validator: "Проверяет темы на достоверность и актуальность через веб-поиск.",
  topic_ranker: "Оценивает темы по 5 критериям и сортирует по рейтингу.",
  researcher: "Собирает факты, цифры и цитаты по утверждённой теме.",
  research_validator: "Перепроверяет факты и переписывает досье с исправлениями.",
  article_writer: "Пишет большую универсальную статью на основе досье.",
  headline_writer: "Генерирует варианты заголовков в разных стилях.",
  image_prompt_writer: "Создаёт промты для генерации иллюстраций к статье.",
  qa_editorial: "Выбирает лучший заголовок, правит текст, ставит оценку.",
  qa_visual: "Выбирает лучшую картинку из сгенерированных вариантов.",
  channel_rewriter: "Адаптирует статью под формат и голос конкретного канала.",
  video_scenarist: "Раскладывает статью на сценарий короткого видео.",
};

const STATUS_RU = {
  queued: "в очереди", running: "выполняется", completed: "завершено", failed: "ошибка",
  generated: "сгенерирована", validated: "проверена", ranked: "отранжирована", written: "написана",
  drafting: "черновик", qa_passed: "готова",
  pending: "ожидает", scheduled: "запланирован", published: "опубликован",
};

const PHASE_RU = { topics: "Темы", articles: "Статьи", publication: "Публикация", full: "Полный цикл" };

const CHANNEL_RU = {
  telegram: { label: "Telegram", icon: "✈", cls: "tg" },
  vk: { label: "ВКонтакте", icon: "VK", cls: "vk" },
  max: { label: "MAX", icon: "M", cls: "vk" },
  facebook: { label: "Facebook", icon: "f", cls: "fb" },
  instagram: { label: "Instagram", icon: "◉", cls: "ig" },
  ok: { label: "Одноклассники", icon: "OK", cls: "ok" },
  threads: { label: "Threads", icon: "@", cls: "x" },
  x: { label: "X (Twitter)", icon: "𝕏", cls: "x" },
  youtube_shorts: { label: "YouTube Shorts", icon: "▶", cls: "yt" },
  tiktok: { label: "TikTok", icon: "♪", cls: "tt" },
  instagram_reels: { label: "Instagram Reels", icon: "◉", cls: "ig" },
  snapchat: { label: "Snapchat", icon: "👻", cls: "yt" },
  vk_video: { label: "VK Видео", icon: "▶", cls: "vk" },
  rutube: { label: "RuTube", icon: "▶", cls: "yt" },
};

const STRATEGY_RU = { by_rank: "по рейтингу", random_among_written: "случайно из написанных" };
const TAB_RU = {
  Pipeline: "Пайплайн", Agents: "Агенты", Topics: "Темы",
  Articles: "Статьи", Channels: "Каналы", Posts: "Публикации", Settings: "Настройки",
};

// ---------- визуальные хелперы ----------------------------------------------

function pill(status) {
  const map = {
    completed: "green", published: "green", qa_passed: "green", written: "green",
    running: "yellow", scheduled: "yellow", drafting: "yellow",
    ranked: "blue", validated: "blue", generated: "gray",
    queued: "gray", pending: "gray", failed: "red",
  };
  return el("span", { class: "pill " + (map[status] || "gray") },
    el("span", { class: "dot" }), STATUS_RU[status] || status);
}

function langTag(l) {
  if (!l) return null;
  if (l === "bi") return el("span", { class: "lang", title: "общий для обеих команд" }, "RU/EN");
  return el("span", { class: "lang " + l,
    title: l === "ru" ? "русская команда" : "английская команда" }, l.toUpperCase());
}

function chIcon(kind) {
  const c = CHANNEL_RU[kind] || { icon: "?", cls: "" };
  return el("div", { class: "ch-ico " + c.cls, title: c.label }, c.icon);
}

function fmtCost(v) {
  const n = Number(v) || 0;
  if (n === 0) return "$0";
  if (n < 0.001) return "<$0.001";
  return "$" + n.toFixed(4);
}

function fmtDate(s) {
  if (!s) return "—";
  try {
    const d = new Date(s.endsWith("Z") ? s : s + "Z");
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit",
      hour: "2-digit", minute: "2-digit" });
  } catch { return s; }
}

function renderMarkdown(md) {
  if (!md) return "";
  return md.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, '<code class="inline">$1</code>')
    .replace(/\n\n/g, "</p><p>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]+?<\/li>)/g, "<ul>$1</ul>")
    .replace(/^/, "<p>") + "</p>";
}

function emptyState(icon, title, text) {
  return el("div", { class: "empty" },
    el("div", { class: "empty-icon" }, icon),
    el("div", { class: "empty-title" }, title),
    el("div", { class: "empty-text" }, text));
}

function loading(text = "Загрузка…") {
  return el("div", { class: "loading" },
    el("div", { class: "spinner" }), el("div", {}, text));
}

// ---------- роутер ----------------------------------------------------------

const router = {
  routes: [],
  add(pattern, handler) { this.routes.push({ pattern, handler }); },
  start() {
    window.addEventListener("hashchange", () => this.dispatch());
    this.dispatch();
  },
  dispatch() {
    const hash = location.hash.slice(1) || "/projects";
    for (const r of this.routes) {
      const params = match(r.pattern, hash);
      if (params !== null) {
        $("#root").innerHTML = "";
        $("#root").append(loading());
        Promise.resolve(r.handler(params)).catch(err => {
          $("#root").innerHTML = "";
          $("#root").append(el("div", { class: "card" },
            el("h2", {}, "Не удалось загрузить страницу"),
            el("div", { class: "muted" }, err.message)));
        });
        renderNav(hash);
        window.scrollTo(0, 0);
        return;
      }
    }
    $("#root").innerHTML = "";
    $("#root").append(el("div", { class: "card" }, el("h2", {}, "Страница не найдена")));
  },
};

function match(pattern, path) {
  const pp = pattern.split("/").filter(Boolean);
  const ap = path.split("/").filter(Boolean);
  if (pp.length !== ap.length) return null;
  const out = {};
  for (let i = 0; i < pp.length; i++) {
    if (pp[i].startsWith(":")) out[pp[i].slice(1)] = ap[i];
    else if (pp[i] !== ap[i]) return null;
  }
  return out;
}

function renderNav(active) {
  const links = [
    ["#/projects", "Проекты"],
    ["#/about", "О системе"],
  ];
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const [href, label] of links) {
    const a = el("a", { href, class: active.startsWith(href.slice(1)) ? "active" : "" }, label);
    nav.append(a);
  }
}

// ---------- список проектов -------------------------------------------------

router.add("/projects", async () => {
  const root = $("#root");
  root.innerHTML = "";
  root.append(el("div", { class: "page-title" }, el("h1", {}, "Проекты")));
  root.append(el("div", { class: "page-subtitle" },
    "Каждый проект — отдельный канал/блог со своей командой ИИ-агентов."));
  const data = await api("/api/projects");
  const grid = el("div", { class: "grid grid-3" });
  if (!data.projects.length) {
    grid.append(emptyState("📂", "Проектов пока нет",
      "Запустите команду `make seed` на сервере, чтобы создать демо-проект."));
  }
  for (const p of data.projects) {
    const langs = JSON.parse(p.language_modes || "[]");
    grid.append(el("a", { href: `#/projects/${p.slug || p.id}`,
      style: "text-decoration:none;color:inherit" },
      el("div", { class: "card card-hover" },
        el("div", { class: "row between" },
          el("h2", { style: "margin:0" }, p.name),
          p.is_enabled ? pill("running") : pill("scheduled")),
        el("div", { class: "muted", style: "font-size:13px;margin:6px 0 12px 0" }, p.niche || "—"),
        el("div", { class: "row" }, ...langs.map(l => langTag(l))),
        el("div", { class: "spacer" }),
        el("div", { class: "kv" },
          el("div", {}, "Тем в день"), el("div", {}, p.daily_topics_target),
          el("div", {}, "Статей в день"), el("div", {}, p.daily_articles_target),
          el("div", {}, "Бюджет"), el("div", {}, "$" + p.budget_usd_month + " / мес"))
      )));
  }
  root.append(grid);
  stamp();
});

// ---------- карточка проекта (slug) -----------------------------------------

router.add("/projects/:pkey", async ({ pkey }) => loadProject(pkey, "Pipeline"));
router.add("/projects/:pkey/tab/:tab", async ({ pkey, tab }) => {
  const map = { pipeline:"Pipeline", agents:"Agents", topics:"Topics",
    articles:"Articles", channels:"Channels", posts:"Posts", settings:"Settings" };
  return loadProject(pkey, map[tab.toLowerCase()] || "Pipeline");
});

async function loadProject(pkey, initialTab) {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pkey}`);
  const p = data.project;
  const slug = p.slug || p.id;

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, p.name),
      el("div", { class: "muted", style: "margin-top:4px" }, p.description || "Без описания")),
    el("div", { class: "row" },
      el("button", { class: "ghost sm", on: { click: () => runPhase(slug, "topics") } }, "Темы"),
      el("button", { class: "ghost sm", on: { click: () => runPhase(slug, "articles") } }, "Статьи"),
      el("button", { class: "ghost sm", on: { click: () => runPhase(slug, "publish") } }, "Публикация"),
      el("button", { on: { click: () => runPhase(slug, "full") } }, "▶ Полный цикл"))));

  const tabs = ["Pipeline", "Agents", "Topics", "Articles", "Channels", "Posts", "Settings"];
  const counts = {
    Agents: data.agents.length, Topics: data.topics.length,
    Articles: data.articles.length, Channels: data.channels.length,
  };
  const tabbar = el("div", { class: "tabs" });
  const view = el("div", {});
  let active = initialTab && tabs.includes(initialTab) ? initialTab : "Pipeline";
  for (const t of tabs) {
    // Каждая вкладка — отдельный hash-маршрут. Pipeline — корневой URL
    // проекта (#/projects/<slug>), остальные — под /tab/<name>. Так
    // браузерная стрелка «Назад» корректно перемещается между вкладками.
    const href = t === "Pipeline"
      ? `#/projects/${slug}`
      : `#/projects/${slug}/tab/${t.toLowerCase()}`;
    const node = el("a", {
      class: "tab",
      href,
      style: "text-decoration:none;color:inherit",
    }, TAB_RU[t], counts[t] != null ? el("span", { class: "count" }, " · " + counts[t]) : null);
    tabbar.append(node);
  }
  root.append(tabbar);
  root.append(view);

  function render() {
    view.innerHTML = "";
    [...tabbar.children].forEach((node, i) => node.classList.toggle("active", tabs[i] === active));
    if (active === "Pipeline") view.append(renderPipelineTab(data));
    if (active === "Agents") view.append(renderAgentsTab(slug, data.agents));
    if (active === "Topics") view.append(renderTopicsTab(slug, data.topics));
    if (active === "Articles") view.append(renderArticlesTab(slug, data.articles));
    if (active === "Channels") view.append(renderChannelsTab(slug, data.channels));
    if (active === "Posts") view.append(renderPostsTab(slug));
    if (active === "Settings") view.append(renderSettingsTab(slug, p));
  }
  render();
  stamp();
}

async function runPhase(slug, phase) {
  const map = { topics: "runs/topics", articles: "runs/articles",
    publish: "runs/publish", full: "runs/full" };
  const labels = { topics: "Запускаю генерацию тем…", articles: "Запускаю написание статей…",
    publish: "Запускаю публикацию…", full: "Запускаю полный цикл…" };
  toast(labels[phase] || "Запуск…");
  try {
    await api(`/api/projects/${slug}/${map[phase]}`, { method: "POST", body: {} });
    // Полный цикл и любые тяжёлые фазы запускаются в фоне на сервере —
    // соединение возвращает 202 сразу. Сообщаем пользователю и обновляем
    // страницу через 5 секунд, чтобы успели обновиться счётчики/запуски.
    if (phase === "full") {
      toast("Запущено в фоне, обновится через 2–3 минуты", "success");
    } else {
      toast("Запущено в фоне, обновится автоматически", "success");
    }
    setTimeout(() => location.reload(), 5000);
  } catch (e) { toast("Ошибка: " + e.message, "error"); }
}

// ---------- вкладка: пайплайн -----------------------------------------------

function renderPipelineTab(data) {
  const wrap = el("div", { class: "grid grid-2" });
  const phases = [
    { title: "1. Сбор тем", icon: "🔎", items: [
      "Генератор предлагает темы по нише",
      "Проверяльщик уточняет факты и расширяет описание",
      "Если тем мало — цикл повторяется",
      "Ранжировщик сортирует по 5 критериям"]},
    { title: "2. Написание статей · Русская команда", icon: "🇷🇺", items: [
      "Исследователь собирает факты",
      "Проверяльщик исследований правит ошибки",
      "Автор пишет универсальную статью",
      "Создатель заголовков · Промт картинок · QA"]},
    { title: "2. Написание статей · Английская команда", icon: "🇬🇧", items: [
      "Researcher gathers facts",
      "Validator fixes errors in the brief",
      "Writer composes the long article",
      "Headlines · Image prompts · QA"]},
    { title: "3. Публикация", icon: "📤", items: [
      "Адаптер канала переписывает текст под формат",
      "Готовый пост публикуется через адаптер канала",
      "Видео-каналы пока пропускаются (на этапе подключения видео-команды)"]},
  ];
  for (const ph of phases) {
    wrap.append(el("div", { class: "card" },
      el("h2", {}, ph.icon + "  " + ph.title),
      el("ul", { style: "margin:0;padding-left:20px;font-size:13.5px;line-height:1.7;color:var(--text-dim)" },
        ...ph.items.map(it => el("li", {}, it)))));
  }
  const runsCard = el("div", { class: "card" }, el("h2", {}, "Последние запуски"),
    data.pipeline_runs.length === 0
      ? el("div", { class: "muted" }, "Запусков ещё не было.")
      : el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "Тип"), el("th", {}, "Статус"),
          el("th", {}, "Начало"), el("th", {}, "Окончание"))),
        el("tbody", {}, ...data.pipeline_runs.map(r => el("tr", {},
          el("td", {}, PHASE_RU[r.kind] || r.kind),
          el("td", {}, pill(r.status)),
          el("td", { class: "muted" }, fmtDate(r.started_at)),
          el("td", { class: "muted" }, fmtDate(r.finished_at)))))));
  return el("div", {}, wrap, el("div", { class: "spacer" }), runsCard);
}

// ---------- вкладка: агенты -------------------------------------------------

function renderAgentsTab(projectSlug, agents) {
  if (!agents.length)
    return emptyState("🤖", "Агентов нет", "Запустите seed для создания команды.");
  const groups = { bi: [], ru: [], en: [] };
  for (const a of agents) {
    if (a.role === "channel_rewriter") continue;
    (groups[a.language] || groups.bi).push(a);
  }
  const rewriters = agents.filter(a => a.role === "channel_rewriter");

  const card = (title, subtitle, list) => {
    if (!list.length) return null;
    const grid = el("div", { class: "grid grid-2" });
    for (const a of list) grid.append(agentCard(projectSlug, a));
    return el("div", { class: "card-section" },
      el("div", { style: "display:flex;align-items:baseline;gap:10px;margin-bottom:14px" },
        el("h2", { style: "margin:0" }, title),
        el("span", { class: "muted", style: "font-size:13px" }, subtitle)),
      grid);
  };
  return el("div", {},
    card("Общие агенты", "одни на весь проект", groups.bi),
    card("Русская команда редакторов", "пишет на русском", groups.ru),
    card("Английская команда редакторов", "пишет на английском", groups.en),
    card("Адаптеры под каналы", "по одному на каждый текстовый канал", rewriters));
}

function agentCard(projectSlug, a) {
  const slug = a.slug || a.id;
  // For channel_rewriter the role label "Адаптер для канала" is the same
  // for every channel, so we show the per-channel display_name instead
  // (seed.py builds it as "Адаптер — Задним числом · Telegram" etc.).
  const headline = (a.role === "channel_rewriter" && a.display_name)
    ? a.display_name
    : (ROLE_RU[a.role] || a.display_name);
  return el("a", { href: `#/projects/${projectSlug}/agents/${slug}`,
      style: "text-decoration:none;color:inherit" },
    el("div", { class: "card card-hover" },
      el("div", { class: "row between" },
        el("h2", { style: "margin:0;font-size:15px" }, headline),
        el("div", { class: "row" }, langTag(a.language))),
      el("div", { class: "muted", style: "font-size:13px;margin:6px 0 12px 0" },
        ROLE_DESC[a.role] || a.description),
      el("div", { class: "kv" },
        el("div", {}, "Модель"), el("div", {}, el("code", { class: "inline" }, a.model)),
        el("div", {}, "Температура"), el("div", {}, a.temperature),
        el("div", {}, "Лимит токенов"), el("div", {}, a.max_tokens)),
      el("div", { class: "spacer" }),
      el("div", { style: "color:var(--accent);font-size:13px;font-weight:600" },
        "Открыть и настроить →")));
}

// ---------- вкладка: темы ---------------------------------------------------

function renderTopicsTab(projectSlug, topics) {
  if (!topics.length)
    return emptyState("💡", "Тем пока нет", "Нажмите «Темы» наверху, чтобы запустить агентов сбора тем.");
  return el("div", { class: "card" }, el("h2", {}, "Темы (" + topics.length + ")"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Тема"), el("th", {}, "Дата события"), el("th", {}, "Статус"),
        el("th", {}, "Рейтинг"), el("th", {}, "Оценки"), el("th", {}, ""))),
      el("tbody", {}, ...topics.map(t => {
        const scores = JSON.parse(t.scores || "{}");
        return el("tr", { class: "row-hover",
            on: { click: () => location.hash =
              `#/projects/${projectSlug}/topics/${t.id}` } },
          el("td", { style: "max-width:520px;font-weight:500" }, t.title),
          el("td", { class: "muted", style: "font-size:13px;white-space:nowrap" },
            t.event_date || "—"),
          el("td", {}, pill(t.status)),
          el("td", {}, el("b", {}, Number(t.score_total).toFixed(1))),
          el("td", { class: "muted", style: "font-size:12px" },
            Object.entries(scores).map(([k, v]) => `${k}: ${v}`).join(" · ")),
          el("td", { style: "color:var(--accent);font-weight:600" }, "Открыть →"));
      }))));
}

// ---------- вкладка: статьи -------------------------------------------------

function renderArticlesTab(projectSlug, articles) {
  if (!articles.length)
    return emptyState("📝", "Статей пока нет", "Сначала запустите сбор тем, потом — написание статей.");
  return el("div", { class: "card" }, el("h2", {}, "Статьи (" + articles.length + ")"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Заголовок"), el("th", {}, "Язык"),
        el("th", {}, "Статус"), el("th", {}, "QA"),
        el("th", {}, "Создана"), el("th", {}, ""))),
      el("tbody", {}, ...articles.map(a => el("tr", {},
        el("td", { style: "max-width:480px" },
          a.chosen_headline || el("span", { class: "muted" }, "—")),
        el("td", {}, langTag(a.language)),
        el("td", {}, pill(a.status)),
        el("td", {}, a.qa_score || "—"),
        el("td", { class: "muted" }, fmtDate(a.created_at)),
        el("td", {}, el("a", { href: `#/projects/${projectSlug}/articles/${a.id}` },
          "Открыть →")))))));
}

// ---------- вкладка: каналы -------------------------------------------------

function renderChannelsTab(projectSlug, channels) {
  if (!channels.length)
    return emptyState("📡", "Каналов пока нет", "Каналы добавляются через seed или вручную.");
  // API уже отсортировал каналы — подключённые (с заполненными
  // credentials_enc) идут первыми, остальные ниже. Здесь просто
  // отрисовываем две секции с заголовком, чтобы пользователь сразу видел
  // где у него боевые каналы, а где ещё пусто.
  const connected = channels.filter(c => c.is_connected);
  const pending = channels.filter(c => !c.is_connected);
  function channelCard(c) {
    const meta = CHANNEL_RU[c.kind] || { label: c.kind };
    const slug = c.slug || c.id;
    return el("a", { href: `#/projects/${projectSlug}/channels/${slug}`,
        style: "text-decoration:none;color:inherit" },
      el("div", { class: "card card-hover" },
        el("div", { class: "row between" },
          el("div", { class: "row" }, chIcon(c.kind),
            el("div", {},
              el("div", { style: "font-weight:600;font-size:15px" }, c.name),
              el("div", { class: "muted", style: "font-size:12px" }, meta.label))),
          el("div", { class: "row" }, langTag(c.language),
            c.is_video ? el("span", { class: "pill blue" }, "видео")
                       : el("span", { class: "pill gray" }, "текст"),
            c.is_connected ? el("span", { class: "pill green" },
              el("span", { class: "dot" }), "подключён")
                           : el("span", { class: "pill gray" },
              el("span", { class: "dot" }), "не подключён"))),
        el("div", { class: "spacer" }),
        el("div", { class: "kv" },
          el("div", {}, "Постов в день"), el("div", {}, c.posts_per_day),
          el("div", {}, "Стратегия"),
          el("div", {}, STRATEGY_RU[c.selection_strategy] || c.selection_strategy)),
        el("div", { class: "spacer" }),
        el("div", { style: "color:var(--accent);font-size:13px;font-weight:600" },
          c.is_connected ? "Управлять каналом →"
                         : "Подключить и настроить →")));
  }
  function section(title, subtitle, list) {
    if (!list.length) return null;
    const grid = el("div", { class: "grid grid-2" });
    list.forEach(c => grid.append(channelCard(c)));
    return el("div", { class: "card-section" },
      el("div", { style: "display:flex;align-items:baseline;gap:10px;margin-bottom:14px" },
        el("h2", { style: "margin:0" }, title),
        el("span", { class: "muted", style: "font-size:13px" }, subtitle)),
      grid);
  }
  return el("div", {},
    section("Подключённые каналы", "токены сохранены, готовы к публикации",
      connected),
    section("Не подключённые", "нужно ввести токены или ключи API",
      pending));
}

// ---------- вкладка: посты --------------------------------------------------

function renderPostsTab(projectSlug) {
  const wrap = el("div", { class: "card" }, el("h2", {}, "Публикации"), loading());
  api(`/api/projects/${projectSlug}/posts`).then(({ posts }) => {
    wrap.innerHTML = "";
    wrap.append(el("h2", {}, "Публикации (" + posts.length + ")"));
    if (!posts.length) {
      wrap.append(emptyState("📤", "Публикаций пока нет", "Запустите фазу публикации."));
      return;
    }
    async function publishNowRow(postId) {
      try {
        const resp = await api(`/api/posts/${postId}/publish_now`, { method: "POST" });
        if (resp.ok) toast("Опубликовано");
        else toast("Не удалось — повторим автоматически", "error");
        setTimeout(() => location.reload(), 800);
      } catch (e) {
        toast("Ошибка: " + e.message, "error");
      }
    }
    wrap.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Канал"), el("th", {}, "Язык"), el("th", {}, "Статус"),
        el("th", {}, "Когда"),
        el("th", {}, "Заголовок"), el("th", {}, "Ссылка"))),
      el("tbody", {}, ...posts.map(p => el("tr", {},
        el("td", {}, el("div", { class: "row" }, chIcon(p.channel_kind), p.channel_name)),
        el("td", {}, langTag(p.language)),
        el("td", {}, pill(p.status)),
        // «Когда»: для опубликованных — реальное время отправки, для
        // запланированных — scheduled_for. Под scheduled добавляем
        // маленькую ссылку «опубликовать сейчас», которая бьёт в
        // POST /api/posts/{id}/publish_now и принудительно отправляет
        // пост, не дожидаясь слота. Полезно для отладки расписания.
        el("td", { class: "muted", style: "font-size:12px;white-space:nowrap" },
          p.status === "published"
            ? fmtDate(p.published_at)
            : el("div", {},
                fmtDate(p.scheduled_for),
                p.status === "scheduled" || p.status === "pending"
                  ? el("div", {},
                      el("a", { href: "#", style: "font-size:11px",
                        on: { click: (e) => {
                          e.preventDefault();
                          publishNowRow(p.id);
                        } } }, "опубликовать сейчас"))
                  : null)),
        el("td", { style: "max-width:380px" }, p.article_headline || ""),
        el("td", {}, p.external_url
          ? el("a", { href: p.external_url, target: "_blank" }, "открыть ↗")
          : el("span", { class: "muted" }, "—")))))));
  }).catch(e => {
    wrap.innerHTML = "";
    wrap.append(el("div", { class: "muted" }, "Ошибка: " + e.message));
  });
  return wrap;
}

// ---------- вкладка: настройки проекта --------------------------------------

function renderSettingsTab(slug, p) {
  function f(name, label, value, type = "text", hint = "") {
    return el("label", { class: "field" }, label,
      el("input", { type, value: value ?? "", "data-name": name }),
      hint ? el("span", { class: "hint" }, hint) : null);
  }
  function fArea(name, label, value, hint = "") {
    return el("label", { class: "field" }, label,
      el("textarea", { value: value ?? "", "data-name": name, style: "min-height:100px" }),
      hint ? el("span", { class: "hint" }, hint) : null);
  }
  const card = el("div", { class: "card" },
    el("h2", {}, "⚙️ Настройки проекта"),
    f("name", "Название проекта", p.name),
    f("niche", "Ниша / тематика", p.niche, "text",
      "Темы агентов будут привязаны к этой нише."),
    fArea("description", "Описание проекта", p.description,
      "Краткое описание — кто аудитория, какой тон."),
    fArea("style_guide", "Стайл-гайд (тон голоса)", p.style_guide,
      "Описание стиля подачи: живой, экспертный, без воды и т.д."),
    f("daily_topics_target", "Тем за один запуск", p.daily_topics_target, "number",
      "Сколько тем генератор должен предложить за один прогон."),
    f("daily_articles_target", "Статей за один запуск", p.daily_articles_target, "number",
      "Сколько лучших тем из рейтинга берётся для написания статей."),
    f("budget_usd_month", "Бюджет ($/мес)", p.budget_usd_month, "number",
      "Лимит расходов на API моделей."),
    el("div", { class: "spacer" }),
    el("div", { class: "row" },
      el("button", { on: { click: saveProject } }, "💾 Сохранить"),
      el("button", { class: "ghost", on: { click: () => location.reload() } }, "Отменить")));
  async function saveProject() {
    const body = {};
    for (const k of ["name", "niche", "description", "style_guide",
        "daily_topics_target", "daily_articles_target", "budget_usd_month"]) {
      const inp = card.querySelector(`[data-name="${k}"]`);
      if (!inp) continue;
      const v = inp.value;
      body[k] = ["daily_topics_target", "daily_articles_target"].includes(k)
        ? parseInt(v, 10)
        : k === "budget_usd_month" ? parseFloat(v) : v;
    }
    try {
      await api(`/api/projects/${slug}`, { method: "PATCH", body });
      toast("Настройки проекта сохранены", "success");
    } catch (e) { toast("Ошибка: " + e.message, "error"); }
  }
  return card;
}



// ============================================================================
// Дружелюбный рендер параметров агента по param_schema
// ============================================================================

function renderParamField(field, currentValue, formRef) {
  const value = currentValue !== undefined ? currentValue : field.default;
  const wrap = el("label", { class: "field" }, field.label);

  if (field.type === "bool") {
    const checkbox = el("input", { type: "checkbox", "data-pkey": field.key,
      style: "width:auto;margin-right:8px;vertical-align:middle" });
    checkbox.checked = !!value;
    const line = el("div", { style: "display:flex;align-items:center" },
      checkbox,
      el("span", { style: "font-size:14px" },
        value ? "включено" : "выключено"));
    checkbox.addEventListener("change", () => {
      line.lastChild.textContent = checkbox.checked ? "включено" : "выключено";
    });
    wrap.append(line);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "int" || field.type === "float") {
    const inp = el("input", {
      type: "number",
      value: value ?? field.default ?? "",
      "data-pkey": field.key,
      "data-ptype": field.type,
      step: field.step != null ? String(field.step) : (field.type === "float" ? "0.1" : "1"),
      min: field.min != null ? String(field.min) : null,
      max: field.max != null ? String(field.max) : null,
    });
    wrap.append(inp);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "text") {
    const inp = el("textarea", {
      value: value ?? "", "data-pkey": field.key, "data-ptype": "text",
      style: "min-height:80px",
    });
    wrap.append(inp);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "string") {
    const inp = el("input", { type: "text", value: value ?? "",
      "data-pkey": field.key, "data-ptype": "string" });
    wrap.append(inp);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "select") {
    const sel = el("select", { "data-pkey": field.key, "data-ptype": "select" });
    for (const opt of field.options || []) {
      const o = el("option", { value: opt.value }, opt.label);
      if (opt.value === value) o.selected = true;
      sel.append(o);
    }
    wrap.append(sel);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "multi_select") {
    const sel = new Set(Array.isArray(value) ? value : []);
    const grid = el("div", { class: "checkbox-grid",
      "data-pkey": field.key, "data-ptype": "multi_select" });
    for (const opt of field.options || []) {
      const cb = el("input", { type: "checkbox", value: opt.value,
        style: "width:auto;margin-right:6px" });
      cb.checked = sel.has(opt.value);
      grid.append(el("label", { class: "checkbox-row" }, cb,
        el("span", {}, opt.label)));
    }
    wrap.append(grid);
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  if (field.type === "weights") {
    const obj = (value && typeof value === "object") ? value : {};
    const grid = el("div", { class: "weights-grid",
      "data-pkey": field.key, "data-ptype": "weights" });
    const sumLabel = el("span", { class: "weights-sum" });
    function recalc() {
      let total = 0;
      for (const inp of grid.querySelectorAll("input[type=number]"))
        total += parseFloat(inp.value || 0) || 0;
      sumLabel.textContent = "Сумма: " + total.toFixed(2);
      sumLabel.className = "weights-sum " + (Math.abs(total - 1) < 0.01 ? "ok" : "warn");
    }
    for (const opt of field.options || []) {
      const inp = el("input", { type: "number", step: "0.05", min: "0", max: "1",
        value: obj[opt.key] != null ? obj[opt.key] : 0,
        "data-wkey": opt.key });
      inp.addEventListener("input", recalc);
      grid.append(el("div", { class: "weight-row" },
        el("span", { class: "weight-label" }, opt.label),
        inp));
    }
    wrap.append(grid);
    wrap.append(sumLabel);
    recalc();
    if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
    return wrap;
  }

  // fallback: текст
  const inp = el("input", { type: "text", value: String(value ?? ""),
    "data-pkey": field.key, "data-ptype": "string" });
  wrap.append(inp);
  if (field.hint) wrap.append(el("span", { class: "hint" }, field.hint));
  return wrap;
}

function collectParamsFromForm(formNode, schema) {
  /** Собирает params из формы согласно схеме. */
  const out = {};
  for (const field of schema) {
    const node = formNode.querySelector(`[data-pkey="${field.key}"]`);
    if (!node) continue;
    if (field.type === "bool") {
      out[field.key] = !!node.checked;
    } else if (field.type === "int") {
      out[field.key] = parseInt(node.value, 10);
    } else if (field.type === "float") {
      out[field.key] = parseFloat(node.value);
    } else if (field.type === "select") {
      out[field.key] = node.value;
    } else if (field.type === "multi_select") {
      out[field.key] = [...node.querySelectorAll("input[type=checkbox]:checked")]
        .map(cb => cb.value);
    } else if (field.type === "weights") {
      const obj = {};
      for (const inp of node.querySelectorAll("input[type=number]"))
        obj[inp.dataset.wkey] = parseFloat(inp.value) || 0;
      out[field.key] = obj;
    } else {
      out[field.key] = node.value;
    }
  }
  return out;
}

// ============================================================================
// Карточка агента (по slug)
// ============================================================================

router.add("/projects/:pkey/agents/:akey", async ({ pkey, akey }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pkey}/agents/${akey}`);
  const a = data.agent;
  const project = data.project;
  const schema = data.param_schema || [];
  let currentParams = {};
  try { currentParams = JSON.parse(a.params); } catch {}

  // Хлебные крошки
  root.append(el("div", { class: "breadcrumbs" },
    el("a", { href: "#/projects" }, "Проекты"), " / ",
    el("a", { href: `#/projects/${project.slug || project.id}` }, project.name), " / ",
    el("span", {}, "Агент: " + (ROLE_RU[a.role] || a.display_name))));

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, ROLE_RU[a.role] || a.display_name),
      el("div", { class: "muted", style: "margin-top:4px" },
        ROLE_DESC[a.role] || a.description)),
    el("div", { class: "row" },
      langTag(a.language),
      el("span", { class: "chip" }, a.role))));

  const promptHint = a.language === "en"
    ? "Промт английской команды должен быть на английском — он отправляется LLM как есть."
    : a.language === "ru"
      ? "Промт русской команды должен быть на русском."
      : "Этот агент общий для обеих команд — промт сам адаптируется по {{ language }}.";

  // --- Основные настройки (общие) ---
  const basicCard = el("div", { class: "card" },
    el("h2", {}, "🔧 Основные настройки"),
    field("display_name", "Название агента", a.display_name),
    field("model", "Модель GPT", a.model, "text", {},
      "Например: openai:gpt-4o, openai:gpt-4o-mini, openai:gpt-5"),
    field("temperature", "Температура (творческость)", a.temperature, "number",
      { step: "0.1", min: "0", max: "2" },
      "0 — детерминированно, 1 — творчески, 2 — хаос."),
    field("max_tokens", "Лимит токенов в ответе", a.max_tokens, "number", {},
      "Сколько максимум токенов агент может сгенерировать за один вызов."));

  // --- Параметры роли (дружелюбные поля) ---
  const roleParamsCard = schema.length === 0 ? null : el("div", { class: "card" },
    el("h2", {}, "🎛 Параметры роли"),
    el("div", { class: "muted",
      style: "font-size:13px;margin-bottom:14px;line-height:1.5" },
      "Это специфичные настройки именно для этой роли агента. " +
      "Все они ниже — обычные поля, не нужно знать JSON."));
  if (schema.length > 0) {
    for (const f of schema) {
      roleParamsCard.append(renderParamField(f, currentParams[f.key], roleParamsCard));
    }
  }

  // --- Поиск в интернете (общий блок для любой роли) ---
  // Поиск включён ⇔ params.search_query_template — непустая строка. Когда
  // галочка стоит, показываем поля для шаблона запроса, глубины и числа
  // результатов. Когда галочка снимается, шаблон обнуляется и executor
  // пропускает Tavily-вызов на этом агенте. Этот блок намеренно живёт
  // отдельно от param_schema, чтобы не дублировать его в каждой роли.
  const searchOn = !!(currentParams.search_query_template
                      && currentParams.search_query_template.trim());
  const searchTplInp = el("input", { type: "text",
    value: currentParams.search_query_template || "",
    placeholder: "{{ topic.title }} {{ today_md }}",
    "data-name": "search_query_template" });
  const searchDepthSel = el("select", { "data-name": "search_depth" },
    el("option", { value: "basic",
      selected: (currentParams.search_depth || "basic") === "basic" },
      "basic — 1 кредит Tavily"),
    el("option", { value: "advanced",
      selected: currentParams.search_depth === "advanced" },
      "advanced — 2 кредита, глубже"));
  const searchMaxInp = el("input", { type: "number",
    value: currentParams.search_max_results || 5, min: "1", max: "20",
    "data-name": "search_max_results" });
  const searchFields = el("div", { class: "search-fields",
    style: searchOn ? "" : "display:none" },
    el("label", { class: "field" }, "Шаблон поискового запроса",
      searchTplInp,
      el("span", { class: "hint" },
        "Mini-Jinja: можно вставлять {{ project.niche }}, {{ topic.title }}, " +
        "{{ today_md }}, {{ language }}. Например для исследователя: " +
        "{{ topic.title }} {{ topic.event_date }}.")),
    el("label", { class: "field" }, "Глубина поиска",
      searchDepthSel,
      el("span", { class: "hint" },
        "advanced даёт более длинные сниппеты, но стоит вдвое больше кредитов.")),
    el("label", { class: "field" }, "Сколько результатов брать",
      searchMaxInp,
      el("span", { class: "hint" },
        "Эти результаты подаются в LLM перед промтом как блок «WEB SEARCH RESULTS».")));
  const searchToggle = el("input", { type: "checkbox",
    style: "width:auto;margin-right:8px;vertical-align:middle" });
  searchToggle.checked = searchOn;
  const searchToggleLabel = el("span", { style: "font-size:14px" },
    searchOn ? "включён" : "выключен");
  searchToggle.addEventListener("change", () => {
    searchToggleLabel.textContent = searchToggle.checked ? "включён" : "выключен";
    searchFields.style.display = searchToggle.checked ? "" : "none";
    // Если включаем впервые и шаблон пуст — подсказываем дефолт по роли.
    if (searchToggle.checked && !searchTplInp.value.trim()) {
      const presets = {
        topic_generator: "{{ project.niche }} {{ today_md }} {{ language }}",
        topic_validator: "{{ topic.title }} {{ topic.event_date }}",
        researcher:      "{{ topic.title }} {{ topic.event_date }}",
        research_validator: "{{ topic.title }}",
      };
      searchTplInp.value = presets[a.role] || "{{ topic.title }}";
    }
  });
  const searchCard = el("div", { class: "card" },
    el("h2", {}, "🔍 Поиск в интернете"),
    el("div", { class: "muted",
      style: "font-size:13px;margin-bottom:14px;line-height:1.5" },
      "Если включено — перед вызовом LLM агент сделает один веб-поиск " +
      "через Tavily и подсунет свежие результаты в промт. Полезно для " +
      "ролей, которым нужны актуальные факты (генератор тем, " +
      "исследователь). Для редактора/QA — не нужно."),
    el("div", { style: "display:flex;align-items:center;margin-bottom:14px" },
      searchToggle, searchToggleLabel),
    searchFields);


  // --- Промт (большой текстарей) ---
  const promptCard = el("div", { class: "card" },
    el("h2", {}, "📝 Промт агента"),
    el("div", { class: "muted",
      style: "font-size:13px;margin-bottom:10px;line-height:1.5" },
      promptHint),
    fieldArea("prompt_template", "", a.prompt_template, "prompt_template", null));

  // --- Расширенные настройки (JSON для опытных) ---
  const advancedCard = el("details", { class: "card details-card" },
    el("summary", {}, "🔬 Расширенные настройки (JSON)"),
    el("div", { class: "muted",
      style: "font-size:13px;margin:10px 0;line-height:1.5" },
      "Только для опытных пользователей. Здесь можно увидеть и поправить параметры в виде JSON. " +
      "Если меняете в форме выше — здесь обновится автоматически."),
    fieldArea("params_json", "", JSON.stringify(currentParams, null, 2),
      "params_json", null));

  // --- Кнопки ---
  const buttonsCard = el("div", { class: "card" },
    el("div", { class: "row" },
      el("button", { on: { click: save } }, "💾 Сохранить все изменения"),
      el("button", { class: "ghost", on: { click: () => location.reload() } }, "Отменить")));

  async function save() {
    const body = {};
    // Основные
    for (const k of ["display_name", "model", "temperature", "max_tokens"]) {
      const v = basicCard.querySelector(`[data-name="${k}"]`).value;
      body[k] = (k === "temperature") ? Number(v)
              : (k === "max_tokens" ? parseInt(v, 10) : v);
    }
    // Промт
    body.prompt_template = promptCard.querySelector('[data-name="prompt_template"]').value;
    // Параметры: сначала из формы, потом JSON-расширение перекрывает
    let params = {};
    if (roleParamsCard && schema.length > 0) {
      params = collectParamsFromForm(roleParamsCard, schema);
    }
    // Search settings: чекбокс перетирает search_query_template поверх
    // того, что собрано из роли. Снят флажок — пишем "" чтобы поиск
    // выключился; стоит — берём шаблон/глубину/кол-во из формы.
    if (searchToggle.checked) {
      params.search_query_template = searchTplInp.value.trim();
      params.search_depth = searchDepthSel.value;
      params.search_max_results = parseInt(searchMaxInp.value, 10) || 5;
    } else {
      params.search_query_template = "";
    }
    // JSON может перекрывать (если опытный пользователь там что-то добавил)
    const jsonRaw = advancedCard.querySelector('[data-name="params_json"]').value.trim();
    if (jsonRaw) {
      try {
        const fromJson = JSON.parse(jsonRaw);
        params = { ...params, ...fromJson };
      } catch (e) {
        toast("В «Расширенных настройках» некорректный JSON: " + e.message, "error");
        return;
      }
    }
    body.params = params;
    try {
      await api(`/api/projects/${pkey}/agents/${akey}`, { method: "PATCH", body });
      toast("Сохранено", "success");
    } catch (e) { toast("Ошибка сохранения: " + e.message, "error"); }
  }

  function field(name, label, value, type = "text", extra = {}, hint = "") {
    return el("label", { class: "field" }, label,
      el("input", { type, value: value ?? "", "data-name": name, ...extra }),
      hint ? el("span", { class: "hint" }, hint) : null);
  }
  function fieldArea(name, label, value, dataName, hint) {
    return el("label", { class: "field" }, label,
      el("textarea", { value: value ?? "", "data-name": dataName || name }),
      hint ? el("span", { class: "hint" }, hint) : null);
  }

  root.append(basicCard);
  if (roleParamsCard) root.append(roleParamsCard);
  root.append(searchCard);
  root.append(promptCard);
  root.append(advancedCard);
  root.append(buttonsCard);

  // --- История запусков ---
  const runsCard = el("div", { class: "card" },
    el("h2", {}, "📊 Последние запуски агента (" + data.runs.length + ")"));
  if (!data.runs.length) {
    runsCard.append(el("div", { class: "muted" }, "Этот агент ещё не запускался."));
  } else {
    runsCard.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Когда"), el("th", {}, "Статус"),
        el("th", {}, "Токены"), el("th", {}, "Стоимость"),
        el("th", {}, "Тема / статья"), el("th", {}, ""))),
      el("tbody", {}, ...data.runs.map(r => {
        const tr = el("tr", {},
          el("td", { class: "muted" }, fmtDate(r.started_at)),
          el("td", {}, pill(r.status)),
          el("td", {}, `${r.tokens_in} / ${r.tokens_out}`),
          el("td", {}, fmtCost(r.cost_usd)),
          el("td", { class: "muted" }, r.topic_id || r.article_id || "—"),
          el("td", {}, el("button", { class: "ghost sm",
            on: { click: () => toggleRun(tr, r) } }, "Подробнее")));
        return tr;
      }))));
  }
  function toggleRun(tr, r) {
    if (tr.nextSibling && tr.nextSibling.classList?.contains("run-detail")) {
      tr.nextSibling.remove();
      return;
    }
    const inputs = (() => { try { return JSON.parse(r.inputs); } catch { return r.inputs; }})();
    const output = (() => { try { return JSON.parse(r.output); } catch { return r.output; }})();
    const detail = el("tr", { class: "run-detail" },
      el("td", { colSpan: 6 },
        el("h3", {}, "Сформированный промт"),
        el("pre", { class: "code" }, r.rendered_prompt || ""),
        el("h3", {}, "Что подали на вход"),
        el("pre", { class: "code" }, JSON.stringify(inputs, null, 2)),
        el("h3", {}, "Что вернул агент"),
        el("pre", { class: "code" }, JSON.stringify(output, null, 2))));
    tr.after(detail);
  }
  root.append(runsCard);
  stamp();
});

// ============================================================================
// Карточка канала (по slug)
// ============================================================================

router.add("/projects/:pkey/channels/:ckey", async ({ pkey, ckey }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pkey}/channels/${ckey}`);
  const c = data.channel; const spec = data.spec; const project = data.project;
  const meta = CHANNEL_RU[c.kind] || { label: c.kind };
  const rewriter = data.rewriter_agent;

  root.append(el("div", { class: "breadcrumbs" },
    el("a", { href: "#/projects" }, "Проекты"), " / ",
    el("a", { href: `#/projects/${project.slug || project.id}` }, project.name), " / ",
    el("span", {}, "Канал: " + meta.label)));

  root.append(el("div", { class: "page-title" },
    el("div", { class: "row gap-lg" }, chIcon(c.kind),
      el("div", {},
        el("h1", { style: "margin:0" }, c.name),
        el("div", { class: "muted", style: "margin-top:4px" }, meta.label))),
    el("div", { class: "row" }, langTag(c.language),
      c.is_video ? el("span", { class: "pill blue" }, "видео-канал")
                 : el("span", { class: "pill gray" }, "текст"),
      c.is_connected ? el("span", { class: "pill green" },
        el("span", { class: "dot" }), "подключён")
                     : el("span", { class: "pill gray" },
        el("span", { class: "dot" }), "не подключён"))));

  // Гайд подключения
  root.append(el("div", { class: "guide-box markdown",
    html: "<h3>Как подключить канал</h3>" + renderMarkdown(spec.connect_guide_md) }));

  // --- Адаптер канала (channel_rewriter) — единственный агент, привязанный
  // именно к этому каналу. Остальные агенты (редакторская команда, сбор
  // тем, визуальная команда) — общие на проект, и видны на вкладке
  // «Агенты». Здесь показываем только rewriter, чтобы не дублировать.
  if (rewriter) {
    const slug = rewriter.slug || rewriter.id;
    root.append(el("div", { class: "card" },
      el("h2", {}, "🤖 Адаптер канала"),
      el("div", { class: "muted",
        style: "font-size:13px;margin-bottom:14px;line-height:1.5" },
        "Этот агент отвечает только за этот канал. Он берёт универсальную " +
        "статью и переписывает её под формат «" + meta.label + "» " +
        "(длина, голос, хэштеги). Промт у него уникальный, под этот канал."),
      el("a", {
        href: `#/projects/${project.slug || project.id}/agents/${slug}`,
        class: "agent-mini",
        style: "text-decoration:none;color:inherit;display:block" },
        el("div", { class: "agent-mini-row" },
          el("div", { style: "font-weight:600;font-size:15px" },
            rewriter.display_name || "Адаптер канала"),
          el("div", { class: "row" }, langTag(rewriter.language))),
        el("div", { class: "muted", style: "font-size:12px;margin-top:4px" },
          "Модель: ", el("code", { class: "inline" }, rewriter.model)),
        el("div", { style: "color:var(--accent);font-size:13px;" +
                          "margin-top:6px;font-weight:600" },
          "Открыть промт и настройки →"))));
  } else if (!c.is_video) {
    // Текстовый канал без rewriter — что-то не так со сидом.
    root.append(el("div", { class: "card" },
      el("h2", {}, "🤖 Адаптер канала"),
      el("div", { class: "muted" },
        "Адаптер для этого канала не найден. Возможно, нужно пересоздать проект.")));
  }

  // Доступы
  const fields = spec.credentials_fields;
  const credsCard = el("div", { class: "card" }, el("h2", {}, "🔑 Доступы к каналу"));
  if (!fields.length) {
    credsCard.append(el("div", { class: "muted" },
      "Этот канал не требует доступов — посты сохраняются как черновики для ручной публикации."));
  } else {
    const inputs = {};
    for (const f of fields) {
      const masked = (c.credentials_masked || {})[f.key] || "";
      const inp = el("input", { type: "text",
        placeholder: masked || "не задано", "data-key": f.key });
      inputs[f.key] = inp;
      credsCard.append(el("label", { class: "field" }, f.key, inp,
        el("span", { class: "hint" }, f.hint)));
    }
    credsCard.append(el("div", { class: "muted",
      style: "font-size:12px;margin-bottom:10px" },
      "Все доступы шифруются и хранятся в базе. В интерфейсе показывается только маска."));
    credsCard.append(el("button", { on: { click: async () => {
      const credentials = {};
      for (const [k, inp] of Object.entries(inputs))
        if (inp.value) credentials[k] = inp.value;
      if (!Object.keys(credentials).length) {
        toast("Нечего сохранять — ни одно поле не заполнено", "error"); return;
      }
      try {
        await api(`/api/projects/${pkey}/channels/${ckey}/credentials`,
          { method: "PATCH", body: { credentials } });
        toast("Сохранено и зашифровано", "success");
        setTimeout(() => location.reload(), 800);
      } catch (e) { toast("Ошибка: " + e.message, "error"); }
    }}}, "💾 Сохранить доступы"));
  }
  root.append(credsCard);

  // Параметры
  const slotsCard = el("div", { class: "card" },
    el("h2", {}, "📅 Расписание и параметры"),
    el("label", { class: "field" }, "Постов в день",
      el("input", { type: "number", value: c.posts_per_day,
        "data-name": "posts_per_day", min: "1", max: "20" }),
      el("span", { class: "hint" }, "При увеличении — добавьте слоты публикации.")),
    el("label", { class: "field" }, "Стратегия выбора статей",
      el("select", { "data-name": "selection_strategy" },
        el("option", { value: "by_rank",
          selected: c.selection_strategy === "by_rank" }, "По рейтингу"),
        el("option", { value: "random_among_written",
          selected: c.selection_strategy === "random_among_written" },
          "Случайно из написанных")),
      el("span", { class: "hint" },
        "«По рейтингу» — берём лучшие по баллу. «Случайно» — рандом среди ещё не опубликованных.")),
    el("div", { class: "kv" },
      el("div", {}, "Лимит символов"),
      el("div", {}, spec.max_chars + " (для одного поста)"),
      el("div", {}, "Поддерживаемое медиа"),
      el("div", {}, spec.media_kinds.join(", ") || "—")),
    el("div", { class: "spacer" }),
    el("button", { on: { click: async () => {
      const body = {
        posts_per_day: parseInt(slotsCard.querySelector('[data-name="posts_per_day"]').value, 10),
        selection_strategy: slotsCard.querySelector('[data-name="selection_strategy"]').value,
      };
      try {
        await api(`/api/projects/${pkey}/channels/${ckey}`, { method: "PATCH", body });
        toast("Параметры канала сохранены", "success");
      } catch (e) { toast("Ошибка: " + e.message, "error"); }
    }}}, "💾 Сохранить параметры"));
  root.append(slotsCard);

  // Слоты публикации (редактируемые)
  const tz = project.timezone || "UTC";
  const slotsEditCard = el("div", { class: "card" },
    el("h2", {}, "🕐 Слоты публикации"),
    el("div", { class: "muted",
      style: "font-size:13px;margin-bottom:14px;line-height:1.5" },
      "В эти моменты канал будет автоматически публиковать посты. " +
      "Время указано по часовому поясу проекта: ",
      el("code", { class: "inline" }, tz),
      ". Изменить пояс можно во вкладке «Настройки» проекта.",
      el("br"),
      el("span", { style: "color:var(--accent)" },
        "⚠️ Внимание: автоматический планировщик ещё не активен. ")));

  const slotsList = el("div", { class: "slots-list" });
  let slotsArr = (data.slots || []).map(s => ({
    time_local: s.time_local, enabled: !!s.enabled }));

  function renderSlots() {
    slotsList.innerHTML = "";
    if (!slotsArr.length) {
      slotsList.append(el("div", { class: "muted" }, "Слотов пока нет."));
      return;
    }
    slotsArr.forEach((s, idx) => {
      const row = el("div", { class: "slot-row" },
        el("input", { type: "time", value: s.time_local, on: { change: ev => {
          slotsArr[idx].time_local = ev.target.value;
        }}}),
        el("label", { class: "slot-toggle" },
          (() => {
            const cb = el("input", { type: "checkbox",
              style: "width:auto;margin-right:6px" });
            cb.checked = s.enabled;
            cb.addEventListener("change", () => { slotsArr[idx].enabled = cb.checked; });
            return cb;
          })(),
          el("span", {}, "активен")),
        el("button", { class: "ghost sm danger", on: { click: () => {
          slotsArr.splice(idx, 1); renderSlots();
        }}}, "✕ удалить"));
      slotsList.append(row);
    });
  }
  renderSlots();

  slotsEditCard.append(slotsList);
  slotsEditCard.append(el("div", { class: "row", style: "margin-top:14px" },
    el("button", { class: "ghost", on: { click: () => {
      slotsArr.push({ time_local: "12:00", enabled: true });
      renderSlots();
    }}}, "+ добавить слот"),
    el("button", { on: { click: async () => {
      try {
        await api(`/api/projects/${pkey}/channels/${ckey}/slots`,
          { method: "PUT", body: { slots: slotsArr } });
        toast("Расписание сохранено", "success");
      } catch (e) { toast("Ошибка: " + e.message, "error"); }
    }}}, "💾 Сохранить расписание")));
  root.append(slotsEditCard);
  stamp();
});

// ============================================================================
// Тема (по id)
// ============================================================================

router.add("/projects/:pkey/topics/:tid", async ({ pkey, tid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pkey}/topics/${tid}`);
  const t = data.topic;
  const project = data.project;
  const scores = (() => { try { return JSON.parse(t.scores || "{}"); } catch { return {}; }})();
  const sources = (() => { try { return JSON.parse(t.sources || "[]"); } catch { return []; }})();

  root.append(el("div", { class: "breadcrumbs" },
    el("a", { href: "#/projects" }, "Проекты"), " / ",
    el("a", { href: `#/projects/${project.slug || project.id}` }, project.name), " / ",
    el("a", { href: `#/projects/${project.slug || project.id}/tab/topics` }, "Темы"), " / ",
    el("span", {}, t.title)));

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0;font-size:22px" }, t.title),
      el("div", { class: "muted", style: "margin-top:4px" },
        t.event_date ? "Дата события: " + t.event_date : "Без даты события")),
    el("div", { class: "row" },
      pill(t.status),
      el("span", { class: "chip" },
        "Рейтинг: " + Number(t.score_total).toFixed(2)))));

  // Описание (расширенное summary от валидатора)
  if (t.summary) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "📋 Что собрала команда сбора тем"),
      el("div", { class: "muted", style: "font-size:13px;margin-bottom:8px" },
        "Это расширенное описание от topic_validator — что подтверждено и почему " +
        "тема прошла фактчек."),
      el("p", { style: "line-height:1.7" }, t.summary)));
  }

  // Оценки ранжировщика
  if (Object.keys(scores).length) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "📊 Как тему оценил ранжировщик"),
      el("div", { class: "muted", style: "font-size:13px;margin-bottom:14px" },
        "Каждый критерий 0–10. Финальный рейтинг считается локально по весам, " +
        "которые заданы в настройках агента topic_ranker."),
      el("div", { class: "scores-grid" },
        ...Object.entries(scores).map(([k, v]) => el("div", { class: "score-row" },
          el("div", { class: "score-label" }, k),
          el("div", { class: "score-bar" },
            el("div", { class: "score-fill",
              style: "width:" + Math.max(0, Math.min(10, Number(v) || 0)) * 10 + "%" })),
          el("div", { class: "score-value" }, String(v)))))));
  }

  // Источники
  if (sources.length) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "🔗 Источники"),
      el("ul", { style: "margin:0;padding-left:20px;line-height:1.8" },
        ...sources.map(s => el("li", {},
          el("a", { href: s, target: "_blank", rel: "noopener" }, s))))));
  }

  // Статьи по этой теме
  if (data.articles?.length) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "✍️ Статьи по этой теме"),
      el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "Заголовок"), el("th", {}, "Язык"),
          el("th", {}, "Статус"), el("th", {}, "QA"),
          el("th", {}, "Картинка"), el("th", {}, ""))),
        el("tbody", {}, ...data.articles.map(a => el("tr", {},
          el("td", { style: "max-width:420px" },
            a.chosen_headline || el("span", { class: "muted" }, "—")),
          el("td", {}, langTag(a.language)),
          el("td", {}, pill(a.status)),
          el("td", {}, a.qa_score || "—"),
          el("td", {}, a.chosen_image_id
            ? el("span", { class: "pill green" }, "есть")
            : el("span", { class: "pill red" }, "нет")),
          el("td", {}, el("a", {
            href: `#/projects/${project.slug || project.id}/articles/${a.id}` },
            "Открыть →"))))))));
  }

  // Запуски топик-фазы (генератор / валидатор / ранжировщик)
  if (data.topic_phase_runs?.length) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "🔍 Что собрали агенты сбора тем"),
      el("div", { class: "muted", style: "font-size:13px;margin-bottom:14px" },
        "Эти запуски породили эту тему вместе с другими в той же пачке. " +
        "Можно посмотреть, что искал генератор, что подтвердил валидатор и " +
        "как ранжировщик оценил весь набор."),
      ...data.topic_phase_runs.map(r => agentRunBlock(r))));
  }

  // Запуски агентов по этой теме (researcher, writer, headline и т.д.)
  if (data.agent_runs?.length) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "🛠 Работа команды редакторов над темой"),
      el("div", { class: "muted", style: "font-size:13px;margin-bottom:14px" },
        "Каждый запуск — отдельный шаг команды (исследование, проверка фактов, " +
        "написание текста, заголовков, иллюстраций, итоговый QA)."),
      ...data.agent_runs.map(r => agentRunBlock(r))));
  }
  stamp();
});

function agentRunBlock(r) {
  const inputs = (() => { try { return JSON.parse(r.inputs); } catch { return r.inputs; }})();
  const output = (() => { try { return JSON.parse(r.output); } catch { return r.output; }})();
  const role = ROLE_RU[r.agent_role] || r.agent_role || "агент";
  const lang = r.agent_language && r.agent_language !== "bi"
    ? " · " + r.agent_language.toUpperCase() : "";
  const block = el("details", { class: "agent-run-block" },
    el("summary", {},
      el("span", { style: "font-weight:600" }, role + lang),
      " ",
      pill(r.status),
      " ",
      el("span", { class: "muted", style: "font-size:12px" }, fmtDate(r.started_at)),
      r.cost_usd ? el("span", { class: "muted",
        style: "font-size:12px;margin-left:8px" }, fmtCost(r.cost_usd)) : null),
    el("h3", {}, "Сформированный промт"),
    el("pre", { class: "code" }, r.rendered_prompt || ""),
    el("h3", {}, "Что подали на вход"),
    el("pre", { class: "code" }, JSON.stringify(inputs, null, 2)),
    el("h3", {}, "Что вернул агент"),
    el("pre", { class: "code" }, JSON.stringify(output, null, 2)));
  return block;
}

// ============================================================================
// Статья
// ============================================================================

router.add("/projects/:pkey/articles/:aid", async ({ pkey, aid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pkey}/articles/${aid}`);
  const a = data.article; const project = data.project;

  root.append(el("div", { class: "breadcrumbs" },
    el("a", { href: "#/projects" }, "Проекты"), " / ",
    el("a", { href: `#/projects/${project.slug || project.id}` }, project.name), " / ",
    el("span", {}, "Статья")));

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, a.chosen_headline || "Без заголовка"),
      el("div", { class: "muted", style: "margin-top:4px" },
        "Тема: " + (data.topic?.title || "—"))),
    el("div", { class: "row" }, langTag(a.language), pill(a.status),
      a.qa_score ? el("span", { class: "chip" }, "QA: " + a.qa_score + "/100") : null)));

  if (data.media.length) {
    const gallery = el("div", { class: "row gap-lg", style: "flex-wrap:wrap" });
    for (const m of data.media) {
      gallery.append(el("img", { src: m.storage_url,
        class: "thumb" + (m.chosen ? " chosen" : ""),
        title: m.chosen ? "Выбрана для публикации" : "Запасной вариант" }));
    }
    root.append(el("div", { class: "card" }, el("h2", {}, "🖼 Иллюстрации"),
      el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
        "Зелёная рамка — выбрано QA для публикации."), gallery));
  }

  // ─── 🎬 Видео ─────────────────────────────────────────────────────────
  // Финальный MP4 хранится как media_assets.kind='video' AND chosen=1.
  // API отдаёт его в ответе под ключом `video` либо null. Если ещё не
  // сгенерировано — кнопка запускает run_video_phase() синхронно.
  const video = data.video;
  if (video && video.url) {
    root.append(el("div", { class: "card" },
      el("h2", {}, "🎬 Видео"),
      el("video", { src: video.url, controls: true,
                    style: "width:100%;max-width:540px;border-radius:8px" }),
      el("div", { class: "muted sm" },
        `Длительность: ${video.duration_s}с · ${video.scenes_count} сцен`)
    ));
  } else {
    root.append(el("div", { class: "card" },
      el("h2", {}, "🎬 Видео"),
      el("p", { class: "muted" }, "Видео ещё не сгенерировано."),
      el("button", { class: "primary",
        on: { click: async (ev) => {
          const btn = ev.currentTarget;
          btn.disabled = true; btn.textContent = "Генерация…";
          try {
            await api(`/api/projects/${pkey}/articles/${aid}/generate_video`,
                      { method: "POST" });
            location.reload();
          } catch (e) {
            alert("Ошибка: " + e.message);
            btn.disabled = false; btn.textContent = "Сгенерировать видео";
          }
        }}
      }, "Сгенерировать видео")
    ));
  }

  root.append(el("div", { class: "card markdown" },
    el("h2", {}, "📄 Текст статьи (универсальная версия)"),
    el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      "Это большая универсальная версия. Под каждый канал её перепишет адаптер канала."),
    el("div", { html: renderMarkdown(a.body_full) })));

  root.append(el("div", { class: "card" },
    el("h2", {}, "🔁 История работы агентов над статьёй"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Когда"), el("th", {}, "Статус"), el("th", {}, "ID агента"))),
      el("tbody", {}, ...data.agent_runs.map(r => el("tr", {},
        el("td", { class: "muted" }, fmtDate(r.started_at)),
        el("td", {}, pill(r.status)),
        el("td", {}, el("code", { class: "inline" }, r.agent_id))))))));

  // ─── Публикация в канал ───────────────────────────────────────────────
  // Показываем подключённые каналы того же языка, что и статья. Для
  // каждого канала: либо кнопка «Опубликовать сейчас» (новый пост), либо
  // статус существующего поста с кнопкой «Повторить» если он провалился.
  // Это альтернатива слот-планировщику: пользователь, не желающий ждать
  // расписания, кликает кнопку и пост уходит в канал немедленно (POST
  // /api/projects/{pkey}/articles/{aid}/publish/{ckey}).
  const connectedChannels = (data.channels || []).filter(c => c.is_connected);
  const postsByChannel = Object.fromEntries(
    (data.posts || []).map(p => [p.channel_id, p]));
  const pubCard = el("div", { class: "card" },
    el("h2", {}, "📢 Опубликовать в канал"),
    el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      "Кликните «Опубликовать сейчас», чтобы пропустить статью через адаптер "
      + "канала и отправить прямо сейчас, не дожидаясь слота расписания."));
  if (!connectedChannels.length) {
    pubCard.append(emptyState("🔌", "Подключённых каналов нет",
      "Подключите токены в разделе «Каналы» проекта."));
  } else {
    const list = el("div", { class: "row gap-md", style: "flex-direction:column;align-items:stretch" });
    for (const ch of connectedChannels) {
      const post = postsByChannel[ch.id];
      const left = el("div", { class: "row" }, chIcon(ch.kind),
        el("div", {}, el("div", {}, ch.name),
          el("div", { class: "muted", style: "font-size:12px" },
            (CHANNEL_RU[ch.kind]?.label || ch.kind))));
      let right;
      if (!post) {
        // Пост ещё не создан — показываем основную CTA.
        right = el("button", { class: "primary",
          on: { click: () => publishNow(ch) } }, "Опубликовать сейчас");
      } else if (post.status === "published") {
        right = el("div", { class: "row" }, pill("published"),
          post.external_url
            ? el("a", { href: post.external_url, target: "_blank" }, "открыть ↗")
            : el("span", { class: "muted" }, "ссылка недоступна"));
      } else if (post.status === "failed") {
        right = el("div", { class: "row" }, pill("failed"),
          el("span", { class: "muted",
            style: "font-size:12px;max-width:280px;overflow:hidden;text-overflow:ellipsis" },
            post.error || ""),
          el("button", { class: "ghost",
            on: { click: () => publishNow(ch) } }, "Повторить"));
      } else {
        // scheduled / pending — показываем когда и даём кнопку «прямо сейчас».
        right = el("div", { class: "row" }, pill(post.status),
          el("span", { class: "muted", style: "font-size:12px" },
            "запланировано: " + fmtDate(post.scheduled_for)),
          el("button", { class: "ghost",
            on: { click: () => publishNow(ch) } }, "опубликовать сейчас"));
      }
      list.append(el("div", { class: "agent-mini",
        style: "display:flex;justify-content:space-between;align-items:center" },
        left, right));
    }
    pubCard.append(list);
  }
  async function publishNow(ch) {
    const ckey = ch.slug || ch.id;
    try {
      const resp = await api(
        `/api/projects/${project.slug || project.id}/articles/${a.id}/publish/${ckey}`,
        { method: "POST" });
      if (resp.ok) {
        toast("Опубликовано в " + ch.name);
      } else {
        toast("Не удалось опубликовать в " + ch.name + " — повторим автоматически", "error");
      }
      setTimeout(() => location.reload(), 800);
    } catch (e) {
      toast("Ошибка: " + e.message, "error");
    }
  }
  root.append(pubCard);

  stamp();
});

// ============================================================================
// О системе
// ============================================================================

router.add("/about", () => {
  const root = $("#root");
  root.innerHTML = "";
  root.append(el("div", { class: "page-title" },
    el("h1", { style: "margin:0" }, "О системе")));
  root.append(el("div", { class: "card markdown", html: `
  <h2>Что это такое</h2>
  <p><b>AiCrewStudio</b> — конструктор «команд ИИ-агентов» для контент-студий. Каждый проект — отдельный канал/блог со своей нишей. Внутри проекта работает команда из специализированных агентов: одни ищут темы, другие пишут статьи, третьи делают картинки, четвёртые публикуют.</p>
  <h2>Как устроен пайплайн</h2>
  <ul>
    <li><b>Сбор тем:</b> генератор → проверяльщик → ранжировщик.</li>
    <li><b>Написание статей</b> идёт параллельно русской и английской командами.</li>
    <li><b>Публикация</b> в каждый канал: адаптер переписывает статью под формат канала.</li>
  </ul>
  <h2>Дружелюбные настройки</h2>
  <p>На странице каждого агента — отдельные понятные поля для всех его параметров: количество тем, длина статьи, стили заголовков, веса критериев и т.д. JSON-редактирование осталось в «Расширенных настройках» для опытных пользователей.</p>
  <h2>Текущий режим</h2>
  <p>Сейчас система работает в <b>демо-режиме</b>: вместо настоящих API используются заглушки.</p>
  <h2>Как переключиться на реальные API</h2>
  <ol>
    <li>На сервере: <code class="inline">/opt/aicrewstudio/.env</code> — поставьте <code class="inline">AICREW_LLM_PROVIDER=openai</code> и впишите <code class="inline">OPENAI_API_KEY</code>.</li>
    <li>На странице каждого канала введите токены — они зашифруются.</li>
    <li>Перезапустите: <code class="inline">systemctl restart aicrew</code>.</li>
  </ol>
  ` }));
  stamp();
});

// ---------- старт -----------------------------------------------------------

router.start();
