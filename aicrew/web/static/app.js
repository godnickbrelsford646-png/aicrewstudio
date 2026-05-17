/* AiCrewStudio frontend – vanilla, no build step. */

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
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
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json();
}

const stamp = () => $("#last-update").textContent = "updated " + new Date().toLocaleTimeString();

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
        Promise.resolve(r.handler(params)).catch(err => {
          $("#root").append(el("div", { class: "card" }, "Error: " + err.message));
        });
        renderNav(hash);
        return;
      }
    }
    $("#root").innerHTML = "404";
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
  const links = [["#/projects", "Projects"], ["#/about", "About"]];
  const nav = $("#nav");
  nav.innerHTML = "";
  for (const [href, label] of links) {
    const a = el("a", { href, class: active.startsWith(href.slice(1)) ? "active" : "" }, label);
    nav.append(a);
  }
}

// ----------------------------------------------------------- helpers ------
function pill(status) {
  const map = {
    completed: "green", published: "green", qa_passed: "green",
    running: "yellow", scheduled: "yellow", drafting: "yellow", ranked: "blue",
    failed: "red",
  };
  return el("span", { class: "pill " + (map[status] || "gray") }, status);
}

function langTag(l) {
  return l ? el("span", { class: "lang " + (l === "ru" ? "ru" : (l === "en" ? "en" : "")) }, l) : "";
}

function renderMarkdown(md) {
  // very tiny: headings, bold, links, lists, paragraphs
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
    .replace(/(<li>.+<\/li>)/gs, "<ul>$1</ul>")
    .replace(/^/, "<p>") + "</p>";
}

// ------------------------------------------------- view: projects list ----

router.add("/projects", async () => {
  const root = $("#root");
  root.append(el("h1", {}, "Projects"));
  const data = await api("/api/projects");
  const grid = el("div", { class: "grid grid-3" });
  if (!data.projects.length) {
    grid.append(el("div", { class: "card muted" }, "No projects yet. Run `make seed` then refresh."));
  }
  for (const p of data.projects) {
    const langs = JSON.parse(p.language_modes || "[]");
    grid.append(el(
      "a", { href: `#/projects/${p.id}`, style: "text-decoration:none;color:inherit" },
      el("div", { class: "card" },
        el("div", { class: "row between" },
          el("h2", {}, p.name),
          p.is_enabled ? pill("running") : pill("scheduled")),
        el("div", { class: "muted", style: "font-size:13px;margin-bottom:8px" }, p.niche),
        el("div", { class: "row" }, langs.map(l => langTag(l))),
        el("div", { class: "spacer" }),
        el("div", { class: "kv" },
          el("div", {}, "topics/day"), el("div", {}, p.daily_topics_target),
          el("div", {}, "articles/day"), el("div", {}, p.daily_articles_target),
          el("div", {}, "budget"), el("div", {}, "$" + p.budget_usd_month + "/mo"),
        ),
      ),
    ));
  }
  root.append(grid);
  stamp();
});

// ------------------------------------------------- view: project detail ----

