// Страница «О карте»: как считается, откуда данные, свежие отметки и ручной ввод из чата.
const LEVEL_WORD = {
  sure: "уверенно",
  likely: "скорее всего",
  stale: "давно не подтверждали",
};

const bannerEl = document.querySelector("#banner");
const feedEl = document.querySelector("#feed");
const stepsEl = document.querySelector("#steps");
const sourcesEl = document.querySelector("#sources");

let clockShift = 0;

// Время сервера, чтобы возраст не зависел от часов на телефоне.
function nowMs() {
  return Date.now() + clockShift;
}

function ago(ms) {
  const min = Math.max(0, Math.round((nowMs() - ms) / 60000));
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const hours = Math.floor(min / 60);
  const rest = min % 60;
  if (hours >= 24) return "больше суток назад";
  return rest ? `${hours} ч ${rest} мин назад` : `${hours} ч назад`;
}

function renderSources(data) {
  bannerEl.textContent = data.banner;
  stepsEl.replaceChildren();
  data.steps.forEach((step) => {
    const li = document.createElement("li");
    li.textContent = step;
    stepsEl.append(li);
  });
  sourcesEl.replaceChildren();
  data.sources.forEach((source) => {
    const block = document.createElement("article");
    block.className = "source";
    const title = document.createElement("h3");
    title.textContent = source.title;
    const kind = document.createElement("p");
    kind.className = "kind";
    kind.textContent = source.kind;
    const where = document.createElement("p");
    const url = source.where.startsWith("http") ? source.where.split(" ")[0] : "";
    if (url) {
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = url;
      where.append(link);
    } else {
      where.textContent = source.where;
    }
    const take = document.createElement("p");
    take.textContent = source.take;
    const action = document.createElement("p");
    action.textContent = source.action;
    block.append(title, kind, where, take, action);
    sourcesEl.append(block);
  });
}

function renderFeed(rows) {
  feedEl.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.textContent = "Свежих отметок пока нет.";
    feedEl.append(empty);
    return;
  }
  rows.slice(0, 20).forEach((item) => {
    const li = document.createElement("li");
    const dot = document.createElement("i");
    dot.className = `dot ${item.status} ${item.level}`;
    const source = item.source ? ` · ${item.source}` : "";
    const part = item.label ? ` · ${item.label}` : item.radius ? " · кусок улицы" : "";
    li.append(
      dot,
      document.createTextNode(
        `${item.name}${part} · ${item.status === "on" ? "есть" : "нет"} · ${ago(item.at)} · ${LEVEL_WORD[item.level]}${source}`
      )
    );
    feedEl.append(li);
  });
}

async function pullLive() {
  const response = await fetch("api/live");
  if (!response.ok) return;
  const data = await response.json();
  clockShift = Number(data.now) ? data.now - Date.now() : 0;
  const rows = [...(data.reports || []), ...(data.places || [])].sort((a, b) => b.at - a.at);
  renderFeed(rows);
}

async function sendComment() {
  const text = document.querySelector("#comment").value.trim();
  const source = document.querySelector("#source").value.trim() || "чат";
  const note = document.querySelector("#comment-note");
  if (!text) {
    note.textContent = "Вставь текст из группы.";
    return;
  }
  const response = await fetch("api/comments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source }),
  });
  const result = await response.json();
  if (!result.ok) {
    note.textContent = response.status === 429 || response.status === 413
      ? result.reason
      : "Нужны название улицы и слова «свет есть» или «света нет». Район целиком не красится.";
    return;
  }
  const names = result.streets.map((item) => item.name).join(", ");
  const word = result.status === "on" ? "свет есть" : "света нет";
  const fresh = result.streets.some((item) => item.new);
  note.textContent = fresh
    ? `${names}: ${word}. Видно всем; цвет блёкнет, если никто не подтверждает.`
    : `${names}: ${word}. Такая отметка уже учтена.`;
  document.querySelector("#comment").value = "";
  await pullLive();
}

document.querySelector("#send-comment").addEventListener("click", () => {
  sendComment().catch(() => {
    document.querySelector("#comment-note").textContent = "Сервер не ответил.";
  });
});

fetch("data/sources.json")
  .then((response) => response.json())
  .then(renderSources)
  .catch(() => {
    bannerEl.textContent = "Данные не загрузились. Обновите страницу.";
  });
pullLive().catch(() => {});
