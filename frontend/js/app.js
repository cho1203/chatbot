const UI_KEY = "fanuc_ui_mode";
const MACHINE_KEY = "fanuc_machine";
const SESSION_KEY = "fanuc_chat_session";
const TAB_KEY = "fanuc_tab";

const TAB_QUESTIONS = {
  alarm: [
    ["알람 410", "알람 410은 무슨 뜻인가요?"],
    ["알람 411", "알람 411은 무슨 뜻인가요?"],
    ["오버트래블 510", "알람 510은 무슨 뜻인가요?"],
    ["비상정지", "비상정지 알람이 났어요"],
  ],
  offset: [
    ["G43", "G43이 무엇인가요?"],
    ["G41", "G41 공구경 보정"],
    ["G54", "G54와 G55 차이가 뭔가요?"],
    ["공구 길이", "공구 길이 보정 방법"],
  ],
  recover: [
    ["비상정지 복구", "비상정지 후 어떻게 복구하나요?"],
    ["원점 복귀", "원점 복귀는 어떻게 하나요?"],
    ["프로그램 재개", "알람 후 프로그램 재개"],
  ],
};

const chatEl = document.getElementById("chat");
const formEl = document.getElementById("form");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const suggestEl = document.getElementById("suggest");
const suggestWorkshopEl = document.getElementById("suggest-workshop");
const uiToggleEl = document.getElementById("ui-toggle");
const headerSubEl = document.getElementById("header-sub");
const machineBarEl = document.getElementById("machine-bar");
const tabsEl = document.getElementById("tabs");

function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function uiMode() {
  return localStorage.getItem(UI_KEY) === "classic" ? "classic" : "workshop";
}

function machine() {
  return localStorage.getItem(MACHINE_KEY) || "mill_0i";
}

function currentTab() {
  return localStorage.getItem(TAB_KEY) || "alarm";
}

function applyMode() {
  const mode = uiMode();
  document.body.classList.toggle("classic", mode === "classic");
  document.body.classList.toggle("workshop", mode === "workshop");
  uiToggleEl.textContent = mode === "classic" ? "새 화면" : "이전 화면";
  headerSubEl.textContent =
    mode === "classic"
      ? "알람 · G/M 코드 · 보정 · 운전 복구"
      : "현장 조수 · 알람 카드 · 기계별 답변";
  renderWorkshopChips();
}

function renderWorkshopChips() {
  const tab = currentTab();
  tabsEl.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("is-on", btn.dataset.tab === tab);
  });
  machineBarEl.querySelectorAll(".machine-btn").forEach((btn) => {
    btn.classList.toggle("is-on", btn.dataset.machine === machine());
  });
  suggestWorkshopEl.innerHTML = "";
  for (const [label, question] of TAB_QUESTIONS[tab] || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.q = question;
    btn.textContent = label;
    suggestWorkshopEl.appendChild(btn);
  }
}

function addPlainBubble(role, text, sources) {
  const wrap = document.createElement("article");
  wrap.className = `bubble ${role}`;
  const missing = role === "bot" && text.startsWith("매뉴얼에 없음");
  if (!missing) {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = role === "user" ? "질문" : uiMode() === "workshop" ? "현장 조수" : "매뉴얼 답변";
    wrap.appendChild(meta);
  }
  wrap.appendChild(document.createTextNode(text));
  if (sources && sources.length) {
    const src = document.createElement("div");
    src.className = "sources";
    src.textContent = "출처: " + sources.map((item) => item.title).join(", ");
    wrap.appendChild(src);
  }
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addCardBubble(data) {
  const card = data.card || {};
  const wrap = document.createElement("article");
  wrap.className = "bubble bot";
  const missing = card.title === "매뉴얼에 없음";
  if (!missing) {
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = "현장 조수";
    wrap.appendChild(meta);
  }

  if (card.badges && card.badges.length) {
    const row = document.createElement("div");
    row.className = "badge-row";
    card.badges.forEach((code) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = code;
      row.appendChild(badge);
    });
    wrap.appendChild(row);
  }

  if (card.title) {
    const title = document.createElement("strong");
    title.textContent = card.title;
    wrap.appendChild(title);
  }

  if (card.lead) {
    const lead = document.createElement("p");
    lead.className = "machine-note";
    lead.style.marginTop = "6px";
    lead.textContent = card.lead;
    wrap.appendChild(lead);
  }

  if (card.cause || card.action || card.caution) {
    const grid = document.createElement("dl");
    grid.className = "card-grid";
    const rows = [
      ["원인", card.cause],
      ["지금 할 일", card.action],
      ["주의", card.caution],
    ];
    rows.forEach(([label, value]) => {
      if (!value) return;
      const box = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      box.appendChild(dt);
      box.appendChild(dd);
      grid.appendChild(box);
    });
    wrap.appendChild(grid);
  }

  if (card.note) {
    const note = document.createElement("p");
    note.className = "machine-note";
    note.textContent = card.note;
    wrap.appendChild(note);
  }

  if (data.sources && data.sources.length) {
    const src = document.createElement("div");
    src.className = "sources";
    src.textContent = "출처: " + data.sources.map((item) => item.title).join(", ");
    wrap.appendChild(src);
  }

  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}

async function sendMessage(text, origin = "chat") {
  const message = text.trim();
  if (!message) return;

  addPlainBubble("user", message);
  inputEl.value = "";
  sendEl.disabled = true;
  const mode = uiMode();
  const source = origin === "chip" ? "chip" : "chat";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: sessionId(),
        machine: machine(),
        mode,
        source,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "서버 오류");
    }
    if (mode === "workshop" && source === "chip" && data.card) {
      addCardBubble(data);
    } else {
      addPlainBubble("bot", data.answer, data.sources);
    }
  } catch (err) {
    addPlainBubble(
      "bot",
      "서버에 연결하지 못했습니다. 8787 포트에서 백엔드가 실행 중인지 확인하세요.\n" + err.message
    );
  } finally {
    sendEl.disabled = false;
    inputEl.focus();
  }
}

function welcome() {
  chatEl.innerHTML = "";
  if (uiMode() === "classic") {
    addPlainBubble(
      "bot",
      "FANUC 매뉴얼 챗봇입니다.\n알람 번호(예: 410), G/M 코드, 공구 보정, 좌표계, 비상정지 복구를 질문해 주세요."
    );
  } else {
    addPlainBubble(
      "bot",
      "기계부터 고르세요. 아래 버튼은 원인·지금 할 일·주의 카드로 답합니다.\n직접 질문하면 매뉴얼 설명으로 답합니다."
    );
  }
}

uiToggleEl.addEventListener("click", () => {
  localStorage.setItem(UI_KEY, uiMode() === "classic" ? "workshop" : "classic");
  applyMode();
  welcome();
});

machineBarEl.addEventListener("click", (event) => {
  const btn = event.target.closest(".machine-btn");
  if (!btn) return;
  localStorage.setItem(MACHINE_KEY, btn.dataset.machine);
  renderWorkshopChips();
});

tabsEl.addEventListener("click", (event) => {
  const btn = event.target.closest(".tab");
  if (!btn) return;
  localStorage.setItem(TAB_KEY, btn.dataset.tab);
  renderWorkshopChips();
});

suggestWorkshopEl.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-q]");
  if (btn) sendMessage(btn.dataset.q, "chip");
});

suggestEl.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-q]");
  if (btn) sendMessage(btn.dataset.q, "chip");
});

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(inputEl.value);
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(inputEl.value);
  }
});

applyMode();
welcome();