router.add("/projects/:pid", async ({ pid }) => {
  const root = $("#root");
  const data = await api(`/api/projects/${pid}`);
  const p = data.project;
  root.append(el("div", { class: "row between" },
    el("h1", {}, p.name),
    el("div", { class: "row" },
      el("button", { class: "ghost", on: { click: () => runPhase(pid, "topics") } }, "Run topics"),
      el("button", { class: "ghost", on: { click: () => runPhase(pid, "articles") } }, "Run articles"),
      el("button", { class: "ghost", on: { click: () => runPhase(pid, "publish") } }, "Run publish"),
      el("button", { on: { click: () => runPhase(pid, "full") } }, "Run full pipeline"),
    ),
  ));
  root.append(el("div", { class: "muted" }, p.description));

  const tabs = ["Pipeline", "Agents", "Topics", "Articles", "Channels", "Posts"];
  const tabbar = el("div", { class: "tabs" });
  const view = el("div", {});
  let active = location.hash.split("?tab=")[1] || "Pipeline";
  for (const t of tabs) {
    const node = el("div", { class: "tab" + (t === active ? " active" : ""), on: {
      click: () => { active = t; render(); },
    }}, t);
    tabbar.append(node);
  }
  root.append(tabbar);
  root.append(view);

  function render() {
    view.innerHTML = "";
    for (const tab of tabbar.children) tab.classList.toggle("active", tab.textContent === active);
    if (active === "Pipeline") view.append(renderPipeline(data));
    if (active === "Agents") view.append(renderAgents(pid, data.agents));
    if (active === "Topics") view.append(renderTopics(data.topics));
    if (active === "Articles") view.append(renderArticles(pid, data.articles));
    if (active === "Channels") view.append(renderChannels(pid, data.channels));
    if (active === "Posts") view.append(renderPosts(pid));
  }
  render();
  stamp();
});

async function runPhase(pid, phase) {
  const map = { topics: "runs/topics", articles: "runs/articles", publish: "runs/publish", full: "runs/full" };
  try {
    const res = await api(`/api/projects/${pid}/${map[phase]}`, { method: "POST", body: {} });
    alert(phase + " run started: " + JSON.stringify(res));
    location.reload();
  } catch (e) { alert("Error: " + e.message); }
}

function renderPipeline(data) {
  const wrap = el("div", { class: "grid grid-2" });
  const phases = [
    ["1. Topic phase", "Generator → Validator → Ranker (loop)"],
    ["2. Article phase RU", "Researcher → ResearchValidator → Writer → Headlines → Image prompts → QA"],
    ["2. Article phase EN", "Researcher → ResearchValidator → Writer → Headlines → Image prompts → QA"],
    ["3. Publication phase", "ChannelRewriter (text) → PublisherAdapter; video channels skipped in MVP"],
  ];
  for (const [title, desc] of phases) {
    wrap.append(el("div", { class: "card" },
      el("h2", {}, title),
      el("div", { class: "muted" }, desc),
    ));
  }
  const runsCard = el("div", { class: "card" },
    el("h2", {}, "Recent pipeline runs"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "id"), el("th", {}, "kind"), el("th", {}, "status"),
        el("th", {}, "started"), el("th", {}, "finished"))),
      el("tbody", {}, data.pipeline_runs.map(r => el("tr", {},
        el("td", {}, el("code", { class: "inline" }, r.id)),
        el("td", {}, r.kind),
        el("td", {}, pill(r.status)),
        el("td", {}, r.started_at || ""),
        el("td", {}, r.finished_at || ""),
      ))),
    ),
  );
  return el("div", {}, wrap, el("div", { class: "spacer" }), runsCard);
}

function renderAgents(pid, agents) {
  const wrap = el("div", { class: "grid grid-2" });
  for (const a of agents) {
    wrap.append(el("div", { class: "card" },
      el("div", { class: "row between" },
        el("h2", {}, a.display_name),
        el("div", { class: "row" },
          langTag(a.language),
          el("span", { class: "pill gray" }, a.role),
        ),
      ),
      el("div", { class: "muted", style: "font-size:13px;margin-bottom:8px" }, a.description),
      el("div", { class: "kv" },
        el("div", {}, "model"), el("div", {}, el("code", { class: "inline" }, a.model)),
        el("div", {}, "temperature"), el("div", {}, a.temperature),
        el("div", {}, "max_tokens"), el("div", {}, a.max_tokens),
        el("div", {}, "tools"), el("div", {}, a.tools_enabled),
      ),
      el("div", { class: "spacer" }),
      el("a", { href: "#/agents/" + a.id, class: "btn", style: "text-decoration:none" }, "Open agent →"),
    ));
  }
  return wrap;
}

