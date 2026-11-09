// The app's only script. Every page works without it; it only adds what a form cannot do by itself:
// - the calendar to click the days in, which writes them in the field as dd/mm/yyyy (the field can still be typed in);
// - the language chosen at the top is used at once, and the button that turns the pages dark or light says what they show;
// - the ↔ that swaps where from and where to, how many advanced settings differ from settings.py while they are
//   changed, and whether the trip is one way or there and back as its days are written (the page says both too);
// - the part of the page of a running search is asked for again every few seconds and put in place, so that the page
//   does not jump to its top as a refresh would. That part is made by the app, its texts escaped there.
// Nothing here writes markup from text: the calendar is built element by element.
"use strict";

const POLL_MS = 2000;   // how often the part of a running search is asked for again
// A map asked for from Discover's form opens by itself once its page is ready, once: the form leaves a note when it is
// sent, and the first page or part that has the map's link takes it. In this tab: a new one, opened without a click,
// would be blocked. The link stays on the page for later. Without storage, the link is there to click
const MAP_NOTE = "open-map";

function two(number) {
  return String(number).padStart(2, "0");
}

function written(day) {
  return `${two(day.getDate())}/${two(day.getMonth() + 1)}/${day.getFullYear()}`;
}

function read(text) {
  // The days of a field written dd/mm/yyyy, and what else is written there ("Friday", "17-19/12"), kept as it is
  const days = new Set(), others = [];
  for (const entry of text.split(/[,;]/).map((part) => part.trim()).filter(Boolean)) {
    const match = entry.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (match) days.add(`${match[3]}-${two(match[2])}-${two(match[1])}`);
    else others.push(entry);
  }
  return { days, others };
}

function key(day) {
  return `${day.getFullYear()}-${two(day.getMonth() + 1)}-${two(day.getDate())}`;
}

function element(tag, text, attributes = {}) {
  const made = document.createElement(tag);
  if (text !== undefined) made.textContent = text;
  for (const [name, value] of Object.entries(attributes)) made.setAttribute(name, value);
  return made;
}

function calendar(field, form) {
  // A month of days under the field; a click ticks a day or unticks it. The way back cannot start before the way there
  const months = form.dataset.months.split(","), weekdays = form.dataset.weekdays.split(",");
  const most = Number(form.dataset.mostDates), ahead = Number(form.dataset.todayLimit);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const last = new Date(today); last.setDate(last.getDate() + ahead);
  let first = today;
  if (field.name === "return_dates") {
    const going = [...read(form.elements.dates.value).days].sort()[0];
    if (going) first = new Date(`${going}T00:00:00`);
  }
  const chosen = read(field.value);
  const start = [...chosen.days].sort()[0];
  let month = start ? new Date(`${start}T00:00:00`) : new Date(first);
  month.setDate(1);
  const box = element("div", undefined, { class: "calendar", role: "dialog" });
  // A click inside is not a click outside, even on a button the month drawn again has just taken away
  box.addEventListener("click", (event) => event.stopPropagation());

  function write() {
    const days = [...chosen.days].sort().map((day) => written(new Date(`${day}T00:00:00`)));
    field.value = [...days, ...chosen.others].join(", ");
    field.dispatchEvent(new Event("input", { bubbles: true }));   // as if typed: the kind of trip follows it
  }

  function draw() {
    box.replaceChildren();
    const header = element("div", undefined, { class: "month" });
    const back = element("button", "<", { type: "button" }), next = element("button", ">", { type: "button" });
    back.disabled = month <= new Date(first.getFullYear(), first.getMonth(), 1);
    back.addEventListener("click", () => { month.setMonth(month.getMonth() - 1); draw(); });
    next.addEventListener("click", () => { month.setMonth(month.getMonth() + 1); draw(); });
    header.append(back, element("span", `${months[month.getMonth()]} ${month.getFullYear()}`), next);
    const table = element("table"), head = element("tr");
    weekdays.forEach((name) => head.append(element("th", name)));
    table.append(head);
    let row = element("tr");
    const blanks = (month.getDay() + 6) % 7;   // the week starts on Monday
    for (let blank = 0; blank < blanks; blank++) row.append(element("td"));
    const day = new Date(month);
    while (day.getMonth() === month.getMonth()) {
      const cell = element("td"), button = element("button", String(day.getDate()), { type: "button" });
      const id = key(day);
      button.disabled = day < first || day > last;
      if (chosen.days.has(id)) button.classList.add("chosen");
      button.addEventListener("click", () => {
        if (chosen.days.has(id)) chosen.days.delete(id);
        else if (chosen.days.size < most) chosen.days.add(id);
        write();
        draw();
      });
      cell.append(button);
      row.append(cell);
      if (row.children.length === 7) { table.append(row); row = element("tr"); }
      day.setDate(day.getDate() + 1);
    }
    if (row.children.length) table.append(row);
    const clear = element("button", form.dataset.clear, { type: "button", class: "quiet" });
    clear.addEventListener("click", () => { chosen.days.clear(); write(); draw(); });
    box.append(header, table, clear, element("small", field.name === "return_dates" ? form.dataset.returnHint : form.dataset.hint));
  }
  draw();
  return box;
}

