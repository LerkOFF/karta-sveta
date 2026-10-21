// Карта света для жителей: смотреть и отмечать. Источники и «как считается» на about.html.
const LEVEL_WORD = {
  sure: "уверенно",
  likely: "скорее всего",
  stale: "давно не подтверждали",
};

const BULLETIN_SHOW_MS = 12 * 60 * 60 * 1000;

// Длина отмечаемого куска улицы вокруг нажатия: радиус в метрах, 0 = вся улица.
const DEFAULT_SCOPE = 100;

const queryInput = document.querySelector("#query");
const resultsEl = document.querySelector("#results");
const pickedEl = document.querySelector("#picked");
const onBtn = document.querySelector("#on");
const offBtn = document.querySelector("#off");
const hereBtn = document.querySelector("#here");
const markNote = document.querySelector("#mark-note");
const bulletinEl = document.querySelector("#bulletin");
const chipsEl = document.querySelector("#scopes");
const fromInput = document.querySelector("#h-from");
const toInput = document.querySelector("#h-to");
const housesBtn = document.querySelector("#h-go");
const groupsEl = document.querySelector("#groups");
const phonesEl = document.querySelector("#phones");

const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/liberty",
  center: [37.8025, 48.0159],
  zoom: 12,
  minZoom: 10,
  maxBounds: [[37.45, 47.83], [38.07, 48.16]],
  attributionControl: true,
});
map.addControl(new maplibregl.NavigationControl(), "top-left");

let index = [];
let pick = null;
let scope = DEFAULT_SCOPE;
let focusKey = null;
let flash = "";
// Выбор по номерам домов: {label, count}. Пока он есть, scope это радиус, посчитанный по домам.
let houses = null;
const houseCache = new Map();
let liveReports = [];
let liveByKey = new Map();
let livePlaces = [];
let bulletin = null;
let clockShift = 0;
let featuresByKey = new Map();
let autoByKey = new Map();

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