function renderTopics(topics) {
  if (!topics.length) return el("div", { class: "card muted" }, "No topics yet. Run topic phase.");
  const tbody = el("tbody", {}, topics.map(t => {
    const scores = JSON.parse(t.scores || "{}");
    return el("tr", {},
      el("td", {}, t.title),
      el("td", {}, pill(t.status)),
      el("td", {}, Number(t.score_total).toFixed(2)),
      el("td", { class: "muted" }, Object.entries(scores).map(([k, v]) => `${k}:${v}`).join(" ")),
    );
  }));
  return el("div", { class: "card" }, el("h2", {}, "Topics"), el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "title"), el("th", {}, "status"), el("th", {}, "score"), el("th", {}, "details"))),
    tbody,
  ));
}

function renderArticles(pid, articles) {
  if (!articles.length) return el("div", { class: "card muted" }, "No articles yet.");
  return el("div", { class: "card" }, el("h2", {}, "Articles"), el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "headline"), el("th", {}, "lang"),
      el("th", {}, "status"), el("th", {}, "qa"), el("th", {}, ""))),
    el("tbody", {}, articles.map(a => el("tr", {},
      el("td", {}, a.chosen_headline || el("span", { class: "muted" }, a.id)),
      el("td", {}, langTag(a.language)),
      el("td", {}, pill(a.status)),
      el("td", {}, a.qa_score),
      el("td", {}, el("a", { href: "#/projects/" + pid + "/articles/" + a.id }, "open →")),
    ))),
  ));
}

function renderChannels(pid, channels) {
  const wrap = el("div", { class: "grid grid-2" });
  for (const c of channels) {
    wrap.append(el("div", { class: "card" },
      el("div", { class: "row between" },
        el("h2", {}, c.name),
        el("div", { class: "row" },
          langTag(c.language),
          el("span", { class: "pill " + (c.is_video ? "blue" : "gray") }, c.is_video ? "video" : "text"),
          el("span", { class: "pill gray" }, c.kind),
          c.is_enabled ? pill("running") : pill("scheduled"),
        ),
      ),
      el("div", { class: "kv" },
        el("div", {}, "posts/day"), el("div", {}, c.posts_per_day),
        el("div", {}, "selection"), el("div", {}, c.selection_strategy),
      ),
      el("div", { class: "spacer" }),
      el("a", { href: "#/channels/" + c.id, class: "btn", style: "text-decoration:none" }, "Configure →"),
    ));
  }
  return wrap;
}

async function renderPosts(pid) {
  const wrap = el("div", { class: "card" }, el("h2", {}, "Posts"), el("div", {}, "Loading…"));
  api(`/api/projects/${pid}/posts`).then(({ posts }) => {
    wrap.innerHTML = "";
    wrap.append(el("h2", {}, "Posts"));
    if (!posts.length) { wrap.append(el("div", { class: "muted" }, "No posts yet.")); return; }
    wrap.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "channel"), el("th", {}, "lang"), el("th", {}, "status"),
        el("th", {}, "headline"), el("th", {}, "url"))),
      el("tbody", {}, posts.map(p => el("tr", {},
        el("td", {}, p.channel_name + " (" + p.channel_kind + ")"),
        el("td", {}, langTag(p.language)),
        el("td", {}, pill(p.status)),
        el("td", {}, p.article_headline || ""),
        el("td", {}, p.external_url ? el("a", { href: p.external_url, target: "_blank" }, "open ↗") : "-"),
      ))),
    ));
  }).catch(e => { wrap.append(el("div", { class: "muted" }, e.message)); });
  return wrap;
}

// ------------------------------------------------- view: agent detail ------

