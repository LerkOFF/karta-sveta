// Карта света для жителей: смотреть и отмечать. Источники и «как считается» на about.html.
const LEVEL_WORD = {
  sure: "уверенно",
  likely: "скорее всего",
  stale: "давно не подтверждали",
};

const BULLETIN_SHOW_MS = 12 * 60 * 60 * 1000;

// Вывод по соседям: отметка слабо красит ближайшие куски других улиц.
const NEIGHBOR_R = 300;
const NEIGHBOR_MIN = 0.12;
const NEIGHBOR_MAX_STREET_M = 1500;
const CELL = 0.0045;

// Длина отмечаемого куска улицы вокруг нажатия: радиус в метрах, 0 = вся улица.
const DEFAULT_SCOPE = 100;

// Область «дом»: круг 18 м, накрывает один дом, дом напротив остаётся при своём. Дома рисуются точками с приближения HOUSE_ZOOM.
const HOUSE_SCOPE = 18;
const HOUSE_ZOOM = 16;
// Дальше 200 м вокруг человека свет не отмечаем, улицу целиком тоже.
const MAX_SCOPE = 200;
const HOUSE_MAX = 6000;

const queryInput = document.querySelector("#query");
const resultsEl = document.querySelector("#results");
const pickedEl = document.querySelector("#picked");
const onBtn = document.querySelector("#on");
const offBtn = document.querySelector("#off");
const hereBtn = document.querySelector("#here");
const markNote = document.querySelector("#mark-note");
const mapEl = document.querySelector("#map");
const unpickBtn = document.querySelector("#unpick");
const foldBtn = document.querySelector("#fold");
const cardEl = document.querySelector("#card");
const bulletinEl = document.querySelector("#bulletin");
const chipsEl = document.querySelector("#scopes");
const fromInput = document.querySelector("#h-from");
const toInput = document.querySelector("#h-to");
const housesBtn = document.querySelector("#h-go");
const groupsEl = document.querySelector("#groups");
const phonesEl = document.querySelector("#phones");
const watchEl = document.querySelector("#watch");
const watchText = document.querySelector("#watch-text");
const watchOffBtn = document.querySelector("#watch-off");

// Тот же ключ, что на странице «О карте»: там датчик можно отключить по токену.
const TOKEN_KEY = "karta-sensor-token";

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
// Датчик, который сервер сам подключает при отметке: ответ сервера о моём датчике и сообщение.
let watchInfo = null;
let watchMsg = "";
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
let segInfo = new Map();
let grid = new Map();
let neighborBySegment = new Map();
// Все дома города {ключ улицы: [[номер, lon, lat], ...]}, грузятся при первом приближении.
let housesAll = null;
let housesLoading = false;

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

// Свежая отметка важнее графика, график важнее вывода по соседям. Если ничего нет, улица зелёная
// «по умолчанию»: так карта заполнена, и люди отмечают там, где света нет.
function baselineKind() {
  return bulletin && bulletin.kind === "all_on" && nowMs() - bulletin.at <= BULLETIN_SHOW_MS ? "bulletin" : "default";
}