function clock(ms) {
  return new Date(ms).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

// Донецкэнерго написал «все ТП запитаны» позже этой отметки «света нет».
function restoredNote(live, verdict = true) {
  if (!live || !live.superseded_at) return "";
  const tail = verdict ? ": отметка, скорее всего, устарела" : "";
  return ` В ${clock(live.superseded_at)} Донецкэнерго сообщил, что все ТП запитаны${tail}.`;
}

function describe(state) {
  const word = (status) => (status === "on" ? "Свет есть" : "Света нет");
  const live = state.live;
  if (state.level === "sure" || state.level === "likely") {
    const dispute = live.conflict ? " Ответы расходятся." : "";
    return `${word(live.status)}, ${ago(live.at)}: ${LEVEL_WORD[state.level]}, ${sourcesWord(live.sources)}.${dispute}${restoredNote(live)} Последняя отметка: «${live.source}».`;
  }
  if (state.level === "schedule") {
    const old = live ? ` Последняя отметка (${word(live.status).toLowerCase()}, ${ago(live.at)}) устарела.${restoredNote(live, false)}` : "";
    return `По графику сейчас ${state.power === "on" ? "свет есть" : "света нет"}. Свежих отметок нет.${old}`;
  }
  if (state.level === "stale") {
    return `${word(live.status)} ${ago(live.at)}, с тех пор никто не подтверждал.${restoredNote(live)} Цвет блёклый: верить с оглядкой.`;
  }
  return "Данных нет: ни графика, ни свежих отметок. Если знаешь, нажми кнопку.";
}

// Общегородская сводка: полоска сверху. Улицы она не красит.
function renderBulletin() {
  const found = bulletin;
  if (!found || nowMs() - found.at > BULLETIN_SHOW_MS) {
    bulletinEl.hidden = true;
    return;
  }
  const when = `${clock(found.at)} (${ago(found.at)})`;
  const sources = found.sources > 1 ? ` ${sourcesWord(found.sources)}.` : "";
  if (found.kind === "all_on") {
    bulletinEl.className = "bulletin on";
    bulletinEl.textContent = `${when}. Донецкэнерго: все трансформаторные подстанции Донецка запитаны. Отметки «света нет» старше этого времени, скорее всего, устарели.${sources}`;
  } else {
    const where = found.districts.length
      ? `Частично обесточены районы: ${found.districts.join(", ")}.`
      : "В некоторых районах Донецка пропал свет.";
    bulletinEl.className = "bulletin off";
    bulletinEl.textContent = `${when}. ${where}${sources}`;
  }
  bulletinEl.hidden = false;
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

function metersPerDegree(lat) {
  return [111320 * Math.cos((lat * Math.PI) / 180), 110540];
}

function distance(a, b) {
  const [kx, ky] = metersPerDegree((a[1] + b[1]) / 2);
  return Math.hypot((a[0] - b[0]) * kx, (a[1] - b[1]) * ky);
}

function linesOf(feature) {
  const geometry = feature.geometry;
  return geometry.type === "MultiLineString" ? geometry.coordinates : [geometry.coordinates];
}

// Часть линии, которая лежит не дальше radius метров от center.
function clipLine(coords, center, radius) {
  const [kx, ky] = metersPerDegree(center[1]);
  const pts = coords.map(([x, y]) => [(x - center[0]) * kx, (y - center[1]) * ky]);
  const pieces = [];
  let current = null;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const [ax, ay] = pts[i];
    const dx = pts[i + 1][0] - ax;
    const dy = pts[i + 1][1] - ay;
    const a = dx * dx + dy * dy;
    const b = 2 * (ax * dx + ay * dy);
    const c = ax * ax + ay * ay - radius * radius;
    const disc = b * b - 4 * a * c;
    if (a === 0 || disc < 0) {
      current = null;
      continue;
    }
    const root = Math.sqrt(disc);
    const lo = Math.max(0, (-b - root) / (2 * a));
    const hi = Math.min(1, (-b + root) / (2 * a));
    if (lo > hi) {
      current = null;
      continue;
    }
    const at = (t) => [
      coords[i][0] + (coords[i + 1][0] - coords[i][0]) * t,
      coords[i][1] + (coords[i + 1][1] - coords[i][1]) * t,
    ];
    if (!current || lo > 0) {
      current = [at(lo)];
      pieces.push(current);
    }
    current.push(at(hi));
    if (hi < 1) current = null;
  }
  return pieces;
}

function piecesOf(key, center, radius) {
  const pieces = [];
  (featuresByKey.get(key) || []).forEach((feature) => {
    linesOf(feature).forEach((line) => {
      if (radius) pieces.push(...clipLine(line, center, radius));
      else pieces.push(line);
    });
  });
  return pieces;
}

// Ближайшая к нажатию точка на линиях улицы.
function nearestOnStreet(key, lngLat) {
  const [kx, ky] = metersPerDegree(lngLat.lat);
  let best = null;
  (featuresByKey.get(key) || []).forEach((feature) => {
    linesOf(feature).forEach((line) => {
      for (let i = 0; i < line.length - 1; i += 1) {
        const ax = (line[i][0] - lngLat.lng) * kx;
        const ay = (line[i][1] - lngLat.lat) * ky;
        const dx = (line[i + 1][0] - lngLat.lng) * kx - ax;
        const dy = (line[i + 1][1] - lngLat.lat) * ky - ay;
        const len = dx * dx + dy * dy;
        const t = len ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len)) : 0;
        const px = ax + dx * t;
        const py = ay + dy * t;
        const d = Math.hypot(px, py);
        if (!best || d < best.d) best = { d, lon: lngLat.lng + px / kx, lat: lngLat.lat + py / ky };
      }
    });
  });
  return best;
}

function applyState(segment, key) {
  const state = stateOf(key);
  map.setFeatureState(
    { source: "streets", id: segment },
    { power: state.power, level: state.level, focused: key === focusKey }
  );
}

function paint() {
  featuresByKey.forEach((group, key) => {
    group.forEach((feature) => applyState(feature.properties.segment, key));
  });
  renderCard();
}

