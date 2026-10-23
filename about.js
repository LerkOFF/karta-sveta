// Страница «О карте»: как считается, откуда данные, свежие отметки и подключение датчика.
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
  renderSensorCount(data.sensors);
}

// Датчики: подключение, ссылка пульса и отключение.
const TOKEN_KEY = "karta-sensor-token";

function renderSensorCount(counts) {
  const el = document.querySelector("#sensor-count");
  const total = counts ? counts.on + counts.off : 0;
  el.textContent = total ? `Сейчас датчиков: ${total}, свет есть у ${counts.on}, нет у ${counts.off}.` : "";
}

function remember(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (error) {
    /* без хранилища токен остаётся только на экране */
  }
}

function remembered() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

function block(tag, text) {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
}

function showSensor(result) {
  const out = document.querySelector("#sensor-result");
  const where = `${result.name}, дом ${result.house}${result.exact ? "" : " (ближайший найденный в базе)"}`;
  const parts = [block("p", `Подключено: ${where}. Красит кусок улицы около ${result.radius} м.`)];
  const url = new URL(`api/pulse/${result.token}`, location.href).href;
  parts.push(
    block("p", "Пусть на роутере, Raspberry Pi или любом компьютере, который всегда в розетке, раз в минуту выполняется эта команда (cron):"),
    block("pre", `* * * * * curl -fsS -m 10 ${url} >/dev/null 2>&1`),
    block("p", "Проверить вручную: откройте ссылку или выполните curl, придёт {\"ok\": true}. Датчик заработает после первого пульса, «нет» поставит после 4 минут тишины."),
    block("pre", url)
  );
  parts.push(
    block("p", "Токен нужен, чтобы отключить датчик. Сохраните его, потом показать не сможем:"),
    block("pre", result.token)
  );
  out.replaceChildren(...parts);
}

async function addSensor() {
  const out = document.querySelector("#sensor-result");
  const address = document.querySelector("#sensor-address").value.trim();
  if (!address) {
    out.textContent = "Напиши улицу и номер дома, например «Артёма 10».";
    return;
  }
  const button = document.querySelector("#sensor-add");
  button.disabled = true;
  out.textContent = "Подключаю…";
  try {
    const response = await fetch("api/sensor", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    const result = await response.json();
    if (!result.ok) {
      out.textContent = result.reason || "Не получилось подключить.";
      return;
    }
    remember(result.token);
    document.querySelector("#sensor-token").value = result.token;
    showSensor(result);
    await pullLive();
  } finally {
    button.disabled = false;
  }
}

async function removeSensor() {
  const note = document.querySelector("#sensor-remove-note");
  const token = document.querySelector("#sensor-token").value.trim();
  if (!token) {
    note.textContent = "Вставь токен датчика.";
    return;
  }
  const response = await fetch("api/sensor/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  const result = await response.json();
  note.textContent = result.ok ? "Датчик отключён, IP удалён." : result.reason || "Не получилось.";
  if (result.ok) {
    document.querySelector("#sensor-token").value = "";
    remember("");
    await pullLive();
  }
}

document.querySelector("#sensor-token").value = remembered();
document.querySelector("#sensor-add").addEventListener("click", () => {
  addSensor().catch(() => {
    document.querySelector("#sensor-result").textContent = "Сервер не ответил.";
  });
});
document.querySelector("#sensor-remove").addEventListener("click", () => {
  removeSensor().catch(() => {
    document.querySelector("#sensor-remove-note").textContent = "Сервер не ответил.";
  });
});

fetch("data/sources.json")
  .then((response) => response.json())
  .then(renderSources)
  .catch(() => {
    bannerEl.textContent = "Данные не загрузились. Обновите страницу.";
  });
pullLive().catch(() => {});