function stateOf(key, segment) {
  const live = liveByKey.get(key) || null;
  if (live && live.level !== "stale") return { power: live.status, level: live.level, live, exact: !!live.exact };
  const plan = autoByKey.get(key);
  if (plan) return { power: plan, level: "schedule", live, exact: true };
  const near = segment ? neighborBySegment.get(segment) : null;
  if (near) return { power: near.status, level: "neighbor", live, near };
  if (live && !live.superseded_at) return { power: live.status, level: "stale", live, exact: !!live.exact };
  return { power: "on", level: "baseline", live, base: baselineKind() };
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
    const exactness = live.exact ? " Точные данные." : " Со слов жителей или из чата, не точные данные.";
    return `${word(live.status)}, ${ago(live.at)}: ${LEVEL_WORD[state.level]}, ${sourcesWord(live.sources)}.${dispute}${restoredNote(live)} Последняя отметка: «${live.source}».${exactness}`;
  }
  if (state.level === "area") {
    const area = state.area;
    return `Этот дом попадает в область отметки на «${area.name}»: ${area.status === "on" ? "свет есть" : "света нет"}, ${ago(area.at)}. Это область вокруг отметки, а не отметка по самому дому.`;
  }
  if (state.level === "schedule") {
    const old = live ? ` Последняя отметка (${word(live.status).toLowerCase()}, ${ago(live.at)}) устарела.${restoredNote(live, false)}` : "";
    return `По графику сейчас ${state.power === "on" ? "свет есть" : "света нет"}. Свежих отметок нет.${old}`;
  }
  if (state.level === "stale") {
    return `${word(live.status)} ${ago(live.at)}, с тех пор никто не подтверждал.${restoredNote(live)} Цвет блёклый: верить с оглядкой.`;
  }
  if (state.level === "neighbor") {
    const near = state.near;
    const word2 = near.from.status === "on" ? "свет есть" : "света нет";
    const dist = near.dist < 40 ? "рядом" : `примерно в ${Math.round(near.dist / 10) * 10} м`;
    return `Прямых отметок по этой улице нет. По соседям: ${dist} на «${near.from.name}» ${word2} (${ago(near.from.at)}). Это вывод по соседям, а не отметка, на самом деле может быть иначе.`;
  }
  if (state.level === "baseline") {
    if (state.base === "bulletin") {
      return `Данных по этой улице нет. По сводке Донецкэнерго (${clock(bulletin.at)}) все подстанции запитаны, поэтому считаем, что свет есть. Это общая сводка по городу, а не отметка. Если света нет, отметьте.`;
    }
    return "Данных по этой улице нет, считаем по умолчанию, что свет есть. Если света нет, отметьте это кнопкой.";
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
        if (!best || d < best.d) best = { d, lon: lngLat.lng + px / kx, lat: lngLat.lat + py / ky, segment: feature.properties.segment };
      }
    });
  });
  return best;
}

// Индекс кусков улиц по сетке: по нему быстро находятся соседи отметки.
function samplePoints(feature) {
  const pts = [];
  linesOf(feature).forEach((line) => {
    const step = Math.max(1, Math.ceil(line.length / 8));
    for (let i = 0; i < line.length; i += step) pts.push(line[i]);
    pts.push(line[line.length - 1]);
  });
  return pts;
}

function cellOf(lon, lat) {
  return `${Math.floor(lon / CELL)}:${Math.floor(lat / CELL)}`;
}

function buildGeometryIndex(features) {
  features.forEach((feature) => {
    const segment = feature.properties.segment;
    const pts = samplePoints(feature);
    segInfo.set(segment, { key: feature.properties.key, pts });
    new Set(pts.map(([x, y]) => cellOf(x, y))).forEach((cell) => {
      if (!grid.has(cell)) grid.set(cell, []);
      grid.get(cell).push(segment);
    });
  });
}

function candidatesNear(lon, lat, meters) {
  const dLat = meters / 110540;
  const dLon = meters / (111320 * Math.cos((lat * Math.PI) / 180));
  const out = new Set();
  for (let x = Math.floor((lon - dLon) / CELL); x <= Math.floor((lon + dLon) / CELL); x += 1) {
    for (let y = Math.floor((lat - dLat) / CELL); y <= Math.floor((lat + dLat) / CELL); y += 1) {
      (grid.get(`${x}:${y}`) || []).forEach((segment) => out.add(segment));
    }
  }
  return out;
}

