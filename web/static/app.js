const loginView = document.querySelector("#login-view");
const appView = document.querySelector("#app-view");
const loginForm = document.querySelector("#login-form");
const loginMessage = document.querySelector("#login-message");
const keywordForm = document.querySelector("#keyword-form");
const keywordMessage = document.querySelector("#keyword-message");
const freeNoteForm = document.querySelector("#free-note-form");
const freeNoteMessage = document.querySelector("#free-note-message");
const freeNoteText = document.querySelector("#free-note-text");
const freeNoteCount = document.querySelector("#free-note-count");
const reviewList = document.querySelector("#review-list");
const reportsList = document.querySelector("#reports-list");
const knowledgeList = document.querySelector("#knowledge-list");
const seedsList = document.querySelector("#seeds-list");
const logoutButton = document.querySelector("#logout-button");
const refreshButton = document.querySelector("#refresh-button");
const sharedReaderKind = document.querySelector("#shared-reader-kind");
const sharedReaderTitle = document.querySelector("#shared-reader-title");
const sharedReaderMeta = document.querySelector("#shared-reader-meta");
const sharedReaderContent = document.querySelector("#shared-reader-content");
const lists = {
  report: reportsList,
  knowledge: knowledgeList,
  seed: seedsList,
};
const kindLabels = {
  report: "最近日报",
  knowledge: "知识节点",
  seed: "点子种子",
};
const kindEyebrows = {
  report: "Reports",
  knowledge: "Knowledge",
  seed: "Seeds",
};

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
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return map[char];
  });
}

function setMessage(node, text, type = "") {
  node.textContent = text;
  node.classList.toggle("success", type === "success");
  node.classList.toggle("error", type === "error");
}

function setFormBusy(form, busy) {
  for (const button of form.querySelectorAll("button")) {
    button.disabled = busy;
  }
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

function syncSummary(sync) {
  if (!sync) {
    return "";
  }
  if (sync.pushed) {
    return `；已同步到 GitHub（${sync.commit}）`;
  }
  if (sync.committed) {
    return `；已本地提交，但推送失败：${sync.message}`;
  }
  if (sync.enabled === false) {
    return "；自动 Git 同步未启用";
  }
  return `；Git 同步未完成：${sync.message}`;
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
    button.className = "list-item";
    button.dataset.path = item.path;
    button.dataset.kind = kind;
    button.dataset.format = item.format || "markdown";
    button.dataset.status = item.status || item.format || "Markdown";
    button.dataset.title = item.title || "";
    button.dataset.modified = item.modified || "";
    button.innerHTML = `
      <span class="item-title">${escapeHtml(item.title)}</span>
      <span class="item-meta">${escapeHtml(item.path)} / ${escapeHtml(item.status || item.format || "Markdown")} / ${escapeHtml(item.modified)}</span>
    `;
    button.addEventListener("click", () => {
      loadFile(kind, item.path, item.status || "");
    });
    container.append(button);
  }
}

function candidateText(item) {
  const payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  if (item.kind === "weight_change_candidate") {
    const target = payload.target || payload.keyword || payload.node_title || payload.path || "未命名对象";
    const fromWeight = payload.current_weight || payload.from_weight || "?";
    const toWeight = payload.suggested_weight || payload.new_weight || payload.to_weight || "?";
    const reason = payload.reason || payload.evidence || payload.text || "";
    return `${target}: 建议权重 ${fromWeight} -> ${toWeight}${reason ? `；理由：${reason}` : ""}`;
  }
  return payload.text || payload.title || payload.summary || item.id || "";
}

function renderReviewQueue(items = []) {
  if (!reviewList) {
    return;
  }
  reviewList.replaceChildren();
  if (!items.length) {
    reviewList.append(emptyMessage("暂无待确认项。"));
    return;
  }
  for (const item of items) {
    const card = document.createElement("section");
    card.className = "review-item";
    const text = candidateText(item);
    card.innerHTML = `
      <div class="review-item-head">
        <span>${escapeHtml(item.kind || "candidate")}</span>
        <small>${escapeHtml(item.created_at || "")}</small>
      </div>
      <textarea rows="3">${escapeHtml(text)}</textarea>
      <div class="review-actions">
        <button type="button" data-action="accept">接受</button>
        <button type="button" class="secondary" data-action="reject">拒绝</button>
      </div>
    `;
    for (const button of card.querySelectorAll("button")) {
      button.addEventListener("click", async () => {
        const action = button.dataset.action;
        const editedText = card.querySelector("textarea").value.trim();
        try {
          await api("/api/review", {
            method: "POST",
            body: JSON.stringify({
              candidate_id: item.id,
              action,
              edited_payload: { ...(item.payload || {}), text: editedText, title: editedText },
            }),
          });
          await loadOverview();
        } catch (error) {
          card.append(emptyMessage(error.message));
        }
      });
    }
    reviewList.append(card);
  }
}