function calendars() {
  for (const field of document.querySelectorAll("input[data-calendar]")) {
    // The browser's list of dates typed before would cover the calendar.
    field.autocomplete = "off";
    let open = null;
    const close = () => { if (open) { open.remove(); open = null; } };
    const show = () => {
      if (open) return;
      for (const other of document.querySelectorAll(".calendar")) other.remove();
      open = calendar(field, field.form);
      field.after(open);
    };
    field.addEventListener("focus", show);   // reached with the keyboard
    field.addEventListener("click", show);   // clicked while it already had the focus
    document.addEventListener("click", (event) => {
      if (open && event.target !== field && !open.contains(event.target)) close();
    });
    field.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  }
}

function swap() {
  for (const button of document.querySelectorAll("button[data-swap]")) {
    const fields = button.form.elements;
    button.hidden = false;
    button.addEventListener("click", () => {
      [fields.origin.value, fields.destination.value] = [fields.destination.value, fields.origin.value];
    });
  }
}

function changes() {
  // The summary of the advanced settings says how many differ from settings.py, each field holding its value there
  for (const advanced of document.querySelectorAll("details.advanced")) {
    const summary = advanced.querySelector("summary");
    const count = () => {
      const changed = [...advanced.querySelectorAll("[data-default]")].filter((field) =>
        (field.type === "checkbox" ? (field.checked ? "on" : "") : field.value) !== field.dataset.default).length;
      summary.textContent = changed ? summary.dataset.changed.replace("{count}", changed) : summary.dataset.none;
    };
    advanced.addEventListener("input", count);
    advanced.addEventListener("change", count);
  }
}

function tripKind() {
  // There and back once the days to come back are written, and only while "round trip" is chosen: one way hides them.
  // Choosing "round trip" opens their calendar
  for (const kind of document.querySelectorAll(".trip-kind")) {
    const form = kind.closest("form"), back = form.elements.return_dates;
    const choices = [...form.querySelectorAll("input[name=trip_kind]")];
    const round = () => !choices.length || choices.some((choice) => choice.checked && choice.value === "round");
    const say = () => { kind.textContent = round() && back.value.trim() ? kind.dataset.round : kind.dataset.one; };
    back.addEventListener("input", say);
    for (const choice of choices) {
      choice.addEventListener("change", () => { say(); if (round()) back.focus(); });
    }
  }
}

function theme() {
  // The button of the header turns the pages dark or light: the app is told what they show now, and where to come back
  for (const form of document.querySelectorAll("form[data-theme-form]")) {
    form.addEventListener("submit", () => {
      form.elements.shown.value = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      form.elements.back.value = location.pathname + location.search;
    });
  }
}

function mapAsked() {
  for (const form of document.querySelectorAll('form[action="/discover"]')) {
    form.addEventListener("submit", () => { try { sessionStorage.setItem(MAP_NOTE, "1"); } catch (error) { /* no storage */ } });
  }
}

function openMap() {
  const link = document.querySelector("a[data-open-map]");
  if (!link) return;
  let asked = false;
  try { asked = sessionStorage.getItem(MAP_NOTE) === "1"; sessionStorage.removeItem(MAP_NOTE); } catch (error) { /* no storage */ }
  if (asked) location.assign(link.href);
}

function language() {
  for (const select of document.querySelectorAll("select[data-submit]")) {
    select.addEventListener("change", () => select.form.submit());
  }
}

function poll() {
  // The part of a running search, again, until it is no longer working; then the page stays as it is
  const live = document.getElementById("live");
  if (!live || !live.dataset.working) return;
  setTimeout(async () => {
    try {
      const answer = await fetch(live.dataset.part, { cache: "no-store" });   // the search's part, or the map's
      if (answer.ok && answer.headers.get("Content-Type").startsWith("text/html")) {
        const text = await answer.text();
        const now = document.getElementById("live");
        if (now && now.outerHTML !== text) {
          const part = new DOMParser().parseFromString(text, "text/html").getElementById("live");
          if (part) {
            const opened = now.querySelector("details"), opening = part.querySelector("details");
            if (opened && opening) opening.open = opened.open;   // as the traveller left it
            now.replaceWith(document.adoptNode(part));
            openMap();
          }
        }
      }
    } catch (error) {
      // the app was stopped, or is busy: ask again
    }
    poll();
  }, POLL_MS);
}

// A page the browser kept for the back button shows what was true when it was left (a search not started yet, a
// form that can be sent): ask for it again instead
window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });

calendars();
swap();
changes();
tripKind();
theme();
mapAsked();
openMap();
language();
poll();
