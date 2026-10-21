const LEVEL_WORD = {
  sure: "уверенно",
  likely: "скорее всего",
  stale: "давно не подтверждали",
};

const queryInput = document.querySelector("#query");
const resultsEl = document.querySelector("#results");
const pickedEl = document.querySelector("#picked");
const onBtn = document.querySelector("#on");
const offBtn = document.querySelector("#off");
const hereBtn = document.querySelector("#here");
const markNote = document.querySelector("#mark-note");
const feedEl = document.querySelector("#feed");
const bannerEl = document.querySelector("#banner");
const autoEl = document.querySelector("#auto");
const stepsEl = document.querySelector("#steps");
const sourcesEl = document.querySelector("#sources");

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/liberty",
  center: [37.8025, 48.0159],
  zoom: 12,
  attributionControl: true,
});
map.addControl(new maplibregl.NavigationControl(), "top-left");

let index = [];
let selected = null;
let focusKey = null;
let liveReports = [];
let liveByKey = new Map();
let clockShift = 0;
let featuresBySegment = new Map();
let featuresByKey = new Map();
let autoByKey = new Map();
let autoNote = "";

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

function sourcesWord(count) {
  const last = count % 10;
  const tens = count % 100;
  if (last === 1 && tens !== 11) return `${count} источник`;
  if (last >= 2 && last <= 4 && (tens < 10 || tens >= 20)) return `${count} источника`;
  return `${count} источников`;
}

// Свежая отметка важнее графика. Если отметка выцвела, цвет даёт график,
// а если и графика нет, остаётся блёклый цвет последней отметки.
function stateOf(key) {
  const live = liveByKey.get(key) || null;
  if (live && live.level !== "stale") return { power: live.status, level: live.level, live };
  const plan = autoByKey.get(key);
  if (plan) return { power: plan, level: "schedule", live };
  if (live) return { power: live.status, level: "stale", live };
  return { power: "", level: "", live: null };
}

function applyState(segment, key) {
  const state = stateOf(key);
  map.setFeatureState(
    { source: "streets", id: segment },
    { power: state.power, level: state.level, selected: segment === selected }
  );
}

function describe(state) {
  const word = (status) => (status === "on" ? "Свет есть" : "Света нет");
  const live = state.live;
  if (state.level === "sure" || state.level === "likely") {
    const dispute = live.conflict ? " Ответы расходятся." : "";
    return `${word(live.status)}, ${ago(live.at)}: ${LEVEL_WORD[state.level]}, ${sourcesWord(live.sources)}.${dispute} Последняя отметка: «${live.source}».`;
  }
  if (state.level === "schedule") {
    const old = live ? ` Последняя отметка (${word(live.status).toLowerCase()}, ${ago(live.at)}) устарела.` : "";
    return `По графику сейчас ${state.power === "on" ? "свет есть" : "света нет"}. Свежих отметок нет.${old}`;
  }
  if (state.level === "stale") {
    return `${word(live.status)} ${ago(live.at)}, с тех пор никто не подтверждал. Цвет блёклый: верить с оглядкой.`;
  }
  return "Данных нет: ни графика, ни свежих отметок. Если знаешь, нажми кнопку.";
}

function paint() {
  featuresBySegment.forEach((feature, segment) => {
    applyState(segment, feature.properties.key);
  });
  renderFeed();
  renderSelection();
}

function labelFor(props) {
  if (props.parts > 1) return `${props.name}, участок ${props.part} из ${props.parts}`;
  return props.name;
}

function boundsOf(features) {
  const box = [180, 90, -180, -90];
  const walk = (coords) => {
    if (typeof coords[0] === "number") {
      box[0] = Math.min(box[0], coords[0]);
      box[1] = Math.min(box[1], coords[1]);
      box[2] = Math.max(box[2], coords[0]);
      box[3] = Math.max(box[3], coords[1]);
      return;
    }
    coords.forEach(walk);
  };
  features.forEach((feature) => walk(feature.geometry.coordinates));
  return [[box[0], box[1]], [box[2], box[3]]];
}

function selectSegment(segment, fit) {
  const feature = featuresBySegment.get(segment);
  if (!feature) return;
  selected = segment;
  focusKey = null;
  onBtn.disabled = false;
  offBtn.disabled = false;
  if (fit) map.fitBounds(boundsOf([feature]), { maxZoom: 17, padding: 48 });
  paint();
}

function focusStreet(item) {
  const group = featuresByKey.get(item.key) || [];
  if (!group.length) return;
  selected = null;
  focusKey = item.key;
  onBtn.disabled = true;
  offBtn.disabled = true;
  map.fitBounds(boundsOf(group), { maxZoom: 16, padding: 48 });
  paint();
}

function renderSelection() {
  if (!selected) {
    if (focusKey) {
      const item = index.find((row) => row.key === focusKey);
      const parts = item ? item.parts : 0;
      pickedEl.textContent = parts > 1
        ? `${item.name}: ${parts} участков. Нажми нужный на карте.`
        : item.name;
      markNote.textContent = `${describe(stateOf(focusKey))} Отметка относится ко всей улице.`;
      return;
    }
    pickedEl.textContent = "Нажми улицу на карте.";
    markNote.textContent = "";
    return;
  }
  const feature = featuresBySegment.get(selected);
  const props = feature.properties;
  pickedEl.textContent = labelFor(props);
  markNote.textContent = `${describe(stateOf(props.key))} Твоя отметка относится ко всей улице.`;
}