router.add("/agents/:aid", async ({ aid }) => {
  const root = $("#root");
  const data = await api("/api/agents/" + aid);
  const a = data.agent;
  let params = a.params; try { params = JSON.parse(a.params); } catch {}
  root.append(el("div", { class: "row between" },
    el("h1", {}, a.display_name),
    el("div", { class: "row" }, langTag(a.language), el("span", { class: "pill gray" }, a.role)),
  ));
  const form = el("div", { class: "card" },
    el("h2", {}, "Settings"),
    field("display_name", a.display_name),
    field("model", a.model),
    field("temperature", a.temperature, "number", { step: "0.1" }),
    field("max_tokens", a.max_tokens, "number"),
    fieldArea("prompt_template", a.prompt_template),
    fieldArea("params (JSON)", JSON.stringify(params, null, 2), "params"),
    el("div", { class: "row" },
      el("button", { on: { click: save } }, "Save"),
      el("button", { class: "ghost", on: { click: () => location.reload() } }, "Reset"),
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
    } catch (e) { alert("params: not valid JSON"); return; }
    await api("/api/agents/" + aid, { method: "PATCH", body });
    alert("Saved.");
  }
  function field(name, value, type = "text", extra = {}) {
    return el("label", { class: "field" }, name,
      el("input", { type, value: value ?? "", "data-name": name, ...extra }));
  }
  function fieldArea(name, value, dataName) {
    return el("label", { class: "field" }, name,
      el("textarea", { value: value ?? "", "data-name": dataName || name }));
  }
  root.append(form);

  const runsCard = el("div", { class: "card" }, el("h2", {}, "Recent runs"));
  if (!data.runs.length) {
    runsCard.append(el("div", { class: "muted" }, "No runs yet."));
  } else {
    runsCard.append(el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "started"), el("th", {}, "status"), el("th", {}, "tokens"),
        el("th", {}, "cost"), el("th", {}, "topic"), el("th", {}, ""))),
      el("tbody", {}, data.runs.map(r => {
        const tr = el("tr", {},
          el("td", {}, r.started_at || ""),
          el("td", {}, pill(r.status)),
          el("td", {}, `${r.tokens_in}/${r.tokens_out}`),
          el("td", {}, "$" + Number(r.cost_usd).toFixed(4)),
          el("td", {}, r.topic_id || r.article_id || ""),
          el("td", {}, el("button", { class: "ghost", on: { click: () => toggleRun(tr, r) } }, "details")),
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
        el("h3", {}, "Rendered prompt"),
        el("pre", { class: "code" }, r.rendered_prompt || ""),
        el("h3", {}, "Inputs"),
        el("pre", { class: "code" }, JSON.stringify(inputs, null, 2)),
        el("h3", {}, "Output"),
        el("pre", { class: "code" }, JSON.stringify(output, null, 2)),
      ));
    tr.after(detail);
  }
  function safeParse(s) { try { return JSON.parse(s); } catch { return s; } }
});

// ------------------------------------------------- view: channel detail ----

router.add("/channels/:cid", async ({ cid }) => {
  const root = $("#root");
  const data = await api("/api/channels/" + cid);
  const c = data.channel; const spec = data.spec;
  root.append(el("div", { class: "row between" },
    el("h1", {}, c.name),
    el("div", { class: "row" },
      langTag(c.language),
      el("span", { class: "pill " + (c.is_video ? "blue" : "gray") }, c.is_video ? "video" : "text"),
      el("span", { class: "pill gray" }, c.kind),
    ),
  ));

  // Connect guide always visible
  root.append(el("div", { class: "guide-box markdown", html: renderMarkdown(spec.connect_guide_md) }));

  // Credentials editor
  const fields = spec.credentials_fields;
  const credsForm = el("div", { class: "card" }, el("h2", {}, "Credentials"));
  if (!fields.length) {
    credsForm.append(el("div", { class: "muted" },
      "This channel does not require credentials. Posts are stored as drafts."));
  } else {
    const inputs = {};
    for (const f of fields) {
      const masked = (c.credentials_masked || {})[f.key] || "";
      const inp = el("input", { type: "text", placeholder: masked || f.hint, "data-key": f.key });
      inputs[f.key] = inp;
      credsForm.append(el("label", { class: "field" }, f.key + " — " + f.hint, inp));
    }
    credsForm.append(el("button", {
      on: {
        click: async () => {
          const credentials = {};
          for (const [k, inp] of Object.entries(inputs))
            if (inp.value) credentials[k] = inp.value;
          await api(`/api/channels/${cid}/credentials`, {
            method: "PATCH", body: { credentials },
          });
          alert("Credentials saved (encrypted).");
          location.reload();
        }
      }
    }, "Save credentials"));
  }
  root.append(credsForm);

  // Slots & params (read-only in MVP)
  const slotsCard = el("div", { class: "card" }, el("h2", {}, "Schedule"),
    el("div", { class: "kv" },
      el("div", {}, "posts/day"), el("div", {}, c.posts_per_day),
      el("div", {}, "selection"), el("div", {}, c.selection_strategy),
      el("div", {}, "max_chars"), el("div", {}, spec.max_chars),
      el("div", {}, "media"), el("div", {}, spec.media_kinds.join(", ")),
    ),
    el("div", { class: "spacer" }),
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "time (local)"), el("th", {}, "enabled"))),
      el("tbody", {}, data.slots.map(s => el("tr", {},
        el("td", {}, s.time_local), el("td", {}, s.enabled ? "yes" : "no"),
      ))),
    ),
  );
  root.append(slotsCard);
  stamp();
});

