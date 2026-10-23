"""Страницы для поисковиков: улицы, районы, телефоны, график, указатели и sitemap.

Всё собирается на сервере из тех же данных, что и карта (data/*.json и база отметок), поэтому на каждой
странице настоящий текст и свежий статус улицы, а не пустая оболочка под JavaScript.
Публичный вход: route(path) возвращает (status, body, content_type) или None.
"""

import html
import json
import re
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import store
from model import distance_m

ROOT = Path(__file__).resolve().parent
SITE = "https://xn----ctbhcc4athwm3c.xn--p1ai"
NAME = "Карта света"
MSK = timezone(timedelta(hours=3))
LIVE_TTL = 30

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}


def translit(text: str) -> str:
    out = "".join(_TR.get(ch, ch) for ch in text.lower())
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", out)).strip("-")


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def display_name(name: str) -> str:
    """«Артёма улица» -> «улица Артёма», «Правды площадьдь» -> «площадь Правды»: так ищут люди."""
    name = name.replace("площадьдь", "площадь").strip()
    if name.startswith("ул "):
        name = "улица " + name[3:]
    words = name.split()
    if len(words) > 1 and words[-1] in ("улица", "переулок", "площадь"):
        return f"{words[-1]} {' '.join(words[:-1])}"
    return name


def in_street(name: str) -> str:
    """«улица Артёма» -> «на улице Артёма». Для проспектов и прочего склонять нечем, имя оставляем как есть."""
    for word, form in (("улица", "улице"), ("переулок", "переулке"), ("площадь", "площади")):
        if name.startswith(word + " "):
            return f"на {form} {name[len(word) + 1:]}"
    return name


def proper_name(name: str) -> str:
    """Название без слова «улица» и т. п.: по нему улицы идут в алфавитный указатель."""
    words = display_name(name).split()
    if len(words) > 1 and words[0] in ("улица", "переулок", "площадь", "бульвар", "проспект", "шоссе"):
        words = words[1:]
    return " ".join(words)


def letter_of(name: str) -> str:
    first = proper_name(name)[:1].upper()
    first = "Е" if first == "Ё" else first
    return first if re.match(r"[А-ЯІЇЄҐ]", first) else "0-9"


def letter_slug(letter: str) -> str:
    if letter == "0-9":
        return "0-9"
    return "eh" if letter == "Э" else translit(letter) or "x"


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ago(minutes: float) -> str:
    minutes = int(minutes)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    return f"{hours} {plural(hours, 'час', 'часа', 'часов')} назад"


def km(meters: int) -> str:
    return f"{meters / 1000:.1f}".replace(".", ",") + " км"


# Прилагательное района: «Киевский» -> «Киевском», «Киевского».
def prepositional(district: str) -> str:
    return district[:-2] + "ом"


def genitive(district: str) -> str:
    return district[:-2] + "ого"