// Что известно про выбранное место: свежая отметка на этом куске, иначе общий цвет улицы.
function stateAt() {
  if (!pick) return null;
  if (scope) {
    const here = livePlaces
      .filter((p) => p.key === pick.key && distance([pick.lon, pick.lat], [p.lon, p.lat]) <= p.radius)
      .sort((a, b) => b.at - a.at)[0];
    if (here) return { power: here.status, level: here.level, live: here };
  }
  return stateOf(pick.key);
}

function scopeText() {
  if (houses) return `дома ${houses.label}`;
  if (!scope) return "вся улица";
  return scope >= 500 ? "отрезок около 1 км" : `отрезок около ${scope * 2} м`;
}

function renderCard() {
  chipsEl.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("on", !houses && Number(button.dataset.r) === scope);
  });
  onBtn.disabled = !pick;
  offBtn.disabled = !pick;
  if (pick) {
    const count = houses ? (houses.estimated ? " (примерно)" : ` (${housesWord(houses.count)})`) : "";
    pickedEl.textContent = `${pick.name}, ${scopeText()}${count}`;
    markNote.textContent = flash || describe(stateAt());
    return;
  }
  flash = "";
  const item = focusKey ? index.find((row) => row.key === focusKey) : null;
  pickedEl.textContent = item
    ? `${item.name}: нажмите на карте нужное место улицы.`
    : "Нажмите на карте на улицу, где хотите отметить свет.";
  markNote.textContent = "";
}

function renderPlaces() {
  const source = map.getSource("places");
  if (!source) return;
  const features = [];
  livePlaces.forEach((place) => {
    const lines = piecesOf(place.key, [place.lon, place.lat], place.radius);
    if (!lines.length) return;
    features.push({
      type: "Feature",
      properties: { status: place.status, level: place.level },
      geometry: { type: "MultiLineString", coordinates: lines },
    });
  });
  source.setData({ type: "FeatureCollection", features });
}

function renderPick() {
  const source = map.getSource("pick");
  if (!source) return;
  const features = [];
  if (pick) {
    const lines = piecesOf(pick.key, [pick.lon, pick.lat], scope);
    if (lines.length) {
      features.push({ type: "Feature", properties: {}, geometry: { type: "MultiLineString", coordinates: lines } });
    }
    if (houses) {
      houses.items.forEach(([number, lon, lat]) => {
        features.push({ type: "Feature", properties: { house: true, n: number }, geometry: { type: "Point", coordinates: [lon, lat] } });
      });
    } else {
      features.push({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [pick.lon, pick.lat] } });
    }
  }
  source.setData({ type: "FeatureCollection", features });
}

function housesWord(count) {
  const last = count % 10;
  const tens = count % 100;
  if (last === 1 && tens !== 11) return `${count} дом`;
  if (last >= 2 && last <= 4 && (tens < 10 || tens >= 20)) return `${count} дома`;
  return `${count} домов`;
}

function clearHouses() {
  houses = null;
  fromInput.value = "";
  toInput.value = "";
}

function setPick(next) {
  pick = next;
  focusKey = null;
  flash = "";
  clearHouses();
  renderPick();
  paint();
}

function focusStreet(item) {
  const group = featuresByKey.get(item.key) || [];
  if (!group.length) return;
  pick = null;
  clearHouses();
  focusKey = item.key;
  flash = "";
  map.fitBounds(boundsOf(group), { maxZoom: 16, padding: 48 });
  renderPick();
  paint();
}

function pickAt(point, lngLat) {
  const hits = map.queryRenderedFeatures(
    [
      [point.x - 18, point.y - 18],
      [point.x + 18, point.y + 18],
    ],
    { layers: ["streets-line"] }
  );
  if (!hits.length) {
    flash = "";
    markNote.textContent = "Рядом нет улицы. Нажмите ближе к линии улицы.";
    return;
  }
  let best = null;
  new Set(hits.map((hit) => hit.properties.key)).forEach((key) => {
    const found = nearestOnStreet(key, lngLat);
    if (found && (!best || found.d < best.d)) {
      const name = hits.find((hit) => hit.properties.key === key).properties.name;
      best = { d: found.d, key, name, lon: found.lon, lat: found.lat };
    }
  });
  if (best) setPick({ key: best.key, name: best.name, lon: best.lon, lat: best.lat });
}

