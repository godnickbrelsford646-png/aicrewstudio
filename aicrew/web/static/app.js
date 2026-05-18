/* AiCrewStudio — фронтенд на чистом JS, полностью на русском. */

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

const stamp = () => $("#last-update").textContent = "обновлено в " + new Date().toLocaleTimeString("ru-RU");

let _toastTimer = null;
function toast(message, kind = "success") {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast show " + kind;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { t.className = "toast"; }, 3500);
}

// ---------- словари (i18n) --------------------------------------------------

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
  // pipeline_runs / agent_runs
  queued: "в очереди",
  running: "выполняется",
  completed: "завершено",
  failed: "ошибка",
  // topics
  generated: "сгенерирована",
  validated: "проверена",
  ranked: "отранжирована",
  written: "написана",
  // articles
  drafting: "черновик",
  qa_passed: "готова",
  // posts
  pending: "ожидает",
  scheduled: "запланирован",
  published: "опубликован",
};

const PHASE_RU = {
  topics: "Темы",
  articles: "Статьи",
  publication: "Публикация",
  full: "Полный цикл",
};

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

const STRATEGY_RU = {
  by_rank: "по рейтингу",
  random_among_written: "случайно",
};

const TAB_RU = {
  Pipeline: "Пайплайн",
  Agents: "Агенты",
  Topics: "Темы",
  Articles: "Статьи",
  Channels: "Каналы",
  Posts: "Публикации",
};

const LANG_LABEL = { ru: "Русская команда", en: "Английская команда", bi: "Двуязычно" };

// ---------- небольшие визуальные хелперы ------------------------------------

function pill(status) {
  const map = {
    completed: "green", published: "green", qa_passed: "green",
    written: "green",
    running: "yellow", scheduled: "yellow", drafting: "yellow",
    ranked: "blue", validated: "blue", generated: "gray",
    queued: "gray", pending: "gray",
    failed: "red",
  };
  return el("span", { class: "pill " + (map[status] || "gray") },
    el("span", { class: "dot" }),
    STATUS_RU[status] || status,
  );
}

function langTag(l) {
  if (!l) return null;
  if (l === "bi") return el("span", { class: "lang", title: "общий для обеих команд" }, "RU/EN");
  return el("span", { class: "lang " + l, title: l === "ru" ? "русская команда" : "английская команда" }, l.toUpperCase());
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
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch { return s; }
}

function renderMarkdown(md) {
  if (!md) return "";
  return md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
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
    el("div", { class: "empty-text" }, text),
  );
}

function loading(text = "Загрузка…") {
  return el("div", { class: "loading" },
    el("div", { class: "spinner" }),
    el("div", {}, text),
  );
}

// ---------- роутер ----------------------------------------------------------