// Отметка со статусом «уверенно» или «скорее всего» слабо красит ближайшие куски других улиц:
// до 0.6 от её уверенности рядом и ноль на расстоянии NEIGHBOR_R. Если рядом спорят «есть» и «нет»
// и ни одна сторона не сильнее другой в полтора раза, кусок не красим.
function computeNeighbors() {
  const sources = [];
  liveReports.forEach((report) => {
    if (report.level === "stale") return;
    const group = featuresByKey.get(report.key) || [];
    const meters = group.reduce((sum, feature) => sum + (feature.properties.meters || 0), 0);
    if (!group.length || meters > NEIGHBOR_MAX_STREET_M) return;
    const pts = group.flatMap((feature) => (segInfo.get(feature.properties.segment) || { pts: [] }).pts);
    sources.push({ key: report.key, status: report.status, conf: report.confidence, name: report.name, at: report.at, pts, radius: 0 });
  });
  livePlaces.forEach((place) => {
    if (place.level === "stale") return;
    sources.push({
      key: place.key,
      status: place.status,
      conf: place.confidence,
      name: place.name,
      at: place.at,
      center: [place.lon, place.lat],
      radius: place.radius,
    });
  });

  const best = new Map();
  sources.forEach((source) => {
    const found = new Set();
    if (source.center) {
      candidatesNear(source.center[0], source.center[1], source.radius + NEIGHBOR_R).forEach((segment) => found.add(segment));
    } else {
      source.pts.forEach(([x, y]) => candidatesNear(x, y, NEIGHBOR_R).forEach((segment) => found.add(segment)));
    }
    found.forEach((segment) => {
      const info = segInfo.get(segment);
      if (!info || info.key === source.key) return;
      let d = Infinity;
      if (source.center) {
        info.pts.forEach((pt) => {
          d = Math.min(d, distance(source.center, pt));
        });
        d = Math.max(0, d - source.radius);
      } else {
        source.pts.forEach((a) => info.pts.forEach((b) => {
          d = Math.min(d, distance(a, b));
        }));
      }
      if (d >= NEIGHBOR_R) return;
      const conf = source.conf * 0.6 * (1 - d / NEIGHBOR_R);
      if (conf < NEIGHBOR_MIN) return;
      const slot = best.get(segment) || {};
      if (!slot[source.status] || slot[source.status].conf < conf) {
        slot[source.status] = { conf, dist: d, from: { name: source.name, status: source.status, at: source.at } };
      }
      best.set(segment, slot);
    });
  });

  const result = new Map();
  best.forEach((slot, segment) => {
    let status;
    if (slot.on && slot.off) {
      if (slot.on.conf >= slot.off.conf * 1.5) status = "on";
      else if (slot.off.conf >= slot.on.conf * 1.5) status = "off";
      else return;
    } else {
      status = slot.on ? "on" : "off";
    }
    result.set(segment, { status, ...slot[status] });
  });
  neighborBySegment = result;
}

function applyState(segment, key) {
  const state = stateOf(key, segment);
  map.setFeatureState(
    { source: "streets", id: segment },
    { power: state.power, level: state.level, exact: !!state.exact, focused: key === focusKey }
  );
}

function paint() {
  featuresByKey.forEach((group, key) => {
    group.forEach((feature) => applyState(feature.properties.segment, key));
  });
  renderHouseDots();
  renderCard();
}

// Что известно про выбранное место: свежая отметка на этом куске, иначе общий цвет улицы.
function stateAt() {
  if (!pick) return null;
  if (pick.house && scope === HOUSE_SCOPE) return houseState(pick.key, pick.lon, pick.lat);
  if (scope) {
    const here = livePlaces
      .filter((p) => p.key === pick.key && distance([pick.lon, pick.lat], [p.lon, p.lat]) <= p.radius)
      .sort((a, b) => b.at - a.at)[0];
    if (here) return { power: here.status, level: here.level, live: here };
  }
  const found = nearestOnStreet(pick.key, { lng: pick.lon, lat: pick.lat });
  return stateOf(pick.key, found && found.segment);
}

function scopeText() {
  if (houses) return `дома ${houses.label}`;
  if (scope === HOUSE_SCOPE) return pick && pick.house ? `дом ${pick.house}` : "один дом";
  return `в радиусе ${scope} м`;
}

// Браузеры на Android сами говорят, что человек на мобильной сети; тогда адрес дома он не покажет.
function connectionKind() {
  const link = navigator.connection;
  return link && link.type === "cellular" ? "cellular" : "";
}

function savedToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (error) {
    return "";
  }
}

function saveToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (error) {
    /* без хранилища датчик останется, но отключить его можно будет только с токеном */
  }
}

function watchStatus() {
  const info = watchInfo;
  if (info.state === "probation") {
    const when = new Date(info.ready_at).toLocaleString("ru", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
    return info.ready_at > Date.now() + clockShift
      ? `Датчик подключён, идёт проверка адреса. Откройте карту из дома не раньше ${when}: если IP тот же, датчик заработает.`
      : "Датчик ждёт подтверждения адреса: откройте карту из дома, с домашнего Wi-Fi.";
  }
  if (info.sensor === "on") return "Датчик работает: у вас сейчас свет есть.";
  if (info.sensor === "off") return "Датчик работает: у вас сейчас света нет.";
  return "Датчик работает, ждём первой проверки.";
}

function renderWatch() {
  const mine = !!(watchInfo && watchInfo.ok);
  watchEl.hidden = !(mine || watchMsg);
  watchText.textContent = watchMsg || (mine ? watchStatus() : "");
  watchOffBtn.hidden = !mine;
}

async function refreshWatch() {
  const token = savedToken();
  if (!token) {
    watchInfo = null;
    renderWatch();
    return;
  }
  const response = await fetch("api/watch/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, net: connectionKind() }),
  });
  const result = await response.json();
  if (result.state === "gone") saveToken("");
  watchInfo = result.ok ? result : null;
  renderWatch();
}