function houseNumber(text) {
  const match = String(text).match(/^\s*(\d+)/);
  return match ? Number(match[1]) : null;
}

// Дома, лежащие цепочкой не дальше 350 м друг от друга, считаем одним участком улицы.
function clusterHouses(list, gap = 350) {
  const parent = list.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      if (distance([list[i][1], list[i][2]], [list[j][1], list[j][2]]) <= gap) parent[find(i)] = find(j);
    }
  }
  const groups = new Map();
  list.forEach((row, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(row);
  });
  return [...groups.values()];
}

// Дома нет в OSM: ставим примерное место по соседним номерам. Между ближайшими известными
// домами номера раскладываем по прямой, если с одной стороны дома нет, берём ближайший известный.
function guessHouses(list, low, high, label) {
  const numbered = list
    .map((row) => ({ row, n: houseNumber(row[0]) }))
    .filter((item) => item.n !== null);
  const below = numbered.filter((item) => item.n < low).sort((a, b) => b.n - a.n).slice(0, 6);
  const above = numbered.filter((item) => item.n > high).sort((a, b) => a.n - b.n).slice(0, 6);
  let best = null;
  below.forEach((a) => {
    above.forEach((b) => {
      const gap = distance([a.row[1], a.row[2]], [b.row[1], b.row[2]]);
      if (gap <= 1200 && (!best || gap < best.gap)) best = { a, b, gap };
    });
  });
  if (best) {
    const at = (n) => {
      const t = (n - best.a.n) / (best.b.n - best.a.n);
      return [best.a.row[1] + (best.b.row[1] - best.a.row[1]) * t, best.a.row[2] + (best.b.row[2] - best.a.row[2]) * t];
    };
    const items = [[`~${low}`, ...at(low)]];
    if (high !== low) items.push([`~${high}`, ...at(high)]);
    return {
      items,
      note: `Домов ${label} в OpenStreetMap нет. Отмечено примерное место между домами ${best.a.n} и ${best.b.n}.`,
    };
  }
  const near = [below[0], above[0]]
    .filter(Boolean)
    .sort((a, b) => Math.abs(a.n - low) - Math.abs(b.n - low))[0];
  if (!near) return null;
  return {
    items: [near.row],
    note: `Домов ${label} в OpenStreetMap нет, ближайший известный дом ${near.row[0]}. Отмечено место около него: если оно не то, нажмите на нужное место улицы.`,
  };
}

async function loadHouses(key) {
  if (!houseCache.has(key)) {
    const response = await fetch(`api/houses?key=${encodeURIComponent(key)}`);
    const data = await response.json();
    houseCache.set(key, data.houses || []);
  }
  return houseCache.get(key);
}

