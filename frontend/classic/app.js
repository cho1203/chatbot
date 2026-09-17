const chatEl = document.getElementById("chat");
const formEl = document.getElementById("form");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const suggestEl = document.getElementById("suggest");

const SESSION_KEY = "fanuc_chat_session";

function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function addBubble(role, text, sources) {
  const wrap = document.createElement("article");
  wrap.className = `bubble ${role}`;
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = role === "user" ? "질문" : "매뉴얼 답변";
  wrap.appendChild(meta);
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

async function sendMessage(text) {
  const message = text.trim();
  if (!message) return;

  addBubble("user", message);
  inputEl.value = "";
  sendEl.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: sessionId(),
        mode: "classic",
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "서버 오류");
    }
    addBubble("bot", data.answer, data.sources);
  } catch (err) {
    addBubble("bot", "서버에 연결하지 못했습니다. 8787 포트에서 백엔드가 실행 중인지 확인하세요.\n" + err.message);
  } finally {
    sendEl.disabled = false;
    inputEl.focus();
  }
}

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

suggestEl.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-q]");
  if (btn) sendMessage(btn.dataset.q);
});

addBubble(
  "bot",
  "FANUC 매뉴얼 챗봇입니다.\n알람 번호(예: 410), G/M 코드, 공구 보정, 좌표계, 비상정지 복구를 질문해 주세요."
);
