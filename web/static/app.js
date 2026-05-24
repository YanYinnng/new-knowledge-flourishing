const loginView = document.querySelector("#login-view");
const appView = document.querySelector("#app-view");
const loginForm = document.querySelector("#login-form");
const loginMessage = document.querySelector("#login-message");
const keywordForm = document.querySelector("#keyword-form");
const keywordMessage = document.querySelector("#keyword-message");
const reportsList = document.querySelector("#reports-list");
const knowledgeList = document.querySelector("#knowledge-list");
const seedsList = document.querySelector("#seeds-list");
const reader = document.querySelector("#reader");
const logoutButton = document.querySelector("#logout-button");
const refreshButton = document.querySelector("#refresh-button");

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "请求失败。");
  }
  return data;
}

function showLogin(message = "") {
  loginView.hidden = false;
  appView.hidden = true;
  loginMessage.textContent = message;
  loginMessage.classList.toggle("error", Boolean(message));
}

function showApp() {
  loginView.hidden = true;
  appView.hidden = false;
  loginMessage.textContent = "";
  loginMessage.classList.remove("error");
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return map[char];
  });
}

function renderMarkdown(markdown) {
  const lines = markdown.split(/\r?\n/);
  const html = [];
  let inList = false;

  for (const line of lines) {
    const escaped = escapeHtml(line);
    if (/^###\s+/.test(line)) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<h3>${escaped.replace(/^###\s+/, "")}</h3>`);
    } else if (/^##\s+/.test(line)) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<h2>${escaped.replace(/^##\s+/, "")}</h2>`);
    } else if (/^#\s+/.test(line)) {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<h1>${escaped.replace(/^#\s+/, "")}</h1>`);
    } else if (/^\s*-\s+/.test(line)) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${escaped.replace(/^\s*-\s+/, "")}</li>`);
    } else if (line.trim() === "") {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
    } else {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
      html.push(`<p>${escaped}</p>`);
    }
  }

  if (inList) {
    html.push("</ul>");
  }
  return html.join("");
}

function emptyMessage(text) {
  const node = document.createElement("p");
  node.className = "empty";
  node.textContent = text;
  return node;
}

function renderList(container, items, kind, emptyText) {
  container.replaceChildren();
  if (!items.length) {
    container.append(emptyMessage(emptyText));
    return;
  }

  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.path = item.path;
    button.dataset.kind = kind;
    button.innerHTML = `
      <span class="item-title">${escapeHtml(item.title)}</span>
      <span class="item-meta">${escapeHtml(item.path)} · ${escapeHtml(item.modified)}</span>
    `;
    button.addEventListener("click", () => loadFile(kind, item.path));
    container.append(button);
  }
}

async function loadOverview() {
  const overview = await api("/api/overview");
  renderList(reportsList, overview.reports, "report", "还没有日报。");
  renderList(knowledgeList, overview.knowledge, "knowledge", "还没有知识节点。");
  renderList(seedsList, overview.seeds, "seed", "还没有点子种子。");
}

async function loadFile(kind, path) {
  const file = await api(`/api/file?kind=${encodeURIComponent(kind)}&path=${encodeURIComponent(path)}`);
  reader.classList.remove("empty");
  reader.innerHTML = renderMarkdown(file.content);
}

async function boot() {
  try {
    const status = await api("/api/auth/status");
    if (status.authenticated) {
      showApp();
      await loadOverview();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin(error.message);
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginMessage.textContent = "";
  loginMessage.classList.remove("error");
  const password = document.querySelector("#password").value;
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    document.querySelector("#password").value = "";
    showApp();
    await loadOverview();
  } catch (error) {
    showLogin(error.message);
  }
});

keywordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  keywordMessage.textContent = "";
  keywordMessage.classList.remove("error");
  const payload = {
    keywords: document.querySelector("#keywords").value,
    context: document.querySelector("#context").value,
    weight: document.querySelector("#weight").value,
  };
  try {
    const result = await api("/api/keywords", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    let syncText = "";
    if (result.sync) {
      if (result.sync.pushed) {
        syncText = `；已同步到 GitHub（${result.sync.commit}）`;
      } else if (result.sync.committed) {
        syncText = `；已本地提交，但推送失败：${result.sync.message}`;
      } else if (result.sync.enabled === false) {
        syncText = "；自动 Git 同步未启用";
      } else {
        syncText = `；Git 同步未完成：${result.sync.message}`;
      }
    }
    keywordMessage.textContent = `已追加 ${result.count} 条到 ${result.path}${syncText}`;
    document.querySelector("#keywords").value = "";
    document.querySelector("#context").value = "";
    document.querySelector("#weight").value = "";
  } catch (error) {
    keywordMessage.textContent = error.message;
    keywordMessage.classList.add("error");
  }
});

logoutButton.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  showLogin();
});

refreshButton.addEventListener("click", async () => {
  await loadOverview();
});

boot();