function renderFeed() {
  const rows = liveReports.slice(0, 12);
  feedEl.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.textContent = "Свежих отметок пока нет. Киевский район идёт по графику.";
    feedEl.append(empty);
    return;
  }
  rows.forEach((item) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.style.border = "0";
    button.style.background = "transparent";
    button.style.width = "100%";
    button.style.textAlign = "left";
    button.style.cursor = "pointer";
    button.style.font = "15px/1.3 ui-sans-serif, system-ui, sans-serif";
    const dot = document.createElement("i");
    dot.className = `dot ${item.status} ${item.level}`;
    const source = item.source ? ` · ${item.source}` : "";
    button.append(
      dot,
      document.createTextNode(
        `${item.name} · ${item.status === "on" ? "есть" : "нет"} · ${ago(item.at)} · ${LEVEL_WORD[item.level]}${source}`
      )
    );
    button.addEventListener("click", () => {
      const known = index.find((row) => row.key === item.key);
      if (known) focusStreet(known);
    });
    li.append(button);
    feedEl.append(li);
  });
}

function mark(status) {
  if (!selected) return;
  const feature = featuresBySegment.get(selected);
  const props = feature.properties;
  fetch("api/mark", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: props.key, status, segment: props.segment }),
  })
    .then(async (response) => {
      const result = await response.json();
      if (!result.ok) {
        markNote.textContent = result.reason || "Отметка не принята.";
        return;
      }
      await pullLive();
    })
    .catch(() => {
      markNote.textContent = "Сервер не ответил, отметка не сохранилась.";
    });
}