// Дома с номерами от и до: красим кусок улицы, который их накрывает.
async function applyHouses() {
  const key = pick ? pick.key : focusKey;
  const item = key ? index.find((row) => row.key === key) : null;
  if (!item) {
    flash = "";
    markNote.textContent = "Сначала найдите улицу по названию или нажмите на неё на карте.";
    return;
  }
  const from = houseNumber(fromInput.value);
  const to = houseNumber(toInput.value);
  if (from === null) {
    markNote.textContent = "Введите номер дома или два номера: с какого по какой.";
    return;
  }
  const low = Math.min(from, to === null ? from : to);
  const high = Math.max(from, to === null ? from : to);
  const label = low === high ? String(low) : `${low}–${high}`;
  const known = await loadHouses(key);
  let matching = known.filter((row) => {
    const number = houseNumber(row[0]);
    return number !== null && number >= low && number <= high;
  });
  let estimate = "";
  if (!matching.length) {
    const guess = guessHouses(known, low, high, label);
    if (!guess) {
      flash = "";
      markNote.textContent = `На улице ${item.name} домов ${label} в картах OpenStreetMap нет. Отметьте кусок улицы кнопками выше.`;
      return;
    }
    matching = guess.items;
    estimate = guess.note;
  }
  // Одно название может носить несколько далёких участков: берём тот, что ближе к нажатию, иначе самый большой.
  const groups = estimate ? [matching] : clusterHouses(matching);
  let chosen = groups[0];
  let notice = estimate;
  if (groups.length > 1) {
    const ref = pick ? [pick.lon, pick.lat] : null;
    const reachTo = (group) => Math.min(...group.map((row) => distance(ref, [row[1], row[2]])));
    groups.sort((x, y) => (ref ? reachTo(x) - reachTo(y) : y.length - x.length));
    chosen = groups[0];
    notice = ref
      ? "Такие номера есть в нескольких местах улицы, выбрано ближайшее к вашему нажатию."
      : "Такие номера есть в нескольких местах улицы, выбрано самое большое. Чтобы выбрать другое, нажмите на нужное место улицы и введите номера снова.";
  }
  const snapped = chosen
    .map(([, lon, lat]) => nearestOnStreet(key, { lng: lon, lat }))
    .filter(Boolean);
  const lons = snapped.map((point) => point.lon);
  const lats = snapped.map((point) => point.lat);
  const center = [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
  const reach = Math.max(...snapped.map((point) => distance(center, [point.lon, point.lat])));
  houses = { label, count: chosen.length, items: chosen, estimated: Boolean(estimate) };
  pick = { key, name: item.name, lon: center[0], lat: center[1] };
  scope = Math.min(2000, Math.max(50, Math.ceil(reach) + 25));
  focusKey = null;
  flash = notice;
  const all = chosen.map(([, lon, lat]) => [lon, lat]).concat(snapped.map((point) => [point.lon, point.lat]));
  const box = all.reduce(
    (acc, [x, y]) => [Math.min(acc[0], x), Math.min(acc[1], y), Math.max(acc[2], x), Math.max(acc[3], y)],
    [180, 90, -180, -90]
  );
  map.fitBounds([[box[0], box[1]], [box[2], box[3]]], {
    maxZoom: 18,
    padding: { top: window.innerWidth > 800 ? 260 : 60, bottom: 60, left: 60, right: 60 },
  });
  renderPick();
  paint();
}

function mark(status) {
  if (!pick) return;
  const body = { key: pick.key, status };
  if (scope) Object.assign(body, { lon: pick.lon, lat: pick.lat, radius: scope });
  if (scope && houses) body.label = `дома ${houses.label}`;
  const where = `${pick.name}, ${scopeText()}`;
  fetch("api/mark", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
    .then(async (response) => {
      const result = await response.json();
      if (!result.ok) {
        flash = result.reason || "Отметка не принята.";
      } else {
        const word = status === "on" ? "свет есть" : "света нет";
        const fresh = result.streets.some((item) => item.new);
        flash = fresh ? `Спасибо: ${where}, ${word}. Видно всем.` : `${where}, ${word}: такая отметка уже учтена.`;
      }
      renderCard();
      await pullLive();
    })
    .catch(() => {
      flash = "Сервер не ответил, отметка не сохранилась.";
      renderCard();
    });
}

async function pullLive() {
  const response = await fetch("api/live");
  if (!response.ok) return;
  const data = await response.json();
  clockShift = Number(data.now) ? data.now - Date.now() : 0;
  liveReports = Array.isArray(data.reports) ? data.reports : [];
  liveByKey = new Map(liveReports.map((item) => [item.key, item]));
  livePlaces = Array.isArray(data.places) ? data.places : [];
  bulletin = data.bulletin || null;
  renderBulletin();
  renderPlaces();
  if (featuresByKey.size) paint();
}

function telHref(phone) {
  return `tel:+${phone.replace(/\D/g, "")}`;
}

function phoneLinks(phones) {
  const wrap = document.createElement("span");
  wrap.className = "phones";
  phones.forEach((phone) => {
    const link = document.createElement("a");
    link.href = telHref(phone);
    link.textContent = phone;
    wrap.append(link);
  });
  return wrap;
}

function pill(title, url) {
  const link = document.createElement("a");
  link.className = "pill";
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = title;
  return link;
}

function renderContacts(data) {
  groupsEl.replaceChildren();
  data.districts.forEach((district) => {
    groupsEl.append(pill(district.name, `https://t.me/${district.channel}`));
  });
  data.city.links.forEach((item) => groupsEl.append(pill(item.title, item.url)));

  phonesEl.replaceChildren();
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = data.hint;
  const city = document.createElement("div");
  city.className = "call";
  const cityName = document.createElement("strong");
  cityName.textContent = data.city.title;
  city.append(cityName, phoneLinks(data.city.phones));
  phonesEl.append(hint, city);
  data.districts.forEach((district) => {
    const row = document.createElement("div");
    row.className = "call";
    const name = document.createElement("strong");
    name.textContent = `${district.name} район`;
    row.append(name, phoneLinks(district.phones));
    phonesEl.append(row);
  });
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = `Номера диспетчеров районов электросетей с сайта Донецкэнерго, сверено ${data.checked}.`;
  phonesEl.append(note);
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

// «Артёма 10-24», «Артёма дома 10–24», «Артёма 14»: улица и номера домов в одной строке.
function splitHouses(value) {
  const match = value.match(/^(.*?)[\s,]+(?:дома?\.?\s*)?(\d+)\s*[а-яa-z]?(?:\s*[-–—]\s*(\d+)\s*[а-яa-z]?)?\s*$/i);
  if (!match || norm(match[1]).length < 2) return { street: value, from: "", to: "" };
  return { street: match[1], from: match[2], to: match[3] || "" };
}

function search(value) {
  const parsed = splitHouses(value);
  const needle = norm(parsed.street);
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
    button.textContent = item.name;
    button.addEventListener("click", () => {
      queryInput.value = item.name;
      resultsEl.hidden = true;
      focusStreet(item);
      if (parsed.from) {
        fromInput.value = parsed.from;
        toInput.value = parsed.to;
        applyHouses();
      }
    });
    li.append(button);
    resultsEl.append(li);
  });
}

function useRussianLabels() {
  (map.getStyle().layers || []).forEach((layer) => {
    if (layer.type !== "symbol") return;
    if (/shield|ref|exit|arrow/i.test(layer.id)) return;
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
  if (today > queues.publishedUntil) {
    return {
      result: new Map(),
      note: `Сейчас ${clock}. График Киевского района действовал до ${queues.publishedUntil.split("-").reverse().join(".")}, новой карточки нет, по графику улицы не красим.`,
    };
  }
  return {
    result,
    note: `Сейчас ${clock}. По графику закрасилось ${onCount} улиц со светом и ${offCount} без света, Киевский район, график объявлен до ${queues.publishedUntil.split("-").reverse().join(".")}.`,
  };
}

function addStreets(streets) {
  streets.features.forEach((feature) => {
    const group = featuresByKey.get(feature.properties.key) || [];
    group.push(feature);
    featuresByKey.set(feature.properties.key, group);
  });
  const before = (map.getStyle().layers || []).find((layer) => layer.type === "symbol");
  const beforeId = before && before.id;
  const empty = { type: "FeatureCollection", features: [] };
  map.addSource("streets", { type: "geojson", data: streets, promoteId: "segment" });
  map.addSource("places", { type: "geojson", data: empty });
  map.addSource("pick", { type: "geojson", data: empty });
  const round = { "line-cap": "round", "line-join": "round" };
  // Жёлтая подсветка под линиями: вся найденная улица видна на карте целиком.
  map.addLayer(
    {
      id: "streets-focus",
      type: "line",
      source: "streets",
      layout: round,
      paint: {
        "line-color": "#ffc933",
        "line-width": 14,
        "line-opacity": ["case", ["boolean", ["feature-state", "focused"], false], 0.85, 0],
      },
    },
    beforeId
  );
  // Синее свечение под выбранным куском.
  map.addLayer(
    {
      id: "pick-glow",
      type: "line",
      source: "pick",
      filter: ["==", ["geometry-type"], "LineString"],
      layout: round,
      paint: { "line-color": "#1d4f91", "line-width": ["interpolate", ["linear"], ["zoom"], 11, 20, 14, 18, 17, 16], "line-opacity": 0.55 },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "streets-line",
      type: "line",
      source: "streets",
      paint: {
        "line-color": [
          "case",
          ["==", ["feature-state", "power"], "on"],
          "#1b7f4e",
          ["==", ["feature-state", "power"], "off"],
          "#b42318",
          "#8d867c",
        ],
        "line-width": [
          "case",
          ["any", ["==", ["feature-state", "power"], "on"], ["==", ["feature-state", "power"], "off"]],
          4.5,
          2.4,
        ],
        "line-opacity": [
          "case",
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
    beforeId
  );
  // Отметки по куску улицы лежат поверх цвета всей улицы.
  map.addLayer(
    {
      id: "places-line",
      type: "line",
      source: "places",
      layout: round,
      paint: {
        "line-color": ["case", ["==", ["get", "status"], "on"], "#1b7f4e", "#b42318"],
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 10, 14, 8, 17, 6],
        "line-opacity": ["case", ["==", ["get", "level"], "stale"], 0.4, ["==", ["get", "level"], "likely"], 0.75, 1],
      },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "pick-point",
      type: "circle",
      source: "pick",
      filter: ["all", ["==", ["geometry-type"], "Point"], ["!", ["has", "house"]]],
      paint: {
        "circle-radius": 7,
        "circle-color": "#ffffff",
        "circle-stroke-color": "#1d4f91",
        "circle-stroke-width": 3,
      },
    },
    beforeId
  );
  map.addLayer({
    id: "pick-houses",
    type: "circle",
    source: "pick",
    filter: ["has", "house"],
    paint: {
      "circle-radius": 4.5,
      "circle-color": "#1d4f91",
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.5,
    },
  });
  map.addLayer({
    id: "pick-house-labels",
    type: "symbol",
    source: "pick",
    minzoom: 15,
    filter: ["has", "house"],
    layout: {
      "text-field": ["get", "n"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 13,
      "text-offset": [0, -1.1],
      "text-allow-overlap": true,
    },
    paint: { "text-color": "#1d4f91", "text-halo-color": "#ffffff", "text-halo-width": 2 },
  });
  map.on("click", (event) => pickAt(event.point, event.lngLat));
  map.on("mousemove", (event) => {
    const near = map.queryRenderedFeatures(
      [
        [event.point.x - 10, event.point.y - 10],
        [event.point.x + 10, event.point.y + 10],
      ],
      { layers: ["streets-line"] }
    );
    map.getCanvas().style.cursor = near.length ? "pointer" : "";
  });
  paint();
}

async function boot() {
  const [indexResponse, streetResponse, queueResponse, contactResponse] = await Promise.all([
    fetch("data/streets-index.json"),
    fetch("data/streets.geojson"),
    fetch("data/kyiv-queues.json"),
    fetch("data/contacts.json"),
  ]);
  renderContacts(await contactResponse.json());
  index = await indexResponse.json();
  const streets = await streetResponse.json();
  const queues = await queueResponse.json();
  autoByKey = scheduleNow(queues).result;
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

queryInput.addEventListener("input", () => search(queryInput.value));
onBtn.addEventListener("click", () => mark("on"));
offBtn.addEventListener("click", () => mark("off"));
chipsEl.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  scope = Number(button.dataset.r);
  flash = "";
  if (houses) {
    clearHouses();
    renderPick();
  }
  renderPick();
  renderCard();
});
housesBtn.addEventListener("click", () => applyHouses());
[fromInput, toInput].forEach((input) => {
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyHouses();
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
      markNote.textContent = "Место не пришло. Нажмите улицу на карте.";
    },
    { enableHighAccuracy: true, timeout: 8000 }
  );
});

boot().catch(() => {
  markNote.textContent = "Данные не загрузились. Обновите страницу.";
});