class Site:
    """Данные сайта в памяти: улицы, районы, график, дома. Строится один раз при запуске."""

    def __init__(self, houses=None):
        self.houses = houses or {}
        self.index = json.loads((ROOT / "data" / "streets-index.json").read_text(encoding="utf-8"))
        self.contacts = json.loads((ROOT / "data" / "contacts.json").read_text(encoding="utf-8"))
        self.queues = json.loads((ROOT / "data" / "kyiv-queues.json").read_text(encoding="utf-8"))
        self.by_slug = {}
        self.by_key = {}
        for item in self.index:
            slug = translit(item["key"]) or "street"
            base, n = slug, 2
            while slug in self.by_slug:
                slug = f"{base}-{n}"
                n += 1
            street = {
                **item,
                "slug": slug,
                "title": display_name(item["name"]),
                "letter": letter_of(item["name"]),
            }
            self.by_slug[slug] = street
            self.by_key[item["key"]] = street
        self.letters = {}
        for street in self.by_slug.values():
            self.letters.setdefault(street["letter"], []).append(street)
        for group in self.letters.values():
            group.sort(key=lambda s: proper_name(s["name"]).lower())
        self.districts = {translit(d["name"]): d for d in self.contacts["districts"]}
        self.stamp = datetime.now(MSK).strftime("%Y-%m-%d")
        self._live = (0, None)

    # --- живые данные -------------------------------------------------------------------------

    def live(self):
        now = time.time()
        if self._live[1] is None or now - self._live[0] > LIVE_TTL:
            with closing(store.connect()) as conn:
                state = store.live(conn)
            self._live = (now, state)
        return self._live[1]

    def street_status(self, key):
        state = self.live()
        whole = next((r for r in state["reports"] if r["key"] == key), None)
        places = [p for p in state["places"] if p["key"] == key]
        return whole, places

    def schedule_state(self, now=None):
        """Киевский район: какие очереди сейчас со светом по графику. Тот же расчёт, что в app.js."""
        now = now or datetime.now(MSK)
        queues = self.queues
        anchor = datetime.fromisoformat(queues["anchor"]).replace(tzinfo=MSK)
        day = (now - anchor).days
        shift = (day // 2) % 4
        minutes = now.hour * 60 + now.minute
        slot = next((i for i, (a, b) in enumerate(queues["slots"]) if a * 60 <= minutes < b * 60), -1)
        states = ["off"] * len(queues["blocks"])
        if slot >= 0:
            states[(slot - shift) % 4] = "on"
        expired = now.strftime("%Y-%m-%d") > queues["publishedUntil"]
        return {"states": states, "slot": slot, "shift": shift, "expired": expired}

    def street_in_schedule(self, key):
        found = {i for i, keys in enumerate(self.queues["blocks"]) if key in keys}
        return sorted(found)

    # --- общая оболочка -----------------------------------------------------------------------

    def nav(self):
        links = [("/", "Карта"), ("/rayony", "Районы"), ("/ulicy", "Улицы"), ("/grafik", "График"), ("/telefony", "Телефоны"), ("/about", "О карте")]
        return "".join(f'<a href="{href}">{label}</a>' for href, label in links)

    def layout(self, path, title, description, body, ld=None, og_image=True):
        url = SITE + path
        scripts = "".join(
            f'<script type="application/ld+json">{json.dumps(item, ensure_ascii=False)}</script>' for item in (ld or [])
        )
        image = f'<meta property="og:image" content="{SITE}/og.png"><meta name="twitter:image" content="{SITE}/og.png">' if og_image else ""
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="yandex-verification" content="28be99af0fad154b">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(description)}">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <meta name="theme-color" content="#f3efe6">
  <link rel="canonical" href="{esc(url)}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="{NAME}">
  <meta property="og:locale" content="ru_RU">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:url" content="{esc(url)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{esc(title)}">
  <meta name="twitter:description" content="{esc(description)}">
  {image}
  <link rel="icon" type="image/png" sizes="120x120" href="/favicon-120.png">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="stylesheet" href="/styles.css?v=21">
  {scripts}
  <script>
    (function(m,e,t,r,i,k,a){{m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};
    m[i].l=1*new Date();
    for (var j=0;j<document.scripts.length;j++){{if(document.scripts[j].src===r){{return;}}}}
    k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)}})
    (window,document,"script","https://mc.yandex.ru/metrika/tag.js","ym");
    ym(113728535,"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true}});
  </script>
  <noscript><div><img src="https://mc.yandex.ru/watch/113728535" style="position:absolute;left:-9999px" alt=""></div></noscript>
</head>
<body class="page">
  <article class="about seo">
    <nav class="topnav" aria-label="Разделы">{self.nav()}</nav>
{body}
    <p class="foot">{NAME}: независимый сайт, не связан с Донецкэнерго. Данные собраны из отметок людей, чатов и открытых источников, поэтому проверяйте важное по телефону диспетчера. Линии улиц © участники OpenStreetMap.</p>
  </article>
</body>
</html>
"""

    def crumbs(self, items):
        """items: [(путь, название)], последняя без ссылки. Возвращает (html, json-ld)."""
        parts = []
        for i, (href, label) in enumerate(items):
            last = i == len(items) - 1
            parts.append(f"<span>{esc(label)}</span>" if last else f'<a href="{href}">{esc(label)}</a>')
        markup = f'<p class="eyebrow crumbs">{" › ".join(parts)}</p>'
        ld = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": label, "item": SITE + href}
                for i, (href, label) in enumerate(items)
            ],
        }
        return markup, ld

    def map_link(self, key=None, label="Открыть на карте"):
        href = "/?street=" + quote(key) if key else "/"
        return f'<a class="btn on cta" href="{href}">{esc(label)}</a>'

    # --- улица --------------------------------------------------------------------------------

    def nearby(self, street, count=10):
        near = sorted(
            (s for s in self.by_slug.values() if s is not street),
            key=lambda s: distance_m(street["lon"], street["lat"], s["lon"], s["lat"]),
        )
        return near[:count]

    def house_numbers(self, key):
        raw = [str(h[0]) for h in self.houses.get(key, [])]
        digits = sorted(int(m.group()) for m in (re.match(r"\d+", n) for n in raw) if m)
        return len(raw), (digits[0] if digits else None), (digits[-1] if digits else None)

    def status_html(self, street):
        whole, places = self.street_status(street["key"])
        levels = {"sure": "уверенно", "likely": "скорее всего", "stale": "давно не подтверждали"}
        lines = []
        verdict = "Данных за сутки нет"
        mood = "none"
        if whole:
            on = whole["status"] == "on"
            verdict = "Свет есть" if on else "Света нет"
            mood = "on" if on else "off"
            how = "по точным данным" if whole.get("exact") else "по отметкам жителей"
            lines.append(
                f"Последняя отметка по улице: {verdict.lower()}, {ago(whole['age_min'])}, {levels[whole['level']]}, {how}."
            )
            if whole.get("conflict"):
                lines.append("Отметки спорят: часть людей пишет обратное.")
        if places:
            on = sum(1 for p in places if p["status"] == "on")
            off = len(places) - on
            where = []
            if on:
                where.append(f"{on} со светом")
            if off:
                where.append(f"{off} без света")
            lines.append(f"Отдельные участки улицы отмечены свежими областями: {', '.join(where)}.")
            if not whole:
                mood = "off" if off else "on"
                verdict = "Есть отметки по участкам"
        blocks = self.street_in_schedule(street["key"])
        if blocks:
            sched = self.schedule_state()
            if not sched["expired"]:
                states = {sched["states"][i] for i in blocks}
                if len(states) == 1:
                    now_on = states == {"on"}
                    lines.append(
                        f"По графику подачи света Киевского района сейчас: {'свет должен быть' if now_on else 'света по графику нет'}. "
                        '<a href="/grafik">Весь график</a>.'
                    )
            else:
                lines.append(
                    f"Улица есть в графике Киевского района, но он объявлен до "
                    f"{'.'.join(reversed(self.queues['publishedUntil'].split('-')))}, нового пока нет."
                )
        if not lines:
            lines.append(
                "За последние сутки отметок по этой улице нет. Если вы здесь, отметьте на карте, есть ли свет: это займёт пару секунд."
            )
        return mood, verdict, "".join(f"<p>{line}</p>" for line in lines)

    def street_page(self, slug):
        street = self.by_slug.get(slug)
        if not street:
            return None
        title_name = street["title"]
        path = f"/ulica/{slug}"
        mood, verdict, status = self.status_html(street)
        total = street["meters"]
        parts = street["parts"]
        count, first, last = self.house_numbers(street["key"])
        facts = [
            f"Длина улицы на карте около {km(total)}, она разбита на {parts} {plural(parts, 'участок', 'участка', 'участков')} примерно по 500 м: свет можно отметить на любом куске отдельно."
        ]
        if count:
            span = f", номера от {first} до {last}" if first is not None and last is not None and first != last else ""
            facts.append(f"В базе адресов OpenStreetMap у этой улицы {count} {plural(count, 'дом', 'дома', 'домов')}{span}. Можно отметить свет по диапазону домов или по одному дому.")
        near = self.nearby(street)
        near_links = "".join(f'<a class="pill" href="/ulica/{s["slug"]}">{esc(s["title"])}</a>' for s in near)
        crumb_html, crumb_ld = self.crumbs(
            [("/", NAME), ("/ulicy", "Улицы"), (f"/ulicy/{letter_slug(street['letter'])}", f"Улицы на {street['letter']}"), (path, title_name)]
        )
        where = in_street(title_name)
        page_title = f"Свет {where} в Донецке: есть ли сейчас" if where != title_name else f"Свет: {title_name}, Донецк, есть ли сейчас"
        description = (
            f"Есть ли свет {where if where != title_name else 'на улице ' + title_name} в Донецке прямо сейчас: "
            f"последние отметки жителей, возраст отметки и уверенность. Длина около {km(total)}"
            + (f", {count} {plural(count, 'дом', 'дома', 'домов')} в базе" if count else "")
            + ". Отметьте свет на карте."
        )
        city = self.contacts["city"]
        phones = " ".join(f'<a href="tel:{re.sub(r"[^+0-9]", "", p)}">{esc(p)}</a>' for p in city["phones"])
        body = f"""    {crumb_html}
    <h1>Свет {esc(where)} в Донецке</h1>
    <div class="status {mood}">
      <strong>{esc(verdict)}</strong>
      {status}
    </div>
    <p>{self.map_link(street['key'], 'Показать на карте и отметить свет')}</p>
    <section class="block">
      <h2>Что известно про {esc(title_name)}</h2>
      {''.join(f'<p>{esc(f)}</p>' for f in facts)}
      <p>Цвет улицы на карте зависит от возраста последней отметки: чем она старше, тем блёклее. Через сутки без подтверждения улица становится серой, потому что про свет час назад и неделю назад это разные сведения.</p>
    </section>
    <section class="block">
      <h2>Нет света: куда звонить</h2>
      <p>Информационно-консультационный центр Донецкэнерго: <span class="phones">{phones}</span></p>
      <p>Телефоны диспетчеров по районам: <a href="/telefony">куда звонить, если нет света</a>.</p>
    </section>
    <section class="block">
      <h2>Соседние улицы</h2>
      <div class="pills">{near_links}</div>
    </section>"""
        ld = [crumb_ld, self.webpage_ld(path, page_title, description)]
        return self.layout(path, page_title, description, body, ld)

    def webpage_ld(self, path, title, description):
        return {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title,
            "description": description,
            "url": SITE + path,
            "inLanguage": "ru",
            "isPartOf": {"@type": "WebSite", "name": NAME, "url": SITE + "/"},
            "about": {"@type": "City", "name": "Донецк"},
        }

    # --- указатель улиц -----------------------------------------------------------------------

    def streets_hub(self):
        path = "/ulicy"
        crumb_html, crumb_ld = self.crumbs([("/", NAME), (path, "Улицы")])
        order = sorted(self.letters, key=lambda l: (l == "0-9", l))
        letters = "".join(
            f'<a class="pill" href="/ulicy/{letter_slug(l)}">{esc(l)} <small>{len(self.letters[l])}</small></a>' for l in order
        )
        longest = sorted(self.by_slug.values(), key=lambda s: -s["meters"])[:30]
        top = "".join(f'<a class="pill" href="/ulica/{s["slug"]}">{esc(s["title"])}</a>' for s in longest)
        total = len(self.by_slug)
        title = "Улицы Донецка: есть ли свет, список по алфавиту"
        description = f"Все {total} улиц Донецка по алфавиту: у каждой статус света, возраст последней отметки и телефон диспетчера."
        body = f"""    {crumb_html}
    <h1>Улицы Донецка</h1>
    <p>{total} {plural(total, 'улица', 'улицы', 'улиц')}, переулков, проспектов и площадей. Выберите букву, откройте улицу и посмотрите, есть ли на ней свет сейчас.</p>
    <section class="block"><h2>По алфавиту</h2><div class="pills">{letters}</div></section>
    <section class="block"><h2>Самые длинные улицы</h2><div class="pills">{top}</div></section>"""
        return self.layout(path, title, description, body, [crumb_ld, self.webpage_ld(path, title, description)])

    def letter_page(self, slug):
        letter = next((l for l in self.letters if letter_slug(l) == slug), None)
        if not letter:
            return None
        path = f"/ulicy/{slug}"
        streets = self.letters[letter]
        crumb_html, crumb_ld = self.crumbs([("/", NAME), ("/ulicy", "Улицы"), (path, f"На {letter}")])
        links = "".join(f'<li><a href="/ulica/{s["slug"]}">{esc(s["title"])}</a></li>' for s in streets)
        label = "на цифру" if letter == "0-9" else f"на букву «{letter}»"
        title = f"Улицы Донецка {label}: свет по каждой улице"
        description = f"{len(streets)} {plural(len(streets), 'улица', 'улицы', 'улиц')} Донецка {label}: открывайте улицу, чтобы увидеть, есть ли на ней свет."
        body = f"""    {crumb_html}
    <h1>Улицы Донецка {esc(label)}</h1>
    <p>{len(streets)} {plural(len(streets), 'улица', 'улицы', 'улиц')}. <a href="/ulicy">Все буквы</a>.</p>
    <ul class="streets">{links}</ul>"""
        return self.layout(path, title, description, body, [crumb_ld, self.webpage_ld(path, title, description)])

    # --- районы -------------------------------------------------------------------------------

    def districts_hub(self):
        path = "/rayony"
        crumb_html, crumb_ld = self.crumbs([("/", NAME), (path, "Районы")])
        cards = "".join(
            f'<a class="pill" href="/rayon/{slug}">{esc(d["name"])} район</a>' for slug, d in self.districts.items()
        )
        title = "Районы Донецка: свет, отключения и телефоны диспетчеров"
        description = "Девять районов Донецка: Буденновский, Ворошиловский, Калининский, Киевский, Кировский, Куйбышевский, Ленинский, Петровский, Пролетарский. Телефоны диспетчеров и группы."
        body = f"""    {crumb_html}
    <h1>Районы Донецка</h1>
    <p>В каждом районе свой диспетчер района электросетей Донецкэнерго. Выберите район, чтобы увидеть телефоны, канал района и карту.</p>
    <div class="pills">{cards}</div>
    <p><a href="/telefony">Все телефоны одной страницей</a>.</p>"""
        return self.layout(path, title, description, body, [crumb_ld, self.webpage_ld(path, title, description)])

    def district_page(self, slug):
        d = self.districts.get(slug)
        if not d:
            return None
        path = f"/rayon/{slug}"
        name = d["name"]
        crumb_html, crumb_ld = self.crumbs([("/", NAME), ("/rayony", "Районы"), (path, f"{name} район")])
        phones = " ".join(f'<a href="tel:{re.sub(r"[^+0-9]", "", p)}">{esc(p)}</a>' for p in d["phones"])
        city = self.contacts["city"]
        city_phones = " ".join(f'<a href="tel:{re.sub(r"[^+0-9]", "", p)}">{esc(p)}</a>' for p in city["phones"])
        channel = f'<a href="https://t.me/{esc(d["channel"])}" rel="nofollow noopener">t.me/{esc(d["channel"])}</a>'
        others = "".join(
            f'<a class="pill" href="/rayon/{s}">{esc(o["name"])}</a>' for s, o in self.districts.items() if s != slug
        )
        extra = ""
        if name == "Киевский":
            extra = self.kyiv_section()
        title = f"Свет в {prepositional(name)} районе Донецка: отключения, телефон"
        description = (
            f"Свет в {prepositional(name)} районе Донецка: телефоны диспетчера района электросетей, канал района и карта "
            f"с отметками жителей по улицам. Что делать, если нет света."
        )
        body = f"""    {crumb_html}
    <h1>Свет в {esc(prepositional(name))} районе Донецка</h1>
    <p>Нет света в {esc(prepositional(name))} районе? Позвоните диспетчеру района электросетей Донецкэнерго, назовите улицу и номер дома, потом отметьте на карте, что света нет: так сосед увидит это, не набирая номер.</p>
    <p>{self.map_link(None, 'Открыть карту света')}</p>
    <section class="block">
      <h2>Телефоны диспетчера {esc(genitive(name))} района</h2>
      <p class="phones">{phones}</p>
      <p>Информационно-консультационный центр Донецкэнерго на весь город: <span class="phones">{city_phones}</span></p>
      <p>Сверено с сайтом Донецкэнерго {esc(self.contacts['checked'])}. Телефоны меняются, на сайте <a href="{esc(self.contacts['source'])}" rel="nofollow noopener">uzesk-dnr.ru</a> актуальный список.</p>
    </section>
    <section class="block">
      <h2>Канал района</h2>
      <p>Новости и ответы про свет в {esc(prepositional(name))} районе: {channel}.</p>
    </section>
    {extra}
    <section class="block">
      <h2>Другие районы</h2>
      <div class="pills">{others}</div>
    </section>"""
        return self.layout(path, title, description, body, [crumb_ld, self.webpage_ld(path, title, description)])

    def kyiv_section(self):
        q = self.queues
        sched = self.schedule_state()
        until = ".".join(reversed(q["publishedUntil"].split("-")))
        if sched["expired"]:
            note = f"График подачи света для Киевского района действовал до {until}. Новой карточки пока нет, поэтому по графику улицы не закрашиваются."
        else:
            note = f"График подачи света для Киевского района объявлен до {until}."
        return f"""<section class="block">
      <h2>График подачи света в Киевском районе</h2>
      <p>{esc(note)} Подробно: <a href="/grafik">график отключений света в Донецке</a>.</p>
    </section>"""

    # --- график -------------------------------------------------------------------------------

    def schedule_page(self):
        path = "/grafik"
        q = self.queues
        sched = self.schedule_state()
        until = ".".join(reversed(q["publishedUntil"].split("-")))
        crumb_html, crumb_ld = self.crumbs([("/", NAME), (path, "График отключений")])
        slots = [f"{a}:00–{b}:00" for a, b in q["slots"]]
        rows = []
        for i, keys in enumerate(q["blocks"]):
            cells = []
            for s in range(len(slots)):
                on = (s - sched["shift"]) % 4 == i
                cells.append(f'<td class="{"on" if on else "off"}">{"свет" if on else "нет"}</td>')
            rows.append(f"<tr><th>Очередь {i + 1}</th>{''.join(cells)}</tr>")
        head = "".join(f"<th>{esc(s)}</th>" for s in slots)
        lists = []
        for i, keys in enumerate(q["blocks"]):
            names = "".join(
                f'<li><a href="/ulica/{self.by_key[k]["slug"]}">{esc(self.by_key[k]["title"])}</a></li>'
                for k in keys
                if k in self.by_key
            )
            lists.append(f"<h3>Очередь {i + 1}</h3><ul class=\"streets\">{names}</ul>")
        if sched["expired"]:
            state = f"График Киевского района действовал до {until}, новой карточки Донецкэнерго пока нет. Как только она выйдет, страница обновится."
        else:
            state = f"График для Киевского района объявлен до {until}. Очереди сменяют друг друга каждые два дня."
        title = "График отключений света в Донецке: Киевский район, очереди"
        description = f"График отключений света в Донецке по очередям и часам: Киевский район, улицы каждой очереди. {state}"
        source = q.get("sourceUrl")
        source_line = f'<p class="note">Источник графика: <a href="{esc(source)}" rel="nofollow noopener">публикация</a>.</p>' if source else ""
        body = f"""    {crumb_html}
    <h1>График отключений света в Донецке</h1>
    <p>{esc(state)} Для остальных районов в карту график пока не загружен: смотрите отметки на карте и <a href="/rayony">телефоны диспетчеров районов</a>.</p>
    <p>{self.map_link(None, 'Открыть карту света')}</p>
    <section class="block">
      <h2>Сегодня по очередям</h2>
      <table class="grid"><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>
      <p class="note">«Свет» значит, что очередь по графику со светом в этот промежуток. Время московское.</p>
      {source_line}
    </section>
    <section class="block">
      <h2>Улицы по очередям</h2>
      {''.join(lists)}
    </section>"""
        return self.layout(path, title, description, body, [crumb_ld, self.webpage_ld(path, title, description)])

    # --- телефоны -----------------------------------------------------------------------------

    def phones_page(self):
        path = "/telefony"
        c = self.contacts
        crumb_html, crumb_ld = self.crumbs([("/", NAME), (path, "Куда звонить")])
        city_phones = " ".join(f'<a href="tel:{re.sub(r"[^+0-9]", "", p)}">{esc(p)}</a>' for p in c["city"]["phones"])
        rows = "".join(
            f'<tr><th><a href="/rayon/{slug}">{esc(d["name"])}</a></th><td class="phones">'
            + " ".join(f'<a href="tel:{re.sub(r"[^+0-9]", "", p)}">{esc(p)}</a>' for p in d["phones"])
            + "</td></tr>"
            for slug, d in self.districts.items()
        )
        links = "".join(
            f'<li><a href="{esc(l["url"])}" rel="nofollow noopener">{esc(l["title"])}</a></li>' for l in c["city"]["links"]
        )
        title = "Куда звонить, если нет света в Донецке: телефоны Донецкэнерго"
        description = "Телефоны информационного центра и диспетчеров районов электросетей Донецкэнерго: Буденновский, Ворошиловский, Калининский, Киевский, Кировский, Куйбышевский, Ленинский, Петровский, Пролетарский районы."
        faq = [
            ("Куда звонить, если в Донецке нет света?", f"В информационно-консультационный центр Донецкэнерго: {', '.join(c['city']['phones'])}. Если знаете свой район, звоните диспетчеру района электросетей, телефоны по районам есть на этой странице."),
            ("Что сказать диспетчеру?", "Назовите улицу, номер дома и с какого времени нет света, уточните, нет ли света только у вас или у соседей. После звонка отметьте на карте, что света нет."),
            ("Это официальный сайт Донецкэнерго?", "Нет. Карта света независимый проект, телефоны взяты с сайта Донецкэнерго и сверены " + c["checked"] + "."),
        ]
        faq_ld = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq
            ],
        }
        faq_html = "".join(f"<h3>{esc(q)}</h3><p>{esc(a)}</p>" for q, a in faq)
        body = f"""    {crumb_html}
    <h1>Куда звонить, если нет света в Донецке</h1>
    <p>{esc(c['hint'])}</p>
    <section class="block">
      <h2>{esc(c['city']['title'])}</h2>
      <p class="phones">{city_phones}</p>
      <ul>{links}</ul>
    </section>
    <section class="block">
      <h2>Диспетчеры районов электросетей</h2>
      <table class="grid"><tbody>{rows}</tbody></table>
      <p class="note">Сверено с <a href="{esc(c['source'])}" rel="nofollow noopener">сайтом Донецкэнерго</a> {esc(c['checked'])}.</p>
    </section>
    <section class="block"><h2>Частые вопросы</h2>{faq_html}</section>
    <p>{self.map_link(None, 'Отметить свет на карте')}</p>"""
        return self.layout(path, title, description, body, [crumb_ld, faq_ld, self.webpage_ld(path, title, description)])

    # --- sitemap ------------------------------------------------------------------------------

    def urls(self):
        out = ["/", "/about", "/rayony", "/ulicy", "/grafik", "/telefony"]
        out += [f"/rayon/{slug}" for slug in self.districts]
        out += [f"/ulicy/{letter_slug(l)}" for l in sorted(self.letters)]
        out += [f"/ulica/{slug}" for slug in self.by_slug]
        return out

    def sitemap(self):
        rows = "".join(f"<url><loc>{esc(SITE + p)}</loc><lastmod>{self.stamp}</lastmod></url>" for p in self.urls())
        return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'

    # --- маршрутизация ------------------------------------------------------------------------

    def route(self, path):
        """Возвращает (status, body, content_type) или None, если путь не наш."""
        path = path.rstrip("/") or "/"
        html_type = "text/html; charset=utf-8"
        if path == "/sitemap.xml":
            return 200, self.sitemap(), "application/xml; charset=utf-8"
        page = None
        if path == "/rayony":
            page = self.districts_hub()
        elif path == "/ulicy":
            page = self.streets_hub()
        elif path == "/grafik":
            page = self.schedule_page()
        elif path == "/telefony":
            page = self.phones_page()
        elif path.startswith("/rayon/"):
            page = self.district_page(path[len("/rayon/"):])
        elif path.startswith("/ulicy/"):
            page = self.letter_page(path[len("/ulicy/"):])
        elif path.startswith("/ulica/"):
            page = self.street_page(path[len("/ulica/"):])
        else:
            return None
        if page is None:
            return 404, "<!DOCTYPE html><meta charset=utf-8><title>Не найдено</title><p>Такой страницы нет. <a href=\"/\">Карта света</a></p>", html_type
        return 200, page, html_type