async function pullLive() {
  const response = await fetch("api/live");
  if (!response.ok) return;
  const data = await response.json();
  clockShift = Number(data.now) ? data.now - Date.now() : 0;
  liveReports = Array.isArray(data.reports) ? data.reports : [];
  liveByKey = new Map(liveReports.map((item) => [item.key, item]));
  const fresh = liveReports.filter((item) => item.level !== "stale").length;
  autoEl.textContent = `${autoNote} Отметок за сутки по ${liveReports.length} улицам, из них свежих ${fresh}. Страница обновляется каждые 20 секунд.`;
  if (featuresBySegment.size) paint();
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

function norm(value) {
  return value
    .toLowerCase()
    .replaceAll("ё", "е")
    .replaceAll("ї", "и")
    .replaceAll("і", "и")
    .replaceAll("є", "е")
    .replaceAll("ґ", "г")
    .replaceAll("вулиця", " ")
    .replaceAll("улица", " ")
    .replaceAll("проспект", " ")
    .replaceAll("переулок", " ")
    .replaceAll("провулок", " ")
    .replaceAll("’", "")
    .replaceAll("'", "")
    .replaceAll("-", "")
    .replace(/\s+/g, " ")
    .trim();
}

function search(value) {
  const needle = norm(value);
  resultsEl.replaceChildren();
  if (needle.length < 2) {
    resultsEl.hidden = true;
    return;
  }
  const found = index
    .filter((item) => item.key.includes(needle) || norm(item.name).includes(needle))
    .slice(0, 8);
  resultsEl.hidden = false;
  if (!found.length) {
    const li = document.createElement("li");
    li.textContent = "Такой улицы в выгрузке нет.";
    li.style.padding = "10px 12px";
    resultsEl.append(li);
    return;
  }
  found.forEach((item) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const extra = item.parts > 1 ? `, ${item.parts} участков` : "";
    button.textContent = `${item.name}${extra}`;
    button.addEventListener("click", () => {
      queryInput.value = item.name;
      resultsEl.hidden = true;
      focusStreet(item);
    });
    li.append(button);
    resultsEl.append(li);
  });
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

function useRussianLabels() {
  (map.getStyle().layers || []).forEach((layer) => {
    if (layer.type !== "symbol") return;
    if (!/name|place|poi|water|park/i.test(layer.id)) return;
    if (/shield|ref|exit/i.test(layer.id)) return;
    if (!layer.layout || !layer.layout["text-field"]) return;
    map.setLayoutProperty(layer.id, "text-field", ["coalesce", ["get", "name:ru"], ["get", "name"]]);
  });
}

function scheduleNow(queues) {
  const now = new Date();
  const anchor = new Date(`${queues.anchor}T00:00:00`);
  const day = Math.floor((now - anchor) / 86400000);
  const shift = ((Math.floor(day / 2) % 4) + 4) % 4;
  const minutes = now.getHours() * 60 + now.getMinutes();
  let slot = -1;
  queues.slots.forEach(([start, end], index) => {
    if (minutes >= start * 60 && minutes < end * 60) slot = index;
  });
  const blockState = queues.blocks.map(() => "off");
  if (slot >= 0) blockState[(slot - shift + 4) % 4] = "on";
  const seen = new Map();
  queues.blocks.forEach((keys, index) => {
    keys.forEach((key) => {
      if (!seen.has(key)) seen.set(key, new Set());
      seen.get(key).add(blockState[index]);
    });
  });
  const result = new Map();
  seen.forEach((set, key) => {
    if (set.size === 1) result.set(key, [...set][0]);
  });
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const date = String(now.getDate()).padStart(2, "0");
  const today = `${now.getFullYear()}-${month}-${date}`;
  const onCount = [...result.values()].filter((value) => value === "on").length;
  const offCount = [...result.values()].filter((value) => value === "off").length;
  const clock = now.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const extended = today > queues.publishedUntil
    ? " Карточки публиковали до 16 октября, дальше круг тот же."
    : "";
  return {
    result,
    note: `Сейчас ${clock}. Само закрасилось ${onCount} улиц со светом и ${offCount} без света, Киевский район.${extended}`,
  };
}

function pickStreet(point) {
  const hits = map.queryRenderedFeatures(
    [
      [point.x - 18, point.y - 18],
      [point.x + 18, point.y + 18],
    ],
    { layers: ["streets-line"] }
  );
  if (!hits.length) {
    markNote.textContent = "Рядом с нажатием нет улицы из выгрузки. Нажми серую черту.";
    return;
  }
  selectSegment(hits[0].properties.segment, false);
}

queryInput.addEventListener("input", () => search(queryInput.value));
onBtn.addEventListener("click", () => mark("on"));
offBtn.addEventListener("click", () => mark("off"));
document.querySelector("#send-comment").addEventListener("click", () => {
  sendComment().catch(() => {
    document.querySelector("#comment-note").textContent = "Сервер комментариев не ответил. Страницу надо открывать через python3 server.py.";
  });
});
hereBtn.addEventListener("click", () => {
  if (!navigator.geolocation) {
    markNote.textContent = "Браузер не отдал место.";
    return;
  }
  hereBtn.disabled = true;
  navigator.geolocation.getCurrentPosition(
    (position) => {
      hereBtn.disabled = false;
      map.flyTo({ center: [position.coords.longitude, position.coords.latitude], zoom: 16 });
    },
    () => {
      hereBtn.disabled = false;
      markNote.textContent = "Место не пришло. Нажми улицу на карте.";
    },
    { enableHighAccuracy: true, timeout: 8000 }
  );
});

function addStreets(streets) {
  streets.features.forEach((feature) => {
    featuresBySegment.set(feature.properties.segment, feature);
    const group = featuresByKey.get(feature.properties.key) || [];
    group.push(feature);
    featuresByKey.set(feature.properties.key, group);
  });
  const before = (map.getStyle().layers || []).find((layer) => layer.type === "symbol");
  map.addSource("streets", {
    type: "geojson",
    data: streets,
    promoteId: "segment",
  });
  map.addLayer(
    {
      id: "streets-line",
      type: "line",
      source: "streets",
      paint: {
        "line-color": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          "#1d4f91",
          ["==", ["feature-state", "power"], "on"],
          "#1b7f4e",
          ["==", ["feature-state", "power"], "off"],
          "#b42318",
          "#8d867c",
        ],
        "line-width": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          6,
          ["any", ["==", ["feature-state", "power"], "on"], ["==", ["feature-state", "power"], "off"]],
          4.5,
          2.4,
        ],
        "line-opacity": [
          "case",
          ["boolean", ["feature-state", "selected"], false],
          1,
          ["==", ["feature-state", "level"], "stale"],
          0.4,
          ["==", ["feature-state", "level"], "schedule"],
          0.6,
          ["==", ["feature-state", "level"], "likely"],
          0.75,
          1,
        ],
      },
    },
    before && before.id
  );
  map.on("click", "streets-line", (event) => {
    selectSegment(event.features[0].properties.segment, false);
  });
  map.on("click", (event) => {
    const direct = map.queryRenderedFeatures(event.point, { layers: ["streets-line"] });
    if (direct.length) return;
    pickStreet(event.point);
  });
  map.on("mouseenter", "streets-line", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "streets-line", () => {
    map.getCanvas().style.cursor = "";
  });
  paint();
}

async function boot() {
  const [sourceResponse, indexResponse, streetResponse, queueResponse] = await Promise.all([
    fetch("data/sources.json"),
    fetch("data/streets-index.json"),
    fetch("data/streets.geojson"),
    fetch("data/kyiv-queues.json"),
  ]);
  renderSources(await sourceResponse.json());
  index = await indexResponse.json();
  const streets = await streetResponse.json();
  const queues = await queueResponse.json();
  const schedule = scheduleNow(queues);
  autoByKey = schedule.result;
  autoNote = schedule.note;
  autoEl.textContent = autoNote;
  const start = () => {
    useRussianLabels();
    addStreets(streets);
    pullLive().catch(() => {});
    setInterval(() => {
      pullLive().catch(() => {});
    }, 20000);
  };
  if (map.isStyleLoaded()) start();
  else map.once("load", start);
}

boot().catch(() => {
  bannerEl.textContent = "Данные не загрузились. Открой страницу через локальный сервер, не как файл с диска.";
});