async function watchOff() {
  const response = await fetch("api/sensor/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: savedToken() }),
  });
  const result = await response.json();
  if (result.ok) {
    saveToken("");
    watchInfo = null;
    watchMsg = "Датчик отключён, IP удалён.";
  } else {
    watchMsg = result.reason || "Не получилось отключить.";
  }
  renderWatch();
}

function renderCard() {
  renderWatch();
  chipsEl.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("on", !houses && Number(button.dataset.r) === scope);
  });
  onBtn.disabled = !pick;
  offBtn.disabled = !pick;
  mapEl.classList.toggle("picking", !!pick);
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

// Область отметки: круг радиуса radius вокруг точки.
function circleRing(center, radius, steps = 48) {
  const [kx, ky] = metersPerDegree(center[1]);
  const ring = [];
  for (let i = 0; i < steps; i += 1) {
    const angle = (2 * Math.PI * i) / steps;
    ring.push([center[0] + (Math.cos(angle) * radius) / kx, center[1] + (Math.sin(angle) * radius) / ky]);
  }
  ring.push(ring[0]);
  return ring;
}

// Зоны разных ответов не налезают друг на друга и касаются по границе: точка принадлежит ближайшей
// отметке из тех, что её накрывают. Здесь о том, кому принадлежит точка, если рядом есть отметка с другим ответом.
function zoneRivals(place) {
  const center = [place.lon, place.lat];
  return livePlaces.filter(
    (other) =>
      other !== place &&
      other.level !== "stale" &&
      other.status !== place.status &&
      distance(center, [other.lon, other.lat]) < place.radius + other.radius
  );
}

function beats(rival, place, at) {
  const near = distance(at, [rival.lon, rival.lat]);
  if (near > rival.radius) return false;
  const own = distance(at, [place.lon, place.lat]);
  return near < own || (near === own && rival.at > place.at);
}

function ownsPoint(place, rivals, at) {
  return distance(at, [place.lon, place.lat]) <= place.radius + 0.5 && !rivals.some((rival) => beats(rival, place, at));
}

// Контур зоны: круг, срезанный там, где точка уже ближе к отметке с другим ответом. cut[i] значит,
// что вершина i лежит на общей границе с соседней зоной.
function zoneShape(place) {
  const center = [place.lon, place.lat];
  const rivals = zoneRivals(place);
  if (!rivals.length) return { ring: circleRing(center, place.radius), cut: null, rivals };
  const [kx, ky] = metersPerDegree(center[1]);
  const steps = 64;
  const ring = [];
  const cut = [];
  for (let i = 0; i < steps; i += 1) {
    const angle = (2 * Math.PI * i) / steps;
    const at = (r) => [center[0] + (Math.cos(angle) * r) / kx, center[1] + (Math.sin(angle) * r) / ky];
    let reach = place.radius;
    let lost = false;
    for (let r = 6; r <= place.radius + 6; r += 6) {
      if (!ownsPoint(place, rivals, at(Math.min(r, place.radius)))) {
        let lo = r - 6;
        let hi = Math.min(r, place.radius);
        for (let k = 0; k < 6; k += 1) {
          const mid = (lo + hi) / 2;
          if (ownsPoint(place, rivals, at(mid))) lo = mid;
          else hi = mid;
        }
        reach = lo;
        lost = true;
        break;
      }
      if (r >= place.radius) break;
    }
    ring.push(at(reach));
    cut.push(lost);
  }
  ring.push(ring[0]);
  return { ring, cut, rivals };
}

// Край зоны по кускам: внешняя дуга и шов с соседней зоной отдельными линиями.
function zoneEdges(shape) {
  if (!shape.cut) return [{ seam: false, coordinates: shape.ring }];
  const edges = [];
  const count = shape.ring.length - 1;
  let current = null;
  for (let i = 0; i < count; i += 1) {
    const next = (i + 1) % count;
    const seam = shape.cut[i] && shape.cut[next];
    if (!current || current.seam !== seam) {
      current = { seam, coordinates: [shape.ring[i]] };
      edges.push(current);
    }
    current.coordinates.push(shape.ring[next]);
  }
  return edges;
}