const router = {
  routes: [],
  add(pattern, handler) { this.routes.push({ pattern, handler }); },
  start() {
    window.addEventListener("hashchange", () => this.dispatch());
    this.dispatch();
  },
  navigate(hash) { location.hash = hash; },
  dispatch() {
    const hash = location.hash.slice(1) || "/projects";
    for (const r of this.routes) {
      const params = match(r.pattern, hash);
      if (params !== null) {
        $("#root").innerHTML = "";
        $("#root").append(loading());
        Promise.resolve(r.handler(params))
          .catch(err => {
            $("#root").innerHTML = "";
            $("#root").append(el("div", { class: "card" },
              el("h2", {}, "Не удалось загрузить страницу"),
              el("div", { class: "muted" }, err.message),
            ));
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
  root.append(el("div", { class: "page-title" },
    el("h1", {}, "Проекты"),
  ));
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
    grid.append(el("a", { href: `#/projects/${p.id}`, style: "text-decoration:none;color:inherit" },
      el("div", { class: "card card-hover" },
        el("div", { class: "row between" },
          el("h2", { style: "margin:0" }, p.name),
          p.is_enabled ? pill("running") : pill("scheduled"),
        ),
        el("div", { class: "muted", style: "font-size:13px;margin:6px 0 12px 0" }, p.niche || "—"),
        el("div", { class: "row" }, ...langs.map(l => langTag(l))),
        el("div", { class: "spacer" }),
        el("div", { class: "kv" },
          el("div", {}, "Тем в день"), el("div", {}, p.daily_topics_target),
          el("div", {}, "Статей в день"), el("div", {}, p.daily_articles_target),
          el("div", {}, "Бюджет"), el("div", {}, "$" + p.budget_usd_month + " / мес"),
        ),
      ),
    ));
  }
  root.append(grid);
  stamp();
});

// ---------- карточка проекта ------------------------------------------------

router.add("/projects/:pid", async ({ pid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pid}`);
  const p = data.project;

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, p.name),
      el("div", { class: "muted", style: "margin-top:4px" }, p.description || "Без описания"),
    ),
    el("div", { class: "row" },
      el("button", { class: "ghost sm", on: { click: () => runPhase(pid, "topics") } }, "Темы"),
      el("button", { class: "ghost sm", on: { click: () => runPhase(pid, "articles") } }, "Статьи"),
      el("button", { class: "ghost sm", on: { click: () => runPhase(pid, "publish") } }, "Публикация"),
      el("button", { on: { click: () => runPhase(pid, "full") } }, "▶ Запустить полный цикл"),
    ),
  ));

  const tabs = ["Pipeline", "Agents", "Topics", "Articles", "Channels", "Posts"];
  const counts = {
    Agents: data.agents.length,
    Topics: data.topics.length,
    Articles: data.articles.length,
    Channels: data.channels.length,
  };
  const tabbar = el("div", { class: "tabs" });
  const view = el("div", {});
  let active = "Pipeline";
  for (const t of tabs) {
    const node = el("div", { class: "tab" + (t === active ? " active" : ""), on: {
      click: () => { active = t; render(); },
    }},
      TAB_RU[t],
      counts[t] != null ? el("span", { class: "count" }, " · " + counts[t]) : null,
    );
    tabbar.append(node);
  }
  root.append(tabbar);
  root.append(view);

  function render() {
    view.innerHTML = "";
    for (const tab of tabbar.children) tab.classList.toggle("active", tab.firstChild?.textContent === TAB_RU[active] || tab.textContent.startsWith(TAB_RU[active]));
    if (active === "Pipeline") view.append(renderPipelineTab(data));
    if (active === "Agents") view.append(renderAgentsTab(data.agents));
    if (active === "Topics") view.append(renderTopicsTab(data.topics));
    if (active === "Articles") view.append(renderArticlesTab(pid, data.articles));
    if (active === "Channels") view.append(renderChannelsTab(data.channels));
    if (active === "Posts") view.append(renderPostsTab(pid));
  }
  // правильный матч активной вкладки
  const setTabActive = () => {
    [...tabbar.children].forEach((node, i) => node.classList.toggle("active", tabs[i] === active));
  };
  const _origRender = render;
  render = () => { _origRender(); setTabActive(); };
  render();
  stamp();
});

async function runPhase(pid, phase) {
  const map = { topics: "runs/topics", articles: "runs/articles", publish: "runs/publish", full: "runs/full" };
  const labels = { topics: "Запускаю генерацию тем…", articles: "Запускаю написание статей…", publish: "Запускаю публикацию…", full: "Запускаю полный цикл…" };
  toast(labels[phase] || "Запуск…");
  try {
    await api(`/api/projects/${pid}/${map[phase]}`, { method: "POST", body: {} });
    toast("Готово. Обновляю страницу…", "success");
    setTimeout(() => location.reload(), 800);
  } catch (e) {
    toast("Ошибка: " + e.message, "error");
  }
}

// ---------- вкладка: пайплайн -----------------------------------------------

function renderPipelineTab(data) {
  const wrap = el("div", { class: "grid grid-2" });
  const phases = [
    {
      title: "1. Сбор тем",
      icon: "🔎",
      items: ["Генератор предлагает темы по нише", "Проверяльщик уточняет факты и расширяет описание", "Если тем мало — цикл повторяется", "Ранжировщик сортирует по 5 критериям"],
    },
    {
      title: "2. Написание статей · Русская команда",
      icon: "🇷🇺",
      items: ["Исследователь собирает факты", "Проверяльщик исследований правит ошибки", "Автор пишет универсальную статью", "Создатель заголовков · Промт картинок · QA"],
    },
    {
      title: "2. Написание статей · Английская команда",
      icon: "🇬🇧",
      items: ["Researcher gathers facts", "Validator fixes errors in the brief", "Writer composes the long article", "Headlines · Image prompts · QA"],
    },
    {
      title: "3. Публикация",
      icon: "📤",
      items: ["Адаптер канала переписывает текст под формат", "Готовый пост публикуется через адаптер канала", "Видео-каналы пока пропускаются (на этапе подключения видео-команды)"],
    },
  ];
  for (const ph of phases) {
    wrap.append(el("div", { class: "card" },
      el("h2", {}, ph.icon + "  " + ph.title),
      el("ul", { style: "margin:0;padding-left:20px;font-size:13.5px;line-height:1.7;color:var(--text-dim)" },
        ...ph.items.map(it => el("li", {}, it)),
      ),
    ));
  }
  const runsCard = el("div", { class: "card" },
    el("h2", {}, "Последние запуски"),
    data.pipeline_runs.length === 0
      ? el("div", { class: "muted" }, "Запусков ещё не было.")
      : el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "Тип"),
            el("th", {}, "Статус"),
            el("th", {}, "Начало"),
            el("th", {}, "Окончание"),
          )),
          el("tbody", {}, ...data.pipeline_runs.map(r => el("tr", {},
            el("td", {}, PHASE_RU[r.kind] || r.kind),
            el("td", {}, pill(r.status)),
            el("td", { class: "muted" }, fmtDate(r.started_at)),
            el("td", { class: "muted" }, fmtDate(r.finished_at)),
          ))),
        ),
  );
  return el("div", {}, wrap, el("div", { class: "spacer" }), runsCard);
}

// ---------- вкладка: агенты -------------------------------------------------

function renderAgentsTab(agents) {
  if (!agents.length) {
    return emptyState("🤖", "Агентов нет", "Запустите seed для создания команды.");
  }
  // группируем: глобальные / RU / EN / per-channel
  const groups = { bi: [], ru: [], en: [] };
  for (const a of agents) {
    if (a.role === "channel_rewriter") continue; // показываем отдельно
    (groups[a.language] || groups.bi).push(a);
  }
  const rewriters = agents.filter(a => a.role === "channel_rewriter");

  const card = (title, subtitle, list) => {
    if (!list.length) return null;
    const grid = el("div", { class: "grid grid-2" });
    for (const a of list) grid.append(agentCard(a));
    return el("div", { class: "card-section" },
      el("div", { style: "display:flex;align-items:baseline;gap:10px;margin-bottom:14px" },
        el("h2", { style: "margin:0" }, title),
        el("span", { class: "muted", style: "font-size:13px" }, subtitle),
      ),
      grid,
    );
  };
  return el("div", {},
    card("Общие агенты", "одни на весь проект", groups.bi),
    card("Русская команда редакторов", "пишет на русском", groups.ru),
    card("Английская команда редакторов", "пишет на английском", groups.en),
    card("Адаптеры под каналы", "по одному на каждый текстовый канал", rewriters),
  );
}

function agentCard(a) {
  return el("a", { href: "#/agents/" + a.id, style: "text-decoration:none;color:inherit" },
    el("div", { class: "card card-hover" },
      el("div", { class: "row between" },
        el("h2", { style: "margin:0;font-size:15px" }, ROLE_RU[a.role] || a.display_name),
        el("div", { class: "row" }, langTag(a.language)),
      ),
      el("div", { class: "muted", style: "font-size:13px;margin:6px 0 12px 0" },
        ROLE_DESC[a.role] || a.description),
      el("div", { class: "kv" },
        el("div", {}, "Модель"), el("div", {}, el("code", { class: "inline" }, a.model)),
        el("div", {}, "Температура"), el("div", {}, a.temperature),
        el("div", {}, "Лимит токенов"), el("div", {}, a.max_tokens),
      ),
      el("div", { class: "spacer" }),
      el("div", { style: "color:var(--accent);font-size:13px;font-weight:600" }, "Открыть и настроить →"),
    ),
  );
}

// ---------- вкладка: темы ---------------------------------------------------

function renderTopicsTab(topics) {
  if (!topics.length) {
    return emptyState("💡", "Тем пока нет",
      "Нажмите «Темы» наверху, чтобы запустить агентов сбора тем.");
  }
  const tbody = el("tbody", {}, ...topics.map(t => {
    const scores = JSON.parse(t.scores || "{}");
    const labels = { relevance: "акт.", novelty: "нов.", virality: "вир.", evergreen: "ever.", sources_quality: "ист." };
    return el("tr", {},
      el("td", { style: "max-width:520px" }, t.title),
      el("td", {}, pill(t.status)),
      el("td", {}, el("b", {}, Number(t.score_total).toFixed(1))),
      el("td", { class: "muted", style: "font-size:12px" },
        Object.entries(scores).map(([k, v]) => `${labels[k]||k}: ${v}`).join(" · ")),
    );
  }));
  return el("div", { class: "card" },
    el("h2", {}, "Темы (" + topics.length + ")"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Тема"), el("th", {}, "Статус"),
        el("th", {}, "Рейтинг"), el("th", {}, "Оценки"),
      )),
      tbody,
    ),
  );
}

// ---------- вкладка: статьи -------------------------------------------------

function renderArticlesTab(pid, articles) {
  if (!articles.length) {
    return emptyState("📝", "Статей пока нет",
      "Сначала запустите сбор тем, потом — написание статей.");
  }
  return el("div", { class: "card" },
    el("h2", {}, "Статьи (" + articles.length + ")"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Заголовок"),
        el("th", {}, "Язык"),
        el("th", {}, "Статус"),
        el("th", {}, "Оценка QA"),
        el("th", {}, "Создана"),
        el("th", {}, ""),
      )),
      el("tbody", {}, ...articles.map(a => el("tr", {},
        el("td", { style: "max-width:480px" }, a.chosen_headline || el("span", { class: "muted" }, "—")),
        el("td", {}, langTag(a.language)),
        el("td", {}, pill(a.status)),
        el("td", {}, a.qa_score || "—"),
        el("td", { class: "muted" }, fmtDate(a.created_at)),
        el("td", {}, el("a", { href: "#/projects/" + pid + "/articles/" + a.id }, "Открыть →")),
      ))),
    ),
  );
}

// ---------- вкладка: каналы -------------------------------------------------

function renderChannelsTab(channels) {
  if (!channels.length) {
    return emptyState("📡", "Каналов пока нет", "Каналы добавляются через seed или вручную.");
  }
  const wrap = el("div", { class: "grid grid-2" });
  for (const c of channels) {
    const meta = CHANNEL_RU[c.kind] || { label: c.kind };
    wrap.append(el("a", { href: "#/channels/" + c.id, style: "text-decoration:none;color:inherit" },
      el("div", { class: "card card-hover" },
        el("div", { class: "row between" },
          el("div", { class: "row" },
            chIcon(c.kind),
            el("div", {},
              el("div", { style: "font-weight:600;font-size:15px" }, c.name),
              el("div", { class: "muted", style: "font-size:12px" }, meta.label),
            ),
          ),
          el("div", { class: "row" },
            langTag(c.language),
            c.is_video ? el("span", { class: "pill blue" }, "видео") : el("span", { class: "pill gray" }, "текст"),
            c.is_enabled ? pill("running") : pill("scheduled"),
          ),
        ),
        el("div", { class: "spacer" }),
        el("div", { class: "kv" },
          el("div", {}, "Постов в день"), el("div", {}, c.posts_per_day),
          el("div", {}, "Стратегия"), el("div", {}, STRATEGY_RU[c.selection_strategy] || c.selection_strategy),
        ),
        el("div", { class: "spacer" }),
        el("div", { style: "color:var(--accent);font-size:13px;font-weight:600" }, "Настроить и подключить →"),
      ),
    ));
  }
  return wrap;
}

// ---------- вкладка: посты --------------------------------------------------

function renderPostsTab(pid) {
  const wrap = el("div", { class: "card" }, el("h2", {}, "Публикации"), loading());
  api(`/api/projects/${pid}/posts`).then(({ posts }) => {
    wrap.innerHTML = "";
    wrap.append(el("h2", {}, "Публикации (" + posts.length + ")"));
    if (!posts.length) { wrap.append(emptyState("📤", "Публикаций пока нет", "Запустите фазу публикации.")); return; }
    wrap.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Канал"),
        el("th", {}, "Язык"),
        el("th", {}, "Статус"),
        el("th", {}, "Заголовок"),
        el("th", {}, "Ссылка"),
      )),
      el("tbody", {}, ...posts.map(p => el("tr", {},
        el("td", {}, el("div", { class: "row" }, chIcon(p.channel_kind), p.channel_name)),
        el("td", {}, langTag(p.language)),
        el("td", {}, pill(p.status)),
        el("td", { style: "max-width:380px" }, p.article_headline || ""),
        el("td", {}, p.external_url
          ? el("a", { href: p.external_url, target: "_blank" }, "открыть ↗")
          : el("span", { class: "muted" }, "—")),
      ))),
    ));
  }).catch(e => {
    wrap.innerHTML = "";
    wrap.append(el("div", { class: "muted" }, "Ошибка: " + e.message));
  });
  return wrap;
}

// ---------- карточка агента -------------------------------------------------

router.add("/agents/:aid", async ({ aid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api("/api/agents/" + aid);
  const a = data.agent;
  let params = a.params; try { params = JSON.parse(a.params); } catch {}

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, ROLE_RU[a.role] || a.display_name),
      el("div", { class: "muted", style: "margin-top:4px" }, ROLE_DESC[a.role] || a.description),
    ),
    el("div", { class: "row" },
      langTag(a.language),
      el("span", { class: "chip" }, a.role),
    ),
  ));

  const isPromptRu = (a.language === "ru");
  const promptHint = a.language === "en"
    ? "Промт английской команды должен быть на английском — он отправляется LLM как есть."
    : a.language === "ru"
    ? "Промт русской команды должен быть на русском."
    : "Этот агент общий для обеих команд — его промт сам адаптируется по {{ language }}.";

  const form = el("div", { class: "card" },
    el("h2", {}, "Настройки агента"),
    field("display_name", "Название", a.display_name),
    field("model", "Модель", a.model, "text", {}, "Например: openai:gpt-4o, openai:gpt-5, mock:smart"),
    field("temperature", "Температура", a.temperature, "number", { step: "0.1", min: "0", max: "2" },
          "0 — детерминированно, 1 — творчески, 2 — хаос."),
    field("max_tokens", "Лимит токенов ответа", a.max_tokens, "number", {},
          "Сколько максимум токенов агент может сгенерировать за один вызов."),
    fieldArea("prompt_template", "Промт", a.prompt_template, "prompt_template", promptHint),
    fieldArea("params", "Параметры (JSON)", JSON.stringify(params, null, 2), "params",
              "Дополнительные настройки конкретной роли. Меняйте, если знаете, что делаете."),
    el("div", { class: "row" },
      el("button", { on: { click: save } }, "💾 Сохранить"),
      el("button", { class: "ghost", on: { click: () => location.reload() } }, "Отменить"),
    ),
  );

  async function save() {
    const body = {};
    for (const k of ["display_name", "model", "temperature", "max_tokens", "prompt_template"]) {
      const v = form.querySelector(`[data-name="${k}"]`).value;
      body[k] = (k === "temperature") ? Number(v) : (k === "max_tokens" ? parseInt(v, 10) : v);
    }
    try {
      body.params = JSON.parse(form.querySelector('[data-name="params"]').value);
    } catch (e) { toast("Параметры не в формате JSON: " + e.message, "error"); return; }
    try {
      await api("/api/agents/" + aid, { method: "PATCH", body });
      toast("Сохранено", "success");
    } catch (e) { toast("Ошибка сохранения: " + e.message, "error"); }
  }
  function field(name, label, value, type = "text", extra = {}, hint = "") {
    return el("label", { class: "field" },
      label,
      el("input", { type, value: value ?? "", "data-name": name, ...extra }),
      hint ? el("span", { class: "hint" }, hint) : null,
    );
  }
  function fieldArea(name, label, value, dataName, hint = "") {
    return el("label", { class: "field" },
      label,
      el("textarea", { value: value ?? "", "data-name": dataName || name }),
      hint ? el("span", { class: "hint" }, hint) : null,
    );
  }
  root.append(form);

  const runsCard = el("div", { class: "card" }, el("h2", {}, "Последние запуски агента (" + data.runs.length + ")"));
  if (!data.runs.length) {
    runsCard.append(el("div", { class: "muted" }, "Этот агент ещё не запускался."));
  } else {
    runsCard.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Когда"),
        el("th", {}, "Статус"),
        el("th", {}, "Токены"),
        el("th", {}, "Стоимость"),
        el("th", {}, "Тема / статья"),
        el("th", {}, ""),
      )),
      el("tbody", {}, ...data.runs.map(r => {
        const tr = el("tr", {},
          el("td", { class: "muted" }, fmtDate(r.started_at)),
          el("td", {}, pill(r.status)),
          el("td", {}, `${r.tokens_in} / ${r.tokens_out}`),
          el("td", {}, fmtCost(r.cost_usd)),
          el("td", { class: "muted" }, r.topic_id || r.article_id || "—"),
          el("td", {}, el("button", { class: "ghost sm", on: { click: () => toggleRun(tr, r) } }, "Подробнее")),
        );
        return tr;
      })),
    ));
  }
  root.append(runsCard);

  function toggleRun(tr, r) {
    if (tr.nextSibling && tr.nextSibling.classList?.contains("run-detail")) {
      tr.nextSibling.remove();
      return;
    }
    const inputs = safeParse(r.inputs);
    const output = safeParse(r.output);
    const detail = el("tr", { class: "run-detail" },
      el("td", { colSpan: 6 },
        el("h3", {}, "Сформированный промт"),
        el("pre", { class: "code" }, r.rendered_prompt || ""),
        el("h3", {}, "Что подали на вход"),
        el("pre", { class: "code" }, JSON.stringify(inputs, null, 2)),
        el("h3", {}, "Что вернул агент"),
        el("pre", { class: "code" }, JSON.stringify(output, null, 2)),
      ));
    tr.after(detail);
  }
  function safeParse(s) { try { return JSON.parse(s); } catch { return s; } }
  stamp();
});

// ---------- карточка канала -------------------------------------------------

router.add("/channels/:cid", async ({ cid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api("/api/channels/" + cid);
  const c = data.channel; const spec = data.spec;
  const meta = CHANNEL_RU[c.kind] || { label: c.kind };

  root.append(el("div", { class: "page-title" },
    el("div", { class: "row gap-lg" },
      chIcon(c.kind),
      el("div", {},
        el("h1", { style: "margin:0" }, c.name),
        el("div", { class: "muted", style: "margin-top:4px" }, meta.label),
      ),
    ),
    el("div", { class: "row" },
      langTag(c.language),
      c.is_video ? el("span", { class: "pill blue" }, "видео-канал") : el("span", { class: "pill gray" }, "текст"),
    ),
  ));

  // инструкция подключения
  root.append(el("div", { class: "guide-box markdown",
                         html: "<h3>Как подключить канал</h3>" + renderMarkdown(spec.connect_guide_md) }));

  // credentials editor
  const fields = spec.credentials_fields;
  const credsCard = el("div", { class: "card" }, el("h2", {}, "🔑 Доступы к каналу"));
  if (!fields.length) {
    credsCard.append(el("div", { class: "muted" },
      "Этот канал не требует доступов — посты сохраняются как черновики для ручной публикации."));
  } else {
    const inputs = {};
    for (const f of fields) {
      const masked = (c.credentials_masked || {})[f.key] || "";
      const inp = el("input", { type: "text", placeholder: masked || "не задано", "data-key": f.key });
      inputs[f.key] = inp;
      credsCard.append(el("label", { class: "field" },
        f.key,
        inp,
        el("span", { class: "hint" }, f.hint),
      ));
    }
    credsCard.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:10px" },
      "Все доступы шифруются и хранятся в базе. В интерфейсе показывается только маска."));
    credsCard.append(el("button", {
      on: {
        click: async () => {
          const credentials = {};
          for (const [k, inp] of Object.entries(inputs))
            if (inp.value) credentials[k] = inp.value;
          if (!Object.keys(credentials).length) {
            toast("Нечего сохранять — ни одно поле не заполнено", "error");
            return;
          }
          try {
            await api(`/api/channels/${cid}/credentials`, {
              method: "PATCH", body: { credentials },
            });
            toast("Сохранено и зашифровано", "success");
            setTimeout(() => location.reload(), 800);
          } catch (e) { toast("Ошибка: " + e.message, "error"); }
        }
      }
    }, "💾 Сохранить доступы"));
  }
  root.append(credsCard);

  // расписание + параметры
  const slotsCard = el("div", { class: "card" }, el("h2", {}, "📅 Расписание и параметры"),
    el("div", { class: "kv" },
      el("div", {}, "Постов в день"), el("div", {}, c.posts_per_day),
      el("div", {}, "Выбор статей"), el("div", {}, STRATEGY_RU[c.selection_strategy] || c.selection_strategy),
      el("div", {}, "Лимит символов"), el("div", {}, spec.max_chars + " (для одного поста)"),
      el("div", {}, "Поддерживаемое медиа"), el("div", {}, spec.media_kinds.join(", ") || "—"),
    ),
    el("div", { class: "spacer" }),
    el("h3", {}, "Слоты публикации (по локальному времени проекта)"),
    data.slots.length === 0
      ? el("div", { class: "muted" }, "Слоты ещё не настроены.")
      : el("table", {},
          el("thead", {}, el("tr", {},
            el("th", {}, "Время"),
            el("th", {}, "Активен"),
          )),
          el("tbody", {}, ...data.slots.map(s => el("tr", {},
            el("td", {}, s.time_local),
            el("td", {}, s.enabled ? "✓" : "—"),
          ))),
        ),
  );
  root.append(slotsCard);
  stamp();
});

// ---------- карточка статьи -------------------------------------------------

router.add("/projects/:pid/articles/:aid", async ({ pid, aid }) => {
  const root = $("#root");
  root.innerHTML = "";
  const data = await api(`/api/projects/${pid}/articles/${aid}`);
  const a = data.article;

  root.append(el("div", { class: "page-title" },
    el("div", {},
      el("h1", { style: "margin:0" }, a.chosen_headline || "Без заголовка"),
      el("div", { class: "muted", style: "margin-top:4px" },
        "Тема: " + (data.topic?.title || "—")),
    ),
    el("div", { class: "row" },
      langTag(a.language), pill(a.status),
      a.qa_score ? el("span", { class: "chip" }, "QA: " + a.qa_score + "/100") : null,
    ),
  ));

  if (data.media.length) {
    const gallery = el("div", { class: "row gap-lg", style: "flex-wrap:wrap" });
    for (const m of data.media) {
      gallery.append(el("img", { src: m.storage_url, class: "thumb" + (m.chosen ? " chosen" : ""),
                                  title: m.chosen ? "Выбрана для публикации" : "Запасной вариант" }));
    }
    root.append(el("div", { class: "card" },
      el("h2", {}, "🖼 Иллюстрации"),
      el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
        "Зелёная рамка — выбрано QA для публикации. Остальные — варианты для аудита."),
      gallery,
    ));
  }

  root.append(el("div", { class: "card markdown" },
    el("h2", {}, "📄 Текст статьи (универсальная версия)"),
    el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      "Эта версия — большая и универсальная. Под каждый канал её перепишет адаптер канала."),
    el("div", { html: renderMarkdown(a.body_full) }),
  ));

  root.append(el("div", { class: "card" },
    el("h2", {}, "🔁 История работы агентов над статьёй"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Когда"),
        el("th", {}, "Статус"),
        el("th", {}, "ID агента"),
      )),
      el("tbody", {}, ...data.agent_runs.map(r => el("tr", {},
        el("td", { class: "muted" }, fmtDate(r.started_at)),
        el("td", {}, pill(r.status)),
        el("td", {}, el("code", { class: "inline" }, r.agent_id)),
      ))),
    ),
  ));
  stamp();
});

// ---------- страница "о системе" -------------------------------------------

router.add("/about", () => {
  const root = $("#root");
  root.innerHTML = "";
  root.append(el("div", { class: "page-title" }, el("h1", { style: "margin:0" }, "О системе")));
  root.append(el("div", { class: "card markdown", html: `
  <h2>Что это такое</h2>
  <p><b>AiCrewStudio</b> — конструктор «команд ИИ-агентов» для контент-студий. Каждый проект — отдельный канал/блог со своей нишей. Внутри проекта работает команда из специализированных агентов: одни ищут темы, другие пишут статьи, третьи делают картинки, четвёртые публикуют.</p>

  <h2>Как устроен пайплайн</h2>
  <ul>
    <li><b>Сбор тем:</b> генератор предлагает темы → проверяльщик подтверждает их → ранжировщик расставляет по рейтингу.</li>
    <li><b>Написание статей</b> идёт параллельно русской и английской командами: исследователь → проверка фактов → автор → заголовки → промты картинок → QA.</li>
    <li><b>Публикация</b> в каждый канал: адаптер канала переписывает статью под нужный формат (TG, VK, X, Threads, Facebook…) и публикует через API канала.</li>
  </ul>

  <h2>Текущий режим</h2>
  <p>Сейчас система работает в <b>демо-режиме</b>: вместо настоящих API используются заглушки, которые отдают детерминированные правдоподобные ответы. Это нужно, чтобы вы могли посмотреть весь поток без затрат.</p>

  <h2>Как переключиться на реальные API</h2>
  <ol>
    <li>На сервере отредактируйте <code class="inline">/opt/aicrewstudio/.env</code>: <code class="inline">AICREW_LLM_PROVIDER=openai</code> и впишите <code class="inline">OPENAI_API_KEY</code>.</li>
    <li>На странице каждого канала введите токены — они зашифруются.</li>
    <li>Перезапустите службу: <code class="inline">systemctl restart aicrew</code>.</li>
  </ol>

  <h2>Поддерживаемые каналы</h2>
  <p><b>Текстовые:</b> Telegram, ВКонтакте, Facebook, Instagram, Одноклассники, Threads, X (Twitter), MAX.</p>
  <p><b>Видео:</b> YouTube Shorts, TikTok, Instagram Reels, VK Видео, Snapchat, RuTube.</p>
  <p>На каждой странице канала есть пошаговая инструкция, где взять ключи доступа.</p>
  ` }));
  stamp();
});

// ---------- старт -----------------------------------------------------------

router.start();