// ------------------------------------------------- view: article detail ---

router.add("/projects/:pid/articles/:aid", async ({ pid, aid }) => {
  const root = $("#root");
  const data = await api(`/api/projects/${pid}/articles/${aid}`);
  const a = data.article;
  root.append(el("div", { class: "row between" },
    el("h1", {}, a.chosen_headline || a.id),
    el("div", { class: "row" }, langTag(a.language), pill(a.status)),
  ));
  if (data.media.length) {
    const gallery = el("div", { class: "row" });
    for (const m of data.media) {
      const img = el("img", { src: m.storage_url, class: "thumb" + (m.chosen ? " chosen" : "") });
      gallery.append(img);
    }
    root.append(el("div", { class: "card" }, el("h2", {}, "Images"), gallery));
  }
  root.append(el("div", { class: "card markdown" },
    el("h2", {}, "Body"),
    el("div", { html: renderMarkdown(a.body_full) }),
  ));
  root.append(el("div", { class: "card" },
    el("h2", {}, "Agent runs for this article"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "started"), el("th", {}, "status"), el("th", {}, "agent_id"))),
      el("tbody", {}, data.agent_runs.map(r => el("tr", {},
        el("td", {}, r.started_at), el("td", {}, pill(r.status)), el("td", {}, r.agent_id),
      ))),
    ),
  ));
});

// ------------------------------------------------------- view: about ------

router.add("/about", () => {
  const root = $("#root");
  root.append(el("h1", {}, "About"));
  root.append(el("div", { class: "card markdown", html: `
  <p><b>AiCrewStudio</b> — конструктор команд ИИ-агентов для контент-студий.</p>
  <h2>Что в этой сборке</h2>
  <ul>
    <li>Все провайдеры — <b>mock</b>: можно прогнать полный цикл без интернета.</li>
    <li>Полный пайплайн: темы → проверка → ранжирование → ресёрч → статья → заголовки → картинки → QA → публикация.</li>
    <li>Параллельные ветки <b>RU</b> и <b>EN</b>.</li>
    <li>Все 14 каналов: TG / VK / MAX / FB / IG / OK / Threads / X / IG Reels / Snap / TikTok / YT Shorts / VK Video / RuTube.</li>
    <li>Видео-команда — пока только агент-сценарист (готов к подключению image2video / TTS / монтажа).</li>
  </ul>
  <h2>Как переключиться на реальные API</h2>
  <p>В <code class="inline">.env</code> установите <code class="inline">AICREW_LLM_PROVIDER=openai</code>,
     <code class="inline">AICREW_IMAGE_PROVIDER=...</code>, и вставьте ключи. В каждом канале — заполните credentials через UI.</p>
  ` }));
});

// ------------------------------------------------------------ start -------

router.start();