// Линия на 6 м частями: так её можно резать по границе зоны.
function densify(line, step = 6) {
  const out = [line[0]];
  for (let i = 1; i < line.length; i += 1) {
    const [a, b] = [line[i - 1], line[i]];
    const n = Math.max(1, Math.ceil(distance(a, b) / step));
    for (let k = 1; k <= n; k += 1) out.push([a[0] + ((b[0] - a[0]) * k) / n, a[1] + ((b[1] - a[1]) * k) / n]);
  }
  return out;
}

function ownedPieces(place, rivals, pieces) {
  if (!rivals.length) return pieces;
  const out = [];
  pieces.forEach((line) => {
    let current = null;
    densify(line).forEach((point) => {
      if (ownsPoint(place, rivals, point)) {
        if (!current) {
          current = [];
          out.push(current);
        }
        current.push(point);
      } else {
        current = null;
      }
    });
  });
  return out.filter((line) => line.length > 1);
}

function renderPlaces() {
  const source = map.getSource("places");
  const areas = map.getSource("areas");
  if (!source || !areas) return;
  const features = [];
  const zones = [];
  livePlaces.forEach((place) => {
    const props = { status: place.status, level: place.level, exact: !!place.exact };
    const shape = zoneShape(place);
    zones.push({ type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [shape.ring] } });
    zoneEdges(shape).forEach((edge) => {
      zones.push({
        type: "Feature",
        properties: { ...props, seam: edge.seam },
        geometry: { type: "LineString", coordinates: edge.coordinates },
      });
    });
    const lines = ownedPieces(place, shape.rivals, piecesOf(place.key, [place.lon, place.lat], place.radius));
    if (!lines.length) return;
    features.push({ type: "Feature", properties: props, geometry: { type: "MultiLineString", coordinates: lines } });
  });
  source.setData({ type: "FeatureCollection", features });
  areas.setData({ type: "FeatureCollection", features: zones });
}

// Что известно про дом: ближайшая отметка из тех, чья зона его накрывает (отметка по улице дома, если
// зоны нет рядом, а улица или график уже известны, идёт раньше чужой зоны); иначе улица целиком или график,
// иначе вывод по соседям, иначе блёклая старая область, иначе «по умолчанию».
function houseState(key, lon, lat) {
  const at = [lon, lat];
  const gap = (p) => distance(at, [p.lon, p.lat]);
  const covering = livePlaces.filter((p) => p.level !== "stale" && gap(p) <= p.radius);
  const nearest = covering.sort((a, b) => gap(a) - gap(b) || b.at - a.at)[0];
  const street = stateOf(key, nearestSegmentOf(key, lon, lat));
  const streetKnown = street.level === "sure" || street.level === "likely" || street.level === "schedule";
  if (nearest && nearest.key === key) return { power: nearest.status, level: nearest.level, live: nearest, exact: !!nearest.exact };
  if (streetKnown) return street;
  if (nearest) return { power: nearest.status, level: "area", live: nearest, exact: false, area: nearest };
  if (street.level === "baseline") {
    const old = livePlaces
      .filter((p) => p.key === key && gap(p) <= p.radius)
      .sort((a, b) => b.at - a.at)[0];
    if (old) return { power: old.status, level: "stale", live: old, exact: !!old.exact };
  }
  return street;
}

function nearestSegmentOf(key, lon, lat) {
  let best = null;
  candidatesNear(lon, lat, 80).forEach((segment) => {
    const info = segInfo.get(segment);
    if (!info || info.key !== key) return;
    info.pts.forEach((pt) => {
      const d = distance([lon, lat], pt);
      if (!best || d < best.d) best = { d, segment };
    });
  });
  if (best) return best.segment;
  const found = nearestOnStreet(key, { lng: lon, lat });
  return found ? found.segment : null;
}

async function loadAllHouses() {
  if (housesAll || housesLoading) return;
  housesLoading = true;
  try {
    const response = await fetch("data/houses.json");
    housesAll = await response.json();
  } catch (error) {
    housesAll = null;
  }
  housesLoading = false;
  renderHouseDots();
}