function markSelected(kind, path) {
  for (const [listKind, container] of Object.entries(lists)) {
    if (!container) {
      continue;
    }
    for (const button of container.querySelectorAll("button")) {
      button.classList.toggle("selected", listKind === kind && button.dataset.path === path);
    }
  }
}

function setReaderHeader(kind, title, meta = "") {
  sharedReaderKind.textContent = kindEyebrows[kind] || "Reader";
  sharedReaderTitle.textContent = title || "选择内容后阅读";
  sharedReaderMeta.textContent = meta || "左侧选择日报、知识节点或点子种子。";
}

async function loadOverview() {
  const overview = await api("/api/overview");
  renderList(reportsList, overview.reports, "report", "还没有日报。");
  renderList(knowledgeList, overview.knowledge, "knowledge", "还没有知识节点。");
  renderList(seedsList, overview.seeds, "seed", "还没有点子种子。");
  renderReviewQueue(overview.review_queue || []);
}

async function loadFile(kind, path, status = "") {
  if (!sharedReaderContent) {
    return;
  }
  markSelected(kind, path);
  const selected = lists[kind]?.querySelector(`button[data-path="${CSS.escape(path)}"]`);
  const title = selected?.dataset.title || kindLabels[kind] || "阅读内容";
  const statusText = selected?.dataset.status || "Markdown";
  const modified = selected?.dataset.modified || "";
  const meta = modified ? `${kindLabels[kind] || "文件"} / ${statusText} / ${modified}` : `${kindLabels[kind] || "文件"} / ${path}`;
  setReaderHeader(kind, title, meta);
  sharedReaderContent.classList.remove("empty");
  sharedReaderContent.innerHTML = "<p class=\"empty\">正在读取文件...</p>";
  try {
    const file = await api(`/api/file?kind=${encodeURIComponent(kind)}&path=${encodeURIComponent(path)}`);
    if (file.format === "pdf") {
      const url = escapeHtml(file.url);
      sharedReaderContent.innerHTML = `
        <p><a href="${url}" target="_blank" rel="noopener">打开 PDF 报告</a></p>
        <iframe class="pdf-frame" src="${url}" title="${escapeHtml(file.title || "PDF 报告")}"></iframe>
      `;
      return;
    }
    const notice = status === "PDF 尚未生成或编译失败"
      ? "<p class=\"message error\">PDF 尚未生成或编译失败。下面显示 report.tex 供检查。</p>"
      : "";
    sharedReaderContent.innerHTML = renderMarkdown(file.content);
    if (notice) {
      sharedReaderContent.innerHTML = notice + sharedReaderContent.innerHTML;
    }
  } catch (error) {
    sharedReaderContent.innerHTML = `<p class="message error">${escapeHtml(error.message)}</p>`;
  }
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
  setMessage(loginMessage, "");
  const password = document.querySelector("#password").value;
  setFormBusy(loginForm, true);
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
  } finally {
    setFormBusy(loginForm, false);
  }
});

keywordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(keywordMessage, "");
  const payload = {
    keywords: document.querySelector("#keywords").value,
    supplemental_info: document.querySelector("#supplemental-info").value,
    weight: document.querySelector("#weight").value,
  };
  setFormBusy(keywordForm, true);
  try {
    const result = await api("/api/keywords", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setMessage(keywordMessage, `已追加 ${result.count} 条关键词到 ${result.path}${syncSummary(result.sync)}`, "success");
    document.querySelector("#keywords").value = "";
    document.querySelector("#supplemental-info").value = "";
    document.querySelector("#weight").value = "";
  } catch (error) {
    setMessage(keywordMessage, error.message, "error");
  } finally {
    setFormBusy(keywordForm, false);
  }
});

freeNoteForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(freeNoteMessage, "");
  const payload = {
    text: freeNoteText.value,
  };
  setFormBusy(freeNoteForm, true);
  try {
    const result = await api("/api/free-notes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setMessage(freeNoteMessage, `已追加随心记到 ${result.path}${syncSummary(result.sync)}`, "success");
    freeNoteText.value = "";
    updateFreeNoteCount();
  } catch (error) {
    setMessage(freeNoteMessage, error.message, "error");
  } finally {
    setFormBusy(freeNoteForm, false);
  }
});

function updateFreeNoteCount() {
  if (!freeNoteText || !freeNoteCount) {
    return;
  }
  freeNoteCount.textContent = `${freeNoteText.value.length} / ${freeNoteText.maxLength || 8000}`;
}

freeNoteText.addEventListener("input", updateFreeNoteCount);

logoutButton.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  showLogin();
});

refreshButton.addEventListener("click", async () => {
  await loadOverview();
});

boot();
updateFreeNoteCount();
