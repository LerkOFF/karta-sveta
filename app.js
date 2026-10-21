const TTL_MS = 6 * 60 * 60 * 1000;
const STORAGE_KEY = "karta-sveta-reports-v2";

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
let reports = loadReports();
let liveReports = [];
let featuresBySegment = new Map();
let featuresByKey = new Map();
let autoByKey = new Map();
let autoNote = "";

function loadReports() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveReports() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(reports));
}

function freshReport(segment) {
  const found = reports
    .filter((item) => item.segment === segment && Date.now() - item.at < TTL_MS)
    .sort((a, b) => b.at - a.at);
  return found[0] || null;
}

function liveFor(key) {
  return liveReports.find((item) => item.key === key && Date.now() - item.at < TTL_MS) || null;
}

function powerOf(segment, key) {
  const live = liveFor(key);
  if (live) return live.status;
  const report = freshReport(segment);
  if (report) return report.status;
  return autoByKey.get(key) || "";
}

function applyState(segment, key) {
  map.setFeatureState(
    { source: "streets", id: segment },
    { power: powerOf(segment, key), selected: segment === selected }
  );
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
      markNote.textContent = "Кнопка и комментарий красят улицу целиком на 6 часов. График при этом прячется.";
      return;
    }
    pickedEl.textContent = "Нажми улицу на карте.";
    markNote.textContent = "";
    return;
  }
  const feature = featuresBySegment.get(selected);
  const props = feature.properties;
  pickedEl.textContent = labelFor(props);
  const live = liveFor(props.key);
  if (live) {
    const when = new Date(live.at);
    const word = live.status === "on" ? "Свет есть" : "Света нет";
    markNote.textContent = `${word}, из «${live.source}», ${when.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}. Вся улица, на 6 часов.`;
    return;
  }
  const report = freshReport(selected);
  if (report) {
    const when = new Date(report.at);
    const word = report.status === "on" ? "Свет есть" : "Света нет";
    markNote.textContent = `${word}, отметка ${when.toLocaleString("ru-RU")}. Через 6 часов снова виден график.`;
    return;
  }
  const auto = autoByKey.get(props.key);
  if (auto === "on") markNote.textContent = "По графику сейчас свет есть. Своя отметка важнее.";
  else if (auto === "off") markNote.textContent = "По графику сейчас света нет. Своя отметка важнее.";
  else markNote.textContent = "На этот участок графика нет.";
}

function renderFeed() {
  const rows = liveReports
    .filter((item) => Date.now() - item.at < TTL_MS)
    .slice(0, 12);
  feedEl.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.textContent = "Из чатов пока пусто. Цвет Киевского района идёт от графика.";
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
    dot.className = `dot ${item.status}`;
    const source = item.source ? ` · ${item.source}` : "";
    button.append(
      dot,
      document.createTextNode(
        `${item.name} · ${item.status === "on" ? "есть" : "нет"}${source} · ${new Date(item.at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`
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
  const box = boundsOf([feature]);
  reports.push({
    segment: selected,
    key: props.key,
    name: props.name,
    status,
    at: Date.now(),
    lat: (box[0][1] + box[1][1]) / 2,
    lon: (box[0][0] + box[1][0]) / 2,
  });
  saveReports();
  paint();
  fetch("/api/mark", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      key: props.key,
      name: props.name,
      status,
      source: "кнопка на карте",
      text: props.name,
    }),
  }).then(() => pullLive()).catch(() => {});
}

async function pullLive() {
  const response = await fetch("/api/live");
  if (!response.ok) return;
  const data = await response.json();
  liveReports = Array.isArray(data.reports) ? data.reports : [];
  autoEl.textContent = `${autoNote} Из чатов закрашено улиц: ${liveReports.length}. Страница проверяет новые записи каждые 20 секунд.`;
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
  const response = await fetch("/api/comments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source }),
  });
  const result = await response.json();
  if (!result.ok) {
    note.textContent = "Нужны название улицы и слова «свет есть» или «света нет». Район целиком не красится.";
    return;
  }
  const names = result.streets.map((item) => item.name).join(", ");
  const word = result.status === "on" ? "свет есть" : "света нет";
  note.textContent = `${names}: ${word}. Запись видна на карте 6 часов.`;
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

document.querySelector("#download").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify(reports, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "otmetki-sveta.json";
  link.click();
  URL.revokeObjectURL(link.href);
});

document.querySelector("#upload").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  try {
    const parsed = JSON.parse(await file.text());
    if (!Array.isArray(parsed)) throw new Error("format");
    reports = parsed.filter((item) => item && item.segment && (item.status === "on" || item.status === "off") && item.at);
    saveReports();
    paint();
    markNote.textContent = `Загружено отметок: ${reports.length}.`;
  } catch {
    markNote.textContent = "Файл не разобрался. Нужен JSON, который скачала эта страница.";
  }
  event.target.value = "";
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