// Дома в окне карты точками: сплошная точка точные данные, кольцо отметка жителей или область.
function renderHouseDots() {
  const source = map.getSource("house-dots");
  if (!source) return;
  const features = [];
  if (housesAll && map.getZoom() >= HOUSE_ZOOM && featuresByKey.size) {
    const box = map.getBounds();
    const [west, east, south, north] = [box.getWest(), box.getEast(), box.getSouth(), box.getNorth()];
    scan: for (const key of Object.keys(housesAll)) {
      for (const row of housesAll[key]) {
        if (row[1] < west || row[1] > east || row[2] < south || row[2] > north) continue;
        const state = houseState(key, row[1], row[2]);
        features.push({
          type: "Feature",
          properties: { power: state.power, level: state.level, exact: !!state.exact, n: row[0], key },
          geometry: { type: "Point", coordinates: [row[1], row[2]] },
        });
        if (features.length >= HOUSE_MAX) break scan;
      }
    }
  } else if (!housesAll && map.getZoom() >= HOUSE_ZOOM) {
    loadAllHouses();
  }
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

// Окно можно свернуть, чтобы оно не закрывало карту; состояние запоминается в браузере.
const FOLD_KEY = "karta-card-folded";

function setFolded(folded, remember = true) {
  cardEl.classList.toggle("folded", folded);
  foldBtn.textContent = folded ? "Отметить свет ▾" : "−";
  foldBtn.setAttribute("aria-expanded", String(!folded));
  foldBtn.setAttribute("aria-label", folded ? "Развернуть окно" : "Свернуть окно");
  if (!remember) return;
  try {
    localStorage.setItem(FOLD_KEY, folded ? "1" : "");
  } catch (error) {
    /* без хранилища окно просто развернётся при следующем заходе */
  }
}

foldBtn.addEventListener("click", () => setFolded(!cardEl.classList.contains("folded")));
try {
  if (localStorage.getItem(FOLD_KEY)) setFolded(true, false);
} catch (error) {
  /* хранилище недоступно: окно развёрнуто */
}

function setPick(next) {
  if (next) setFolded(false, false);
  pick = next;
  watchMsg = "";
  focusKey = null;
  flash = "";
  clearHouses();
  renderPick();
  paint();
}

unpickBtn.addEventListener("click", () => setPick(null));

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

// Нажатие на точку дома выбирает дом: отметка пойдёт по области около него.
function pickHouse(point, lngLat) {
  if (!housesAll || map.getZoom() < HOUSE_ZOOM || !map.getLayer("houses-dot")) return false;
  const dots = map.queryRenderedFeatures(
    [
      [point.x - 12, point.y - 12],
      [point.x + 12, point.y + 12],
    ],
    { layers: ["houses-dot"] }
  );
  if (!dots.length) return false;
  const here = [lngLat.lng, lngLat.lat];
  const dot = dots.sort(
    (a, b) => distance(here, a.geometry.coordinates) - distance(here, b.geometry.coordinates)
  )[0];
  const item = index.find((row) => row.key === dot.properties.key);
  scope = HOUSE_SCOPE;
  setPick({
    key: dot.properties.key,
    name: item ? item.name : dot.properties.key,
    lon: dot.geometry.coordinates[0],
    lat: dot.geometry.coordinates[1],
    house: String(dot.properties.n),
  });
  return true;
}

function pickAt(point, lngLat) {
  if (pickHouse(point, lngLat)) return;
  const hits = map.queryRenderedFeatures(
    [
      [point.x - 18, point.y - 18],
      [point.x + 18, point.y + 18],
    ],
    { layers: ["streets-line"] }
  );
  if (!hits.length) {
    if (pick || focusKey) {
      setPick(null);
      return;
    }
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
  if (reach + 25 > MAX_SCOPE) {
    flash = "";
    markNote.textContent = `Дома ${label} лежат дальше ${MAX_SCOPE} м от центра: отметить можно только в радиусе ${MAX_SCOPE} м. Возьмите диапазон поуже.`;
    return;
  }
  houses = { label, count: chosen.length, items: chosen, estimated: Boolean(estimate) };
  pick = { key, name: item.name, lon: center[0], lat: center[1] };
  scope = Math.max(50, Math.ceil(reach) + 25);
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
  const body = { key: pick.key, status, token: savedToken(), net: connectionKind() };
  if (scope) Object.assign(body, { lon: pick.lon, lat: pick.lat, radius: scope });
  if (scope && houses) body.label = `дома ${houses.label}`;
  else if (scope === HOUSE_SCOPE && pick.house) body.label = `дом ${pick.house}`;
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
        if (result.sensor) saveToken(result.sensor);
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
  if (featuresByKey.size) {
    computeNeighbors();
    paint();
  }
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

// Штриховка для областей по отметкам жителей: диагональные линии цвета ответа на прозрачном фоне.
function addHatchImages() {
  [["on", "27, 127, 78"], ["off", "180, 35, 24"]].forEach(([status, rgb]) => {
    if (map.hasImage(`hatch-${status}`)) return;
    const size = 32;
    const canvas = document.createElement("canvas");
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    ctx.strokeStyle = `rgba(${rgb}, 0.6)`;
    ctx.lineWidth = 3;
    [-size / 2, 0, size / 2].forEach((shift) => {
      ctx.beginPath();
      ctx.moveTo(shift - 2, size + 2);
      ctx.lineTo(shift + size + 2, -2);
      ctx.stroke();
    });
    map.addImage(`hatch-${status}`, ctx.getImageData(0, 0, size, size), { pixelRatio: 2 });
  });
}

function addStreets(streets) {
  streets.features.forEach((feature) => {
    const group = featuresByKey.get(feature.properties.key) || [];
    group.push(feature);
    featuresByKey.set(feature.properties.key, group);
  });
  buildGeometryIndex(streets.features);
  const before = (map.getStyle().layers || []).find((layer) => layer.type === "symbol");
  const beforeId = before && before.id;
  const empty = { type: "FeatureCollection", features: [] };
  map.addSource("streets", { type: "geojson", data: streets, promoteId: "segment" });
  map.addSource("places", { type: "geojson", data: empty });
  map.addSource("areas", { type: "geojson", data: empty });
  map.addSource("house-dots", { type: "geojson", data: empty });
  map.addSource("pick", { type: "geojson", data: empty });
  const round = { "line-cap": "round", "line-join": "round" };
  addHatchImages();
  const statusColor = ["case", ["==", ["get", "status"], "on"], "#1b7f4e", "#b42318"];
  const fadeOf = ["case", ["==", ["get", "level"], "stale"], 0.4, ["==", ["get", "level"], "likely"], 0.75, 1];
  // Области отметок: у точных данных сплошная заливка, у отметок жителей штриховка. Край зоны сплошной
  // у точных и пунктирный у остальных, шов между соседними зонами белый.
  const zoneFade = ["case", ["==", ["get", "level"], "stale"], 0.35, ["==", ["get", "level"], "likely"], 0.7, 1];
  map.addLayer(
    {
      id: "areas-fill",
      type: "fill",
      source: "areas",
      filter: ["all", ["==", ["geometry-type"], "Polygon"], ["==", ["get", "exact"], true]],
      paint: {
        "fill-color": statusColor,
        "fill-opacity": ["case", ["==", ["get", "level"], "stale"], 0.06, ["==", ["get", "level"], "likely"], 0.14, 0.2],
      },
    },
    beforeId
  );
  ["on", "off"].forEach((status) => {
    map.addLayer(
      {
        id: `areas-hatch-${status}`,
        type: "fill",
        source: "areas",
        filter: ["all", ["==", ["geometry-type"], "Polygon"], ["!=", ["get", "exact"], true], ["==", ["get", "status"], status]],
        paint: { "fill-pattern": `hatch-${status}`, "fill-opacity": zoneFade },
      },
      beforeId
    );
  });
  map.addLayer(
    {
      id: "areas-edge",
      type: "line",
      source: "areas",
      filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "exact"], true], ["!=", ["get", "seam"], true]],
      paint: { "line-color": statusColor, "line-width": 2, "line-opacity": fadeOf },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "areas-edge-approx",
      type: "line",
      source: "areas",
      filter: ["all", ["==", ["geometry-type"], "LineString"], ["!=", ["get", "exact"], true], ["!=", ["get", "seam"], true]],
      layout: { "line-cap": "butt" },
      paint: { "line-color": statusColor, "line-width": 2, "line-dasharray": [3, 2], "line-opacity": fadeOf },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "areas-seam",
      type: "line",
      source: "areas",
      filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "seam"], true]],
      paint: { "line-color": "#ffffff", "line-width": 2.4, "line-opacity": 0.9 },
    },
    beforeId
  );
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
          ["any", ["==", ["feature-state", "level"], "baseline"], ["==", ["feature-state", "level"], "neighbor"]],
          0,
          ["!", ["boolean", ["feature-state", "exact"], false]],
          0,
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
  // Отметки жителей и чатов не точные данные: рисуем пунктиром.
  map.addLayer(
    {
      id: "streets-approx",
      type: "line",
      source: "streets",
      layout: { "line-cap": "butt" },
      paint: {
        "line-color": ["case", ["==", ["feature-state", "power"], "off"], "#b42318", "#1b7f4e"],
        "line-width": 4.5,
        "line-dasharray": [3, 1.4],
        "line-opacity": [
          "case",
          ["boolean", ["feature-state", "exact"], false],
          0,
          ["==", ["feature-state", "level"], "sure"],
          1,
          ["==", ["feature-state", "level"], "likely"],
          0.75,
          ["==", ["feature-state", "level"], "stale"],
          0.4,
          0,
        ],
      },
    },
    beforeId
  );
  // Не настоящие отметки: «по соседям» частыми штрихами, «по сводке или по умолчанию» точками.
  map.addLayer(
    {
      id: "streets-nbr",
      type: "line",
      source: "streets",
      layout: { "line-cap": "butt" },
      paint: {
        "line-color": ["case", ["==", ["feature-state", "power"], "off"], "#b42318", "#1b7f4e"],
        "line-width": 3.2,
        "line-dasharray": [1, 1.5],
        "line-opacity": ["case", ["==", ["feature-state", "level"], "neighbor"], 0.85, 0],
      },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "streets-base",
      type: "line",
      source: "streets",
      layout: round,
      paint: {
        "line-color": "#1b7f4e",
        "line-width": 2.8,
        "line-dasharray": [0.1, 2.2],
        "line-opacity": ["case", ["==", ["feature-state", "level"], "baseline"], 0.75, 0],
      },
    },
    beforeId
  );
  // Отметки по куску улицы лежат поверх цвета всей улицы: сплошные у точных данных, пунктир у остальных.
  const placeWidth = ["interpolate", ["linear"], ["zoom"], 11, 10, 14, 8, 17, 6];
  map.addLayer(
    {
      id: "places-line",
      type: "line",
      source: "places",
      filter: ["==", ["get", "exact"], true],
      layout: round,
      paint: { "line-color": statusColor, "line-width": placeWidth, "line-opacity": fadeOf },
    },
    beforeId
  );
  map.addLayer(
    {
      id: "places-approx",
      type: "line",
      source: "places",
      filter: ["!=", ["get", "exact"], true],
      layout: { "line-cap": "butt", "line-join": "round" },
      paint: { "line-color": statusColor, "line-width": placeWidth, "line-dasharray": [2.4, 1.2], "line-opacity": fadeOf },
    },
    beforeId
  );
  // Дома точками: сплошная точка точные данные, кольцо отметка жителей или область, мелкая точка «по умолчанию».
  const houseColor = ["case", ["==", ["get", "power"], "off"], "#b42318", "#1b7f4e"];
  const houseFade = [
    "case",
    ["==", ["get", "level"], "stale"],
    0.45,
    ["==", ["get", "level"], "likely"],
    0.8,
    ["==", ["get", "level"], "baseline"],
    0.55,
    1,
  ];
  map.addLayer(
    {
      id: "houses-dot",
      type: "circle",
      source: "house-dots",
      paint: {
        "circle-radius": [
          "interpolate",
          ["linear"],
          ["zoom"],
          16,
          ["case", ["==", ["get", "level"], "baseline"], 2, 3.5],
          19,
          ["case", ["==", ["get", "level"], "baseline"], 3.5, 7],
        ],
        "circle-color": ["case", ["==", ["get", "exact"], true], houseColor, ["==", ["get", "level"], "baseline"], houseColor, "#fffdf8"],
        "circle-stroke-color": ["case", ["==", ["get", "exact"], true], "#ffffff", houseColor],
        "circle-stroke-width": ["case", ["==", ["get", "exact"], true], 1.5, ["==", ["get", "level"], "baseline"], 0, 2.2],
        "circle-opacity": houseFade,
        "circle-stroke-opacity": houseFade,
      },
    }
  );
  map.addLayer({
    id: "houses-label",
    type: "symbol",
    source: "house-dots",
    minzoom: 17,
    layout: {
      "text-field": ["get", "n"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 11,
      "text-offset": [0, -1],
    },
    paint: { "text-color": "#3a342d", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
  });
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
  map.on("moveend", renderHouseDots);
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
watchOffBtn.addEventListener("click", () => {
  watchOff().catch(() => {
    watchMsg = "Сервер не ответил.";
    renderWatch();
  });
});
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

refreshWatch().catch(() => {});
setInterval(() => {
  refreshWatch().catch(() => {});
}, 5 * 60 * 1000);

boot().catch(() => {
  markNote.textContent = "Данные не загрузились. Обновите страницу.";
});
