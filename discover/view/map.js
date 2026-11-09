// The map page's script (discover/view/page.py builds the page around it): the map, the list, the trip of a destination,
// the filters, and, in the app, the destinations asked for again while the timetables are looked up.
// Every text is put in the page as text, never as markup.
// What the page shows, put in it by page.py as JSON: data, never code
const data = JSON.parse(document.getElementById("data").textContent);
let state = data.state;
let picked = null;   // the destination whose trip is open, by its airport
const colours = ["#2e9e4f", "#8fbf3f", "#e6b422", "#e67e22", "#d0453a"];
// A text of the page in the traveller's language (texts/languages, discover_page_...), its {values} filled in
const t = (key, values) => (data.texts[key] === undefined ? key : data.texts[key]).replace(/\{(\w+)\}/g, (all, name) => (values && name in values ? values[name] : all));
const modeName = mode => (data.texts["mode_" + mode] === undefined ? mode : data.texts["mode_" + mode]);
const eur = value => value.toFixed(0) + " €";
// A tooltip's text, as text: Leaflet would read a string as markup, and the names come from Wikidata and OurAirports
const tip = text => { const span = document.createElement("span"); span.textContent = text; return span; };
const add = (parent, tag, text, kind) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (kind) element.className = kind; parent.append(element); return element; };
const place = code => (state.names[code] || code) + " (" + code + ")";
const day = text => new Date(text + "T12:00:00").toLocaleDateString(data.locale, {weekday: "short", day: "numeric", month: "short"});
// The weekdays of a timetable (ground.py writes them Mon to Sun) in the page's language, from the 16th of November
// 2026, a Monday
const weekdayNames = Object.fromEntries(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((code, i) =>
  [code, new Date(2026, 10, 16 + i, 12).toLocaleDateString(data.locale, {weekday: "short"})]));
const weekdays = codes => codes.map(code => weekdayNames[code] || code).join(", ");
const when = (date, time) => t("when", {time: time, day: day(date)});
const hours = minutes => (minutes >= 1440 ? Math.floor(minutes / 1440) + "d " : "") + Math.floor(minutes % 1440 / 60) + "h" + String(minutes % 60).padStart(2, "0");
// The time in the city of a return trip, as 48h (2 nights); "about" when a way in or out was not looked up
const stayText = s => {
  if (!s) return "";
  const text = t(s.nights === 1 ? "stay_one" : "stay_many", {hours: Math.round(s.minutes / 60), nights: s.nights});
  return s.estimated ? t("about", {text: text}) : text;
};
// " · also by train" or "by bus" or both, when a train or bus goes there with no flight (ground.py)
const groundText = p => {
  if (!p.ground || !p.ground.length) return "";
  const priced = p.ground.filter(option => option.price && option.price.total_eur !== null).sort((a, b) => a.price.total_eur - b.price.total_eur);
  return " · " + t("also_by", {what: priced.length ? modeName(priced[0].mode) + " " + eur(priced[0].price.total_eur) + (priced[0].price.stay ? ", " + stayText(priced[0].price.stay) : "")
                                                    : [...new Set(p.ground.map(option => option.mode))].sort().reverse().map(modeName).join(t("or"))});
};
// The price of a city with no flight (ground_fares.py), null while none of its trains or buses is priced
const fareOf = g => (g.fare_eur === undefined ? null : g.fare_eur);
// The trains and buses of a city, the ones with a price first, cheapest first; then the others, as they came (quickest first)
const totalOf = option => (option.price && option.price.total_eur !== null ? option.price.total_eur : null);
const byPrice = options => [...options].sort((a, b) => (totalOf(a) === null) - (totalOf(b) === null) || (totalOf(a) || 0) - (totalOf(b) || 0));
// The cheapest train or bus of a city priced in full, or null
const cheapestGround = options => byPrice(options || []).find(option => totalOf(option) !== null) || null;
// A city's price on the map: the cheapest way there, by air or by train or bus, as far as it is known
const cityPrice = p => Math.min(p.total_eur, ...(cheapestGround(p.ground) ? [totalOf(cheapestGround(p.ground))] : []));
const between = (date1, time1, date2, time2) => Math.round((new Date(date2 + "T" + time2) - new Date(date1 + "T" + time1)) / 60000);
const before = (date, time, minutes) => { const moment = new Date(new Date(date + "T" + time) - minutes * 60000);
  const two = number => String(number).padStart(2, "0");
  return [moment.getFullYear() + "-" + two(moment.getMonth() + 1) + "-" + two(moment.getDate()), two(moment.getHours()) + ":" + two(moment.getMinutes())]; };
const worst = legs => legs.reduce((grade, leg) => [grade, leg.grade, leg.price_grade].sort().pop(), "A");
const fromTo = (start, end) => t("from_to", {from: start, to: end});

document.documentElement.lang = data.locale;
document.getElementById("title").textContent = t(data.round_trip ? "title_return" : "title_one_way", {origin: data.origin.name});
document.getElementById("by-ground-title").textContent = t("by_ground_title");
document.getElementById("by-ground-note").textContent = t("by_ground_note");
document.getElementById("searched-title").textContent = t("searched_title");
const legend = document.getElementById("legend");
colours.forEach((colour, i) => { const dot = document.createElement("span"); dot.style.background = colour; legend.append(dot, i === 0 ? t("legend_cheapest") : i === colours.length - 1 ? t("legend_dearest") : ""); });
legend.append(" · " + t("legend_ring"));
const GROUND_COLOUR = "#7a4fd6";   // a city reached by train or bus only: no price, so none of the price colours
if ((state.by_ground || []).length) {
  const ring = document.createElement("span"); ring.style.border = "2px solid " + GROUND_COLOUR; ring.style.width = ring.style.height = "6px";
  legend.append(ring, t("legend_ground"));
}
const map = L.map("map").setView([data.origin.lat, data.origin.lon], 4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 12, attribution: "© OpenStreetMap"}).addTo(map)
  .once("tileerror", () => { const note = document.getElementById("tiles"); note.hidden = false; note.textContent = t("tiles_failed"); });
L.circleMarker([data.origin.lat, data.origin.lon], {radius: 8, color: "#1f6fd0", fillOpacity: 1}).addTo(map).bindTooltip(tip(data.origin.name));
const markers = L.layerGroup().addTo(map);
let line = null;

// An icon of the app's sprite (trip_planner/report/icons.py, in the page), as the app draws it
function iconOf(parent, name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", "icon icon-" + name); svg.setAttribute("aria-hidden", "true");
  svgAdd(svg, "use", {href: "#i-" + name});
  parent.append(svg);
  return svg;
}
// A grade as the planner says it: its coloured dot and what it means
function gradeText(parent, grade) {
  add(parent, "span", "●").style.color = data.grade_colours[grade];
  parent.append(" " + t({A: "grade_a", B: "grade_b"}[grade] || "grade_c"));
}
const outward = url => Boolean(url) && url.startsWith("https://");
function sourceLink(parent, url, text) {
  const anchor = add(parent, "a", text);
  anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
}

// A way to or from an airport inside its step, as the planner shows a ground leg (app/results.py): its vehicles one
// by one with the changes between them, how sure its times and its price are, where they come from, and the other
// ways published from that airport, closed
function describeWay(box, way) {
  const vehicles = way.vehicles || [];
  if (vehicles.length) {
    const list = add(box, "ul", undefined, "vehicles");
    vehicles.forEach((vehicle, i) => {
      if (i) add(list, "li", t("vehicle_change", {place: vehicles[i - 1].to || vehicle.from,
                                                  wait: hours((minutesOf(vehicle.time) - minutesOf(vehicles[i - 1].arrive) + 1440) % 1440)}), "change");
      const row = add(list, "li");
      iconOf(row, ["train", "bus", "tram", "ferry", "walk"].includes(vehicle.kind) ? vehicle.kind : "route");
      const text = add(row, "span");
      add(text, "b", vehicle.time);
      text.append(" " + [vehicle.line, vehicle.from && vehicle.to ? vehicle.from + " → " + vehicle.to : ""].filter(Boolean).join(" ")
                  + " (" + vehicle.arrive + ")");
    });
  }
  if (way.price_eur === undefined) return;   // nothing known of it: a map made before the ways were kept
  const facts = add(box, "div", undefined, "facts");
  gradeText(add(facts, "span", t("times") + ": "), way.grade);
  gradeText(add(facts, "span", t("price", {price: eur(way.price_eur)}) + ": "), way.price_grade);
  const said = way.how || "";
  if (said || outward(way.url)) {
    const how = add(box, "p", said ? (way.looked_up ? t("looked_up_for_flight", {how: said}) : said.charAt(0).toUpperCase() + said.slice(1)) + " " : "", "muted");
    if (outward(way.url)) sourceLink(how, way.url, t("source_link"));
  }
  if (way.quote) add(box, "p", "“" + way.quote + "”", "muted quote");
  // Every published way from that airport, so the traveller sees the choice and where each price was read
  if (way.options && way.options.length > 1) {
    const more = add(box, "details", undefined, "others");
    add(more, "summary", t("other_ways", {count: way.options.length}));
    const others = add(more, "ul");
    for (const option of way.options) {
      const row = add(others, "li", t("published_option", {what: [option.operator, option.mode ? modeName(option.mode) : ""].filter(Boolean).join(" "),
                                                           to: option.to ? t("to_place", {place: option.to}) : "", from: option.from_price ? t("from_price") : "",
                                                           price_text: option.price_text, price: eur(option.price_eur), source: option.source}) + " ");
      if (outward(option.url)) sourceLink(row, option.url, t("source_link_small"));
    }
  }
}

// A vertical line of steps, as the planner's option (app/results.py option_steps): step(at, mark, grade) adds a place
// (a dot) or a ride (its icon, the line in the colour of its grade), and gives the box for its words
function stepsIn(parent) {
  const steps = add(parent, "ol", undefined, "steps");
  return (at, mark, grade) => {
    const item = add(steps, "li");
    if (grade) { item.className = "ride"; item.style.setProperty("--ride", data.grade_colours[grade]); }
    add(item, "span", at, "at");
    const rail = add(item, "span", undefined, "rail");
    if (mark) iconOf(rail, mark); else add(rail, "b");
    return add(item, "div", undefined, "what");
  };
}

// One way of the trip from top to bottom, as the planner's option (app/results.py option_steps): leaving the city,
// the way to the airport, the airport, each flight with its stopovers, the landing, the way into the city and the
// arrival. What the traveller should know about a connection before booking it goes into warnings. toAirport and
// fromAirport are the ways to and from its airports: once looked up in the timetables, the warnings about the hour
// are theirs to give, not the map's. A time worked out from the flight, not looked up, is written with "~".
function describeLeg(parent, title, leg, warnings, toAirport, fromAirport, start, end) {
  add(parent, "h4", title + " — " + leg.airline + ", " + (leg.stops ? t(leg.stops > 1 ? "stops" : "stop", {count: leg.stops}) : t("direct")));
  const [date, time] = before(leg.date, leg.time, data.airport_minutes);
  if (!(toAirport && toAirport.looked_up) && (date !== leg.date || Number(time.slice(0, 2)) < data.early_hour))
    warnings.push(t("warn_early", {airport: place(leg.from), when: when(date, time)}));
  const landing = Number((leg.arrival_time || "12").slice(0, 2));
  if (!(fromAirport && fromAirport.looked_up) && leg.arrival_time && (landing >= data.late_hour || landing < data.early_hour))
    warnings.push(t("warn_late", {airport: place(leg.to), when: when(leg.arrival_date, leg.arrival_time)}));
  const times = legTimes(leg, toAirport, fromAirport), to = times.toAirport, from = times.fromAirport;
  const step = stepsIn(parent);
  const wayIcon = way => { const first = (way.vehicles || []).find(vehicle => vehicle.kind !== "walk"); return first ? first.kind : "route"; };
  // A time of a flight, as minutes from midnight of the day the way starts (the line's clock)
  const flightClock = (day, time) => clock(between(leg.date, "00:00", day || leg.date, time));
  add(step(times.guess(to, times.leave)), "p", t("step_leave", {place: start}));
  describeWay(step("", wayIcon(to), to.grade), to);
  add(step(times.guess(to, times.reach)), "p", t("step_at_airport", {airport: place(leg.from), minutes: times.departs - times.reach}));
  leg.segments.forEach((segment, index) => {
    if (index) {
      const previous = leg.segments[index - 1];
      const wait = between(previous.arrival_date, previous.arrival_time, segment.date, segment.time);
      const night = previous.arrival_date !== segment.date;
      add(step(flightClock(previous.arrival_date, previous.arrival_time)), "p",
          t("step_stopover", {airport: place(segment.from), hours: hours(wait), night: night ? t("through_night") : ""})
          + (previous.to !== segment.from ? " " + t("change_other_airport", {airport: place(previous.to)}) : ""));
      if (previous.to !== segment.from)
        warnings.push(t("warn_other_airport", {from: place(previous.to), to: place(segment.from)}));
      if (night || wait >= data.long_wait)
        warnings.push(t("warn_long_wait", {hours: hours(wait), airport: place(segment.from), night: night ? t("through_night") : ""}));
      if ((previous.airline || "") !== (segment.airline || ""))
        warnings.push(t("warn_two_tickets", {first: previous.airline, second: segment.airline, airport: place(segment.from)}));
    }
    const box = step(index ? flightClock(segment.date, segment.time) : clock(times.departs), "plane", "A");
    add(box, "p", t("step_flight", {flight: [segment.airline || leg.airline, segment.flight].filter(Boolean).join(" "), from: place(segment.from),
                                    to: place(segment.to), duration: leg.segments.length === 1 && leg.minutes ? ", " + hours(leg.minutes) : ""}));
    const facts = add(add(box, "div", undefined, "facts"), "span", t("times") + ": ");
    gradeText(facts, "A");
    facts.append(" · " + leg.source);
  });
  add(step(leg.arrival_time ? clock(times.lands) : t("no_time")), "p", t("step_landing", {airport: place(leg.to)}));
  const onward = step(times.guess(from, times.out), wayIcon(from), from.grade);
  add(onward, "p", t("step_onward", {minutes: times.out - times.lands}));
  describeWay(onward, from);
  add(step(times.guess(from, times.arrive)), "p", t("step_arrival", {place: end}));
}

// Times as minutes from midnight of the day the trip starts, and back: a time past midnight says the day it falls on
const minutesOf = time => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
const clock = minutes => { const two = number => String(number).padStart(2, "0"), days = Math.floor(minutes / 1440), rest = minutes - days * 1440;
  return two(Math.floor(rest / 60)) + ":" + two(rest % 60) + (days > 0 ? " (+" + days + "d)" : days < 0 ? " (" + days + "d)" : ""); };
const SVG = "http://www.w3.org/2000/svg";
const svgAdd = (parent, tag, attributes, text) => { const element = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  if (text !== undefined) element.textContent = text; parent.append(element); return element; };

// The vehicles drawn with their icon on the line: the app's icons (trip_planner/report/icons.py), whose sprite is in
// the page, named as icons.mode_of names a vehicle (confirm.vehicles)
const VEHICLE_ICONS = new Set(["train", "bus", "tram", "ferry"]);
const SHORTEST = 78;     // the least width of a part between two points with their time and place: room for the labels
const SHORTEST_RIDE = 34, SHORTEST_WAIT = 12;   // ... of a vehicle and of a change inside a way: room for its icon, and a gap that shows

// An option drawn as the app draws it (app/report_page.py): a straight line from the first point to the last, each
// part as long as its share of the time, in the colour of the grade of its times, a wait as a thin grey line, and
// at each point its time above and its place below. points has one more item than parts. A part may name its
// vehicle's kind as a third item, drawn on the line. A point may be a change of vehicle, [time, place, "change"]: a
// small dot whose time and place are said on hover; a point that is null is a boundary with nothing drawn.
function drawLine(parent, title, points, parts) {
  add(parent, "p", title, "line-title");
  const edge = 6, y = 30;
  const labelled = point => point && point.length < 3;
  const least = parts.map((part, i) => labelled(points[i]) && labelled(points[i + 1]) ? SHORTEST : part[2] ? SHORTEST_RIDE : SHORTEST_WAIT);
  const room = Math.max(760, least.reduce((sum, width) => sum + width, 0) + 2 * edge);
  const svg = svgAdd(add(parent, "div", undefined, "line"), "svg", {viewBox: "0 0 " + room + " 64", role: "img", "aria-label": title});
  const total = parts.reduce((sum, [, minutes]) => sum + Math.max(1, minutes), 0);
  const free = room - 2 * edge - least.reduce((sum, width) => sum + width, 0);
  const xs = [edge];
  parts.forEach(([, minutes], i) => xs.push(xs[i] + least[i] + free * Math.max(1, minutes) / total));
  parts.forEach(([grade], i) => svgAdd(svg, "line", {x1: xs[i], x2: xs[i + 1], y1: y, y2: y, "stroke-width": grade ? 7 : 3,
                                                     stroke: grade ? data.grade_colours[grade] : "var(--line)"}));
  parts.forEach(([, , kind], i) => {
    if (!VEHICLE_ICONS.has(kind) || xs[i + 1] - xs[i] < SHORTEST_RIDE - 4) return;
    const middle = (xs[i] + xs[i + 1]) / 2;
    svgAdd(svg, "circle", {cx: middle, cy: y, r: 11, fill: "var(--bg)"});
    svgAdd(svg, "use", {href: "#i-" + kind, x: middle - 8, y: y - 8, width: 16, height: 16, class: "vehicle",
                    fill: "none", stroke: "var(--fg)", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round"});
  });
  points.forEach((point, i) => {
    if (!point) return;
    const [time, place, change] = point;
    if (change) {   // the changes of a way are too close together for their labels: said on hover
      const dot = svgAdd(svg, "circle", {cx: xs[i], cy: y, r: 3.5, fill: "var(--bg)", stroke: "var(--fg)", "stroke-width": 1.5});
      svgAdd(dot, "title", {}, (time + " " + place).trim());
      return;
    }
    const anchor = i === 0 ? "start" : i === points.length - 1 ? "end" : "middle";
    svgAdd(svg, "circle", {cx: xs[i], cy: y, r: 5, fill: "var(--bg)", stroke: "var(--fg)", "stroke-width": 2});
    svgAdd(svg, "text", {x: xs[i], y: y - 10, "text-anchor": anchor, "font-weight": 700, fill: "var(--fg)"}, time);
    svgAdd(svg, "text", {x: xs[i], y: y + 22, "text-anchor": anchor, fill: "var(--muted)"}, place);
  });
}

// A way to or from an airport on the line, one part per vehicle when its vehicles are known (as the planner's
// results.ride_parts): [the points inside it, its parts]. Each vehicle in the colour of the way's grade with its icon,
// the walks and the waits for the next one grey, a change where one ends and the next starts. A way of one vehicle,
// or whose vehicles' times do not fit in its own, stays one part.
function wayParts(way, minutes) {
  const vehicles = way.vehicles || [], rides = vehicles.filter(vehicle => vehicle.kind !== "walk");
  const whole = [[], [[way.grade, minutes, rides.length ? rides[0].kind : ""]]];
  if (rides.length < 2 || !way.leave) return whole;
  const gap = (first, last) => (minutesOf(last) - minutesOf(first) + 1440) % 1440;
  const segments = [];
  let at = way.leave;
  rides.forEach((ride, i) => {
    const before = gap(at, ride.time);   // a walk to the first stop, or the wait for the next vehicle
    if (before) segments.push([i ? [at, rides[i - 1].to, "change"] : null, [null, before]]);
    segments.push([i ? [ride.time, ride.from, "change"] : null, [way.grade, gap(ride.time, ride.arrive), ride.kind]]);
    at = ride.arrive;
  });
  const after = minutes - segments.reduce((sum, [, part]) => sum + part[1], 0);   // a walk from the last stop
  if (after < 0) return whole;
  if (after) segments.push([null, [null, after]]);
  return [segments.slice(1).map(([point]) => point), segments.map(([, part]) => part)];
}

// One way of a flight as the planner's line: from the city to the airport, the wait there, the flight, the wait
// after landing and the way into the city. A way to or from an airport looked up for this flight has its own times;
// the others are worked out from the flight and the minutes known, and written with "~".
// A way to or from an airport read for this very flight, with its own times
const knownWay = way => way.looked_up && way.leave && way.arrive;

// The times of one way of a trip, as minutes from midnight of its day: leaving the city, at the airport, the take-off,
// the landing, leaving the airport and reaching the city. The line and the steps both say these, so they agree.
function legTimes(leg, toAirport, fromAirport) {
  toAirport = toAirport || {grade: "C", minutes: 0}; fromAirport = fromAirport || {grade: "C", minutes: 0};
  const departs = minutesOf(leg.time), atAirport = departs - data.airport_minutes;
  const flying = leg.minutes || Math.max(0, between(leg.date, leg.time, leg.arrival_date || leg.date, leg.arrival_time || leg.time));
  // The landing in the local time of the airport landed at, as the flight gives it: the way into the city is in that time
  const lands = leg.arrival_time ? minutesOf(leg.arrival_time) + 1440 * Math.round(between(leg.date, "12:00", leg.arrival_date || leg.date, "12:00") / 1440)
                                 : departs + flying;
  const known = knownWay;
  const reach = known(toAirport) ? minutesOf(toAirport.arrive) - (minutesOf(toAirport.arrive) > departs ? 1440 : 0) : atAirport;
  const leave = known(toAirport) ? reach - ((minutesOf(toAirport.arrive) - minutesOf(toAirport.leave) + 1440) % 1440) : atAirport - toAirport.minutes;
  let out = lands + data.landing_minutes;
  if (known(fromAirport)) { out = minutesOf(fromAirport.leave); while (out < lands) out += 1440; }
  const arrive = known(fromAirport) ? out + (minutesOf(fromAirport.arrive) - minutesOf(fromAirport.leave) + 1440) % 1440 : out + fromAirport.minutes;
  const guess = (way, minutes) => (known(way) ? "" : "~") + clock(minutes);
  return {toAirport, fromAirport, departs, flying, lands, reach, leave, out, arrive, guess};
}

function flightLine(parent, title, leg, toAirport, fromAirport, start, end) {
  const {departs, flying, lands, reach, leave, out, arrive, guess} = legTimes(leg, toAirport, fromAirport);
  toAirport = toAirport || {grade: "C", minutes: 0}; fromAirport = fromAirport || {grade: "C", minutes: 0};
  const known = knownWay;
  // A way looked up for this flight is drawn vehicle by vehicle; the others are one part, their vehicles unknown
  const [toPoints, toParts] = known(toAirport) ? wayParts(toAirport, reach - leave) : [[], [[toAirport.grade, reach - leave]]];
  const [fromPoints, fromParts] = known(fromAirport) ? wayParts(fromAirport, arrive - out) : [[], [[fromAirport.grade, arrive - out]]];
  drawLine(parent, title, [[guess(toAirport, leave), start], ...toPoints, [guess(toAirport, reach), leg.from], [clock(departs), "✈"],
                           [clock(lands), leg.to], [guess(fromAirport, out), ""], ...fromPoints, [guess(fromAirport, arrive), end]],
           [...toParts, [null, departs - reach], ["A", flying], [null, out - lands], ...fromParts]);
}

// An operator with its mode, unless its name says it already: "ÖBB (train)", "FlixBus", "BlaBlaCar Bus"
const operatorText = option => option.operator + (option.operator.toLowerCase().includes(option.mode) ? "" : " (" + modeName(option.mode) + ")");

// The flights of a trip drawn to scale, the way there and the way back; the trains and buses are steps only (describeGround)
function describeLines(panel, p) {
  const box = add(panel, "div", undefined, "lines");
  const access = p.access || [];
  flightLine(box, t(p.back ? "flight_out_line" : "flight_line", {airline: p.airline, day: day(p.date), total: eur(p.total_eur)}),
             p, access[0], access[1], data.origin.short, p.name);
  if (p.back) flightLine(box, t("flight_back_line", {airline: p.back.airline, day: day(p.back.date)}), p.back, access[2], access[3], p.name, data.origin.short);
  colourLegend(box);
}

// What the colour of each part of a line means, once per trip
function colourLegend(parent) {
  const legend = add(parent, "p", "", "muted");
  legend.append(t("colours_intro") + " ");
  [["A", t("grade_a")], ["B", t("grade_b")], ["C", t("grade_c")]].forEach(([grade, text]) => {
    const dot = add(legend, "span", "●"); dot.style.color = data.grade_colours[grade]; legend.append(" " + text + " · ");
  });
  legend.append(t("colours_end"));
}

// Where each part of a trip is sold, as the planner's "where to book": the flight, and each way to and from the
// airports that was looked up for these flights and is sold online. A way with no such link says so.
function describeBooking(panel, p) {
  add(panel, "h4", t("where_to_book"));
  const list = add(panel, "ul", undefined, "links");
  const link = (item, text, url) => { const anchor = add(item, "a", text); anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; };
  for (const booking of p.links.filter(link => link.kind === "book" && link.url.startsWith("https://"))) link(add(list, "li"), booking.label, booking.url);
  for (const way of p.access || []) {
    const item = add(list, "li", (way.to_airport ? fromTo(way.place || t("the_city"), place(way.airport)) : fromTo(place(way.airport), way.place || t("the_city"))) + ": ");
    const urls = (way.urls || []).filter(url => url.startsWith("https://"));
    if (urls.length) urls.forEach((url, i) => { if (i) item.append(", "); link(item, url.replace("https://", "").split("/")[0], url); });
    else item.append(way.looked_up ? t("no_seller") : t("not_looked_up_seller"));
  }
}

// The trains and buses that go straight to a city (ground.py), each as a flight is shown (describeLeg): its price in
// its title, then each way from top to bottom, the station left, the ride with how sure its times and its price are,
// and the station reached; where to buy it, or why the map has no price for it
function describeGround(panel, options, key) {
  for (const option of byPrice(options)) {
    const priced = option.price, out = priced && priced.out, back = priced && priced.back;
    const title = add(panel, "h4", operatorText(option) + " ", "ground-title");
    if (totalOf(option) !== null) add(title, "strong", eur(totalOf(option)) + (back ? " " + t("there_and_back") : ""), "price");
    else if (out) add(title, "strong", t("out_only_price", {price: eur(out.price_eur)}), "price");
    else add(title, "span", t("no_price"), "price none");
    if (out) groundWay(panel, option, t("out"), out, option.from_stop, option.stop);
    else groundTimetable(panel, option);
    if (data.round_trip && back) {
      if (priced.stay) add(panel, "p", t("ground_stay_line", {stay: stayText(priced.stay), stop: option.stop}), "muted");
      groundWay(panel, option, t("back"), back, option.stop, option.from_stop);
    } else if (data.round_trip) groundBack(panel, option, out);
    if (out) continue;
    const why = add(panel, "p", (priced ? t("not_sold_by", {operator: option.operator}) + " " : option.sold ? t("price_not_asked") + " " : "")
                    + t("buy_it_on", {grade: option.grade}) + " ", "muted");
    const site = add(why, "a", (option.site || "").replace("https://", "").split("/")[0] || t("operator_site", {operator: option.operator}));
    if (outward(option.site)) { site.href = option.site; site.target = "_blank"; site.rel = "noopener noreferrer"; }
    why.append(t("searching_on_day", {from: option.from_stop, to: option.stop}));
  }
  if ((state.asked_prices || {})[key] === "none" && !options.some(option => option.price && option.price.out))
    add(panel, "p", t("price_asked_none"), "muted");
}

// One way sold for a day (ground_fares.py, grade A): leaving the station, the ride, the station reached
function groundWay(panel, option, title, leg, from, to) {
  add(panel, "h5", title + ", " + day(leg.day));
  const step = stepsIn(panel);
  const leaves = minutesOf(leg.leave), arrives = minutesOf(leg.arrive) + 1440 * Math.round(between(leg.day, "12:00", leg.arrive_day, "12:00") / 1440);
  // FlixBus asked city to city sells from its own stations, maybe not the timetable's (ground_fares.flixbus_town_day)
  const leaving = leg.from_stop || from, reaching = leg.to_stop || to;
  add(step(clock(leaves)), "p", t("step_leave", {place: leaving}));
  const ride = step("", option.mode, leg.grade);
  add(ride, "p", t("ground_ride", {what: operatorText(option), hours: hours(arrives - leaves)}));
  // A trip sold with changes of coach (TripSettings.max_coach_changes): where, and how long the wait
  for (const change of leg.changes || []) add(ride, "p", t("ground_change", {place: change.at || "?", wait: hours(change.wait)}), "warn");
  const price = add(ride, "p", undefined, "ground-price");
  add(price, "strong", eur(leg.price_eur));
  price.append(" " + t("ground_price_sold", {seller: leg.seller}) + " ");
  if (outward(leg.url)) sourceLink(price, leg.url, t("buy"));
  gradeText(add(add(ride, "div", undefined, "facts"), "span", t("times") + ": "), leg.grade);
  add(step(clock(arrives)), "p", t("step_arrival", {place: reaching}));
}

// The way there with no price: its first departure of the week in the timetables (grade B), and the others
function groundTimetable(panel, option) {
  add(panel, "h5", (data.round_trip ? t("out") + ", " : "") + t("ground_days", {weekdays: weekdays(option.weekdays)}));
  const step = stepsIn(panel);
  const leaves = minutesOf(option.times[0]);
  add(step(clock(leaves)), "p", t("step_leave", {place: option.from_stop}));
  const ride = step("", option.mode, option.grade);
  add(ride, "p", t("ground_ride", {what: operatorText(option), hours: hours(option.minutes)}));
  if (option.times.length > 1)
    add(ride, "p", t("ground_also_at", {times: option.times.slice(1, 6).join(", ")
                                               + (option.times.length > 6 ? " " + t("and_more_count", {count: option.times.length - 6}) : "")}), "muted");
  const facts = add(ride, "div", undefined, "facts");
  gradeText(add(facts, "span", t("times") + ": "), option.grade);
  add(facts, "span", t("ground_price_none"));
  add(step(clock(leaves + option.minutes)), "p", t("step_arrival", {place: option.stop}));
}

// The way back with no price: its first straight departure in the timetables (ground.ways_back, grade B) drawn as
// the way there, or what is known of it: none those days, or not looked up
function groundBack(panel, option, out) {
  const timetable = option.back;
  if (!(timetable && timetable.times.length)) {
    add(panel, "h5", t("back"));
    add(panel, "p", (out ? t("no_way_back_sold_line") + " " : "")
        + (timetable === null ? t("back_unknown") : timetable === undefined ? t("back_not_asked")
           : t("no_straight_back", {operator: option.operator, days: timetable.days > 1 ? t("first_days_back", {count: timetable.days}) : day(timetable.day)})), "muted");
    return;
  }
  const [date, time] = timetable.times[0].split(" ");   // "dd/mm hh:mm", in the city's time
  add(panel, "h5", t("back") + ", " + date);
  if (out) add(panel, "p", t("no_way_back_sold_line"), "muted");
  const step = stepsIn(panel);
  const leaves = minutesOf(time);
  add(step(clock(leaves)), "p", t("step_leave", {place: option.stop}));
  const ride = step("", option.mode, "B");
  add(ride, "p", t("ground_ride", {what: operatorText(option), hours: hours(timetable.minutes)}));
  if (timetable.times.length > 1)
    add(ride, "p", t("ground_also_at", {times: timetable.times.slice(1, 5).join(", ") + (timetable.times.length > 5 ? " " + t("and_more") : "")}), "muted");
  const facts = add(ride, "div", undefined, "facts");
  gradeText(add(facts, "span", t("times") + ": "), "B");
  add(facts, "span", t("ground_price_none"));
  add(step(clock(leaves + timetable.minutes)), "p", t("step_arrival", {place: option.from_stop}));
}

// In the app, beside the button that closes the trip: one that asks for the prices of a city whose trains and buses
// the map did not price, as it prices only what can be among its cheapest (ground_fares.price_asked); the page
// follows the state until they come. A page opened as a file has no app to ask.
function priceButton(panel, options, key) {
  const asked = (state.asked_prices || {})[key];
  // Shown while one of its trains or buses that code can price has no price for the way there: asked again after "none"
  if (!data.token || !key || asked === "priced" || !(options || []).some(option => option.sold && !(option.price && option.price.out))) return;
  const button = add(panel, "button", t(asked === "waiting" ? "price_asking" : asked === "none" ? "price_ask_again" : "price_ask"), "ask-price");
  button.disabled = asked === "waiting";
  button.onclick = () => {
    button.disabled = true;
    fetch(data.price_link || "price", {method: "POST", body: new URLSearchParams({token: data.token, city: key})})
      .then(response => {
        if (response.status === 410) { button.replaceWith(add(panel, "p", t("price_map_ended"), "muted")); return; }
        if (!response.ok) throw new Error(String(response.status));
        state.asked_prices = {...(state.asked_prices || {}), [key]: "waiting"};
        render();
        follow();
      })
      .catch(() => { button.disabled = false; });
  };
}

// A city no flight goes to, reached straight by train or bus: its options only, with no price and no planner
// The route of the trip open, as the filters show its city: its flight, its trains and buses, or both. Made again
// when the filters change (draw), so the map never shows a way the list has hidden.
let routeMaker = null;
function showRoute(make, fit) {
  routeMaker = make;
  if (line) line.remove();
  line = make().addTo(map);
  if (fit && line.getLayers().length) map.fitBounds(line.getBounds(), {padding: [30, 30], maxZoom: 9});
  fitIcons();
}
function closeTrip(panel) {
  panel.hidden = true; picked = null; routeMaker = null;
  if (line) line.remove();
  map.invalidateSize();
}
// A city a flight goes to: its flight's route while the filters show the flight, even when they show its trains and
// buses too (the trip open is the flight's); its trains and buses' route once the flight is hidden
const tripRoute = p => (shownFlight(p) || !shownByGround(p.ground, p.country) ? routeOf(p) : groundRouteOf(p));

// key is the city's in the lists: "~" and its own for a city no flight goes to, its airport for one whose flight
// the filters hide
function explainGround(g, scroll, key) {
  picked = key || "~" + g.key;
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", t("close"), "close");
  close.onclick = () => closeTrip(panel);
  priceButton(panel, g.ground, picked);
  add(panel, "h2", data.origin.name + " → " + g.name);
  add(panel, "p", t("no_flight_here", {modes: g.modes.map(modeName).join(t("and")), origin: data.origin.name, hours: hours(g.minutes)}) + " "
      + (fareOf(g) !== null ? t("ground_ranked", {price: eur(fareOf(g)) + (data.round_trip ? " " + t("there_and_back") : "")}) : t("ground_not_ranked")), "muted");
  colourLegend(panel);
  describeGround(panel, g.ground, picked);
  add(panel, "h4", t("links"));
  const anchor = add(add(add(panel, "ul", undefined, "links"), "li"), "a", t("by_public_transport", {from: data.origin.name, to: g.name}));
  anchor.href = "https://www.google.com/maps/dir/?" + new URLSearchParams({api: 1, origin: data.origin.name, destination: g.ground[0].stop, travelmode: "transit"});
  anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  document.querySelectorAll("#rows li, #ground-rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === picked));
  map.invalidateSize();
  showRoute(() => groundRouteOf(g), true);   // after the map took its new size
  if (scroll !== false) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

// The route of a trip on the map, as its line and its steps say it: from home to the airport and from the airport
// into the city in the colour of the grade of each way's times, each flight as an arc from airport to airport through
// its stopovers, the way back the same on the other side. A place whose position is not known is left out of it.
const FLIGHT_COLOUR = "#1f6fd0";
const WALK_COLOUR = "#8a94a3";   // the way to a station, not timetabled: an SVG stroke takes no CSS variable
// The icon of a vehicle on the map, in the middle of its part of the route (from, to), as the line and the steps show
// it. Shown only while that part is long enough on the screen: a way of a few kilometres would hide its own ends
// until the map is brought closer (fitIcons)
const ICON_ROOM = 48;   // pixels a part needs on the screen for its icon
function vehicleMarker(group, where, kind, from, to) {
  const name = ["plane", "train", "bus", "tram", "ferry"].includes(kind) ? kind : "route";
  const marker = L.marker(where, {interactive: false, keyboard: false,
                                  icon: L.divIcon({className: "route-icon", iconSize: [24, 24],
                                                   html: '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>'})});
  marker.ends = [from, to];
  marker.addTo(group);
}
function fitIcons() {
  if (!line) return;
  line.eachLayer(layer => {
    if (!layer.ends || !layer.getElement()) return;
    const [from, to] = layer.ends.map(where => map.latLngToContainerPoint(where));
    layer.getElement().hidden = from.distanceTo(to) < ICON_ROOM;
  });
}
map.on("zoomend", fitIcons);
// The kind of the first vehicle of a way, as its step's icon (wayParts): "route" when its vehicles are not known
const kindOfWay = way => { const first = ((way && way.vehicles) || []).find(vehicle => vehicle.kind !== "walk"); return first ? first.kind : "route"; };
const middleOf = (from, to) => [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];

function routeOf(p) {
  const group = L.featureGroup(), access = p.access || [];
  const at = code => (state.places || {})[code] || (code === p.to ? [p.lat, p.lon] : null);
  const centreOf = code => (p.centres || {})[code] || at(code);
  const home = [data.origin.lat, data.origin.lon];
  const ground = (from, to, way) => {
    if (!from || !to || (from[0] === to[0] && from[1] === to[1])) return;
    L.polyline([from, to], {color: data.grade_colours[(way && way.grade) || "C"], weight: 4, opacity: 0.9}).addTo(group);
    vehicleMarker(group, middleOf(from, to), kindOfWay(way), from, to);
  };
  // An arc between two airports, bent to one side so that the way back does not hide the way there
  const arc = (from, to, side) => {
    if (!from || !to) return;
    const [lat1, lon1] = from, [lat2, lon2] = to, bend = 0.18 * side;
    const middle = [(lat1 + lat2) / 2 - (lon2 - lon1) * bend, (lon1 + lon2) / 2 + (lat2 - lat1) * bend];
    const points = [];
    for (let i = 0; i <= 24; i++) {
      const s = i / 24, a = (1 - s) * (1 - s), b = 2 * s * (1 - s), c = s * s;
      points.push([a * lat1 + b * middle[0] + c * lat2, a * lon1 + b * middle[1] + c * lon2]);
    }
    L.polyline(points, {color: FLIGHT_COLOUR, weight: 3, opacity: 0.9}).addTo(group);
    vehicleMarker(group, points[12], "plane", from, to);   // the middle of the arc
  };
  const airport = code => {
    if (at(code)) L.circleMarker(at(code), {radius: 4, color: "#fff", weight: 1.5, fillColor: FLIGHT_COLOUR, fillOpacity: 1})
      .bindTooltip(tip(place(code))).addTo(group);
  };
  const flights = (leg, side) => {
    for (const segment of leg.segments && leg.segments.length ? leg.segments : [leg]) { arc(at(segment.from), at(segment.to), side); airport(segment.from); airport(segment.to); }
  };
  ground(home, at(p.from), access[0]);
  flights(p, 1);
  ground(at(p.to), centreOf(p.to), access[1]);
  if (p.back) {
    ground(centreOf(p.back.from), at(p.back.from), access[2]);
    flights(p.back, -1);
    ground(at(p.back.to), home, access[3]);
  }
  return group;
}

// The route of a city reached by train or bus only: its quickest priced option (or its quickest), from the station it
// leaves from to the one it reaches, in the colour of the grade of its times, with its vehicle's icon. A station not
// known (a map made before they were kept) is the traveller's city.
function groundRouteOf(g) {
  const group = L.featureGroup();
  const allowed = (g.ground || []).filter(option => filter[option.mode]);   // a train hidden by the filters is not drawn
  const option = cheapestGround(allowed) || byPrice(allowed)[0];
  if (!option) return group;
  const home = [data.origin.lat, data.origin.lon];
  const from = option.from_lat !== undefined && option.from_lat !== null ? [option.from_lat, option.from_lon] : home;
  const to = option.lat !== undefined ? [option.lat, option.lon] : [g.lat, g.lon];
  const grade = option.price && option.price.out ? "A" : option.grade || "B";
  if (from !== home) L.polyline([home, from], {color: WALK_COLOUR, weight: 2, dashArray: "2 5"}).addTo(group);   // to the station
  L.polyline([from, to], {color: data.grade_colours[grade], weight: 4, opacity: 0.9}).addTo(group);
  for (const [where, name] of [[from, option.from_stop], [to, option.stop]])
    L.circleMarker(where, {radius: 4, color: "#fff", weight: 1.5, fillColor: GROUND_COLOUR, fillOpacity: 1}).bindTooltip(tip(name)).addTo(group);
  vehicleMarker(group, middleOf(from, to), option.mode, from, to);
  return group;
}

function explain(p, scroll) {
  if (!shownFlight(p) && shownByGround(p.ground, p.country)) {   // its flight hidden: the trip is its trains and buses
    const best = cheapestGround(p.ground);
    explainGround({key: p.to, name: p.name, lat: p.lat, lon: p.lon, ground: p.ground, fare_eur: best ? totalOf(best) : undefined,
                   modes: [...new Set(p.ground.map(option => option.mode))].sort(),
                   minutes: Math.min(...p.ground.map(option => option.minutes))}, scroll, p.to);
    return;
  }
  picked = p.to;
  const panel = document.getElementById("detail");
  panel.replaceChildren();
  panel.hidden = false;
  const close = add(panel, "button", t("close"), "close");
  close.onclick = () => closeTrip(panel);
  priceButton(panel, p.ground, p.to);
  add(panel, "h2", data.origin.name + " → " + p.name + " (" + p.to + ", " + p.country + ")");
  describeLines(panel, p);
  add(panel, "p", t("total_line", {total: eur(p.total_eur), stay: p.stay ? ", " + t("in_the_city", {stay: stayText(p.stay)}) : "",
                                   flights: t(p.back ? "flights_there_back" : "flight_one"), price: eur(p.price_eur), source: p.source, ground: eur(p.ground_eur)})
      + " " + (p.confirmed ? t("all_looked_up") : t("not_all_looked_up")), "muted");
  if (p.also && p.also.length)
    add(panel, "p", t("also_to", {city: p.name, others: p.also.map(other => place(other.to) + " " + t("from_airport", {airport: other.from}) + ", " + eur(other.total_eur)
        + " (" + (other.access && other.access.every(leg => leg.looked_up) ? t("looked_up") : t("estimated")) + ")").join("; ")}), "muted");
  if (p.ground && p.ground.length) {   // trains and buses to the same city, with no flight: timetables only (ground.py), never a price
    add(panel, "h4", t("also_no_flight"));
    describeGround(panel, p.ground, p.to);
  }
  const warnings = [];
  const warning = add(panel, "div");
  const access = p.access || [];
  describeLeg(panel, t("out"), p, warnings, access[0], access[1], data.origin.short, p.name);
  if (p.back) {
    add(panel, "p", t("landing_to_back", {hours: hours(between(p.arrival_date || p.date, p.arrival_time || p.time, p.back.date, p.back.time))})
        + (p.stay ? ": " + t("stay_detail", {stay: stayText(p.stay), city: p.name, minutes: data.airport_minutes})
           + (p.stay.estimated ? t("stay_estimated") : "") : "") + ".", "muted");
    describeLeg(panel, t("back"), {...p.back, source: p.source}, warnings, access[2], access[3], p.name, data.origin.short);
  }
  if (warnings.length) {
    add(warning, "h4", t("before_booking"), "warn");
    const list = add(warning, "ul", undefined, "links");
    warnings.forEach(text => add(list, "li", text, "warn"));
  }
  describeBooking(panel, p);
  add(panel, "h4", t("links"));
  const list = add(panel, "ul", undefined, "links");
  for (const link of p.links) {
    if (!link.url.startsWith("https://") || link.kind === "book") continue;
    const anchor = add(add(list, "li"), "a", link.label);
    anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  }
  add(panel, "p", t("price_may_change"), "muted");
  describePlan(panel, p);
  document.querySelectorAll("#rows li, #ground-rows tr").forEach(row => row.classList.toggle("picked", row.dataset.to === p.to));
  map.invalidateSize();   // the trip opened beside the map, which shrank to make room
  // The whole route in view, after the map took its new size. Nothing else moves the map when a trip opens: a
  // second move while the first is still animated is ignored by Leaflet
  showRoute(() => tripRoute(p), true);
  if (scroll !== false) panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

// The way to check the trip in the planner, which looks up the ground transport in real timetables: a link to its
// form in the app. A page opened as a file has no planner to open.
function describePlan(panel, p) {
  const legs = [[t("the_way_there"), p.plan.out], [t("the_way_back"), p.plan.back]].filter(([, plan]) => plan);
  if (!legs.length || !data.planner_link) return;
  add(panel, "h4", t("check_in_planner"));
  add(panel, "p", t("planner_intro"), "muted");
  for (const [label, plan] of legs) {   // its form, filled in with the trip, to check it and search
    const line = add(panel, "p");
    const link = add(line, "a", t("open_planner_on", {way: label}));
    link.href = data.planner_link + encodeURIComponent(plan.key);
  }
}

// One way of a flight on its card: the airport it leaves from, the day and the hour, and its stopovers
const leg = (flight, from) => from + " · " + day(flight.date) + " " + flight.time + (flight.stops ? " +" + flight.stops : "");
const rows = document.getElementById("rows");   // the destinations by air, one card each
// In the app, the way back to Discover's form; a page opened as a file has none
if (data.planner_link) {
  const back = document.getElementById("back");
  back.textContent = t("back_link"); back.href = "/discover"; back.hidden = false;
}

// Whether the program is still searching or has seen everything. The flights of every destination were all
// searched before the page opened: what can still change while running is the ways to and from the airports, and
// so the prices and their order. lost: the map's server stopped answering, so what is shown is its last state.
let lost = false;
function describeStatus() {
  const status = document.getElementById("status");
  const looked = state.lookups.length, count = state.points.length;
  const missing = (state.coverage || {}).missing || [];
  status.replaceChildren();
  status.className = lost ? "lost" : state.running ? "running" : "done";
  if (data.kiwi_why)   // only Ryanair's flights: the first thing to know about this map
    add(status, "p", t("only_ryanair", {why: data.kiwi_why}), "kiwi");
  document.title = (state.running && !lost ? "⏳ " : "") + data.title;
  const ground = groundCount();
  if (state.running && !lost) {
    add(status, "span", undefined, "pulse");
    add(status, "strong", t("still_searching"));
    add(status, "span", t("flights_all_searched", {count: count}) + " "
        + (state.phase === "timetables" ? t("timetables_running", {looked: looked, most: data.max_lookups}) + " " : ""));
    // The trains and buses, as the flights: what was seen, and what is still being asked
    const train = add(status, "p", "", "ground-status");
    if (ground.reached) train.append(t("ground_reached", {reached: ground.reached, origin: data.origin.short, flown: ground.flown}) + " "
        + (state.phase === "prices" ? t("phase_prices", {priced: ground.priced})
           : state.phase === "timetables" ? t("phase_timetables", {priced: ground.priced})
           : state.phase === "ways back" ? t("phase_ways_back", {priced: ground.priced})
           : t("phase_other", {priced: ground.priced})));
    add(status, "p", t("updates_itself"), "ground-status");
    return;
  }
  add(status, "strong", lost ? t("stopped") : t("finished"));
  add(status, "span", (lost ? t("lost_server") + " " : "")
      + t("all_on_map", {count: count}) + " "
      + (looked ? t("looked_up_count", {count: looked}) + (state.confirm_stop ? "; " + t("lookups_stop", {stop: state.confirm_stop}) : "") + ". "
                : t("none_looked_up") + " ")
      + (missing.length ? t("missing_flown", {count: missing.length}) : ""));
  if (ground.reached)
    add(status, "p", t("ground_reached", {reached: ground.reached, origin: data.origin.short, flown: ground.flown}) + " "
        + t("ground_priced_end", {priced: ground.priced, stop: state.fare_stop ? ": " + t("pricing_stop", {stop: state.fare_stop}) : ""}), "ground-status");
}

// The cities reached by train or bus: with no flight, and with a flight too (flown); priced, those with a price
function groundCount() {
  const flown = state.points.filter(p => p.ground && p.ground.length);
  const all = [...(state.by_ground || []).map(g => g.ground), ...flown.map(p => p.ground)];
  return {reached: all.length, flown: flown.length, priced: all.filter(options => options.some(option => totalOf(option) !== null)).length};
}

// What the map and the lists show, chosen above the map, so that a crowded map can be thinned out: a destination
// left out by a filter is only hidden. price and hours are null for no limit. flights hides the flights; train and
// bus the trains and buses of every city, a flight going there or not: London by bus stays with its flight
// hidden. A train or bus with no price yet is not hidden by the price. leave is the earliest hour the outward
// flight, or the train or bus, may leave at, arrive the latest it may arrive at, null for any: a way arriving the
// next day is let through by no limit.
const filter = {flights: true, train: true, bus: true, price: null, hours: null, leave: null, arrive: null, country: "", looked: false, direct: false};
const SHOW_ALL = {...filter};   // what "show all" brings the filters back to
const regions = (() => { try { return new Intl.DisplayNames([data.locale, "en"], {type: "region"}); } catch (error) { return null; } })();
const countryName = code => { try { return (regions && regions.of(code)) || code; } catch (error) { return code; } };
// Whether a way leaving at leaves and arriving at arrives (minutes from the midnight of the day it leaves) is within
// the hours of the filters
const inHours = (leaves, arrives) => (filter.leave === null || leaves >= filter.leave * 60) && (filter.arrive === null || arrives <= filter.arrive * 60);
const dayShift = (from, to) => (from && to && to !== from ? Math.round((Date.parse(to) - Date.parse(from)) / 86400000) * 1440 : 0);
const flightInHours = p => !p.time || !p.arrival_time
  || inHours(minutesOf(p.time), minutesOf(p.arrival_time) + dayShift(p.date, p.arrival_date));
// A train or bus: the outward way sold for a day when it has one, otherwise any of its timetable's departures
const groundInHours = option => {
  const out = option.price && option.price.out;
  if (out) return inHours(minutesOf(out.leave), minutesOf(out.arrive) + dayShift(out.day, out.arrive_day));
  return !(option.times || []).length || option.times.some(time => inHours(minutesOf(time), minutesOf(time) + option.minutes));
};
const shownFlight = p => filter.flights && (filter.price === null || p.total_eur <= filter.price)
  && (filter.hours === null || !p.minutes || p.minutes <= filter.hours * 60) && flightInHours(p)
  && (!filter.country || p.country === filter.country) && (!filter.looked || p.confirmed)
  && (!filter.direct || (!p.stops && (!p.back || !p.back.stops)));   // direct both ways
// The trains and buses of a city, with a flight or not, let through by the boxes of their modes, the hours on board,
// the country, and the price when they have one
const shownByGround = (options, country) => {
  if (!options || !options.length) return false;
  const best = cheapestGround(options), fare = best ? totalOf(best) : null;
  return options.some(option => filter[option.mode] && groundInHours(option)) && (filter.hours === null || Math.min(...options.map(option => option.minutes)) <= filter.hours * 60)
    && (!filter.country || country === filter.country) && (filter.price === null || fare === null || fare <= filter.price);
};
const shownGround = g => shownByGround(g.ground, g.country);

// The filters, made again when the destinations change (render): their choices are kept in filter
function describeFilters() {
  const box = document.getElementById("filters");
  box.replaceChildren();
  const byGround = state.by_ground || [];
  const check = (key, text) => {
    const label = add(box, "label"), input = add(label, "input");
    input.type = "checkbox"; input.checked = filter[key];
    input.onchange = () => { filter[key] = input.checked; draw(); };
    label.append(" " + text);
  };
  // A limit as the booking sites have it: a box to type the exact value in and a slider beside it, the two kept
  // together, up to the largest value of the map (which means no limit). The words around the value are the text's
  // own, split where {limit} goes, and do not change: the slider stays where it is while it moves. A lowest limit
  // (atLeast: from an hour on) has no limit at 0 instead.
  const slider = (key, text, top, step, unit, none, atLeast = false) => {
    const label = add(box, "label", undefined, "limit"), [before, after] = text.split("\u0000");
    label.append(before);
    const typed = add(label, "input"), range = add(label, "input");
    label.insertBefore(document.createTextNode(unit + after), range);
    const free = atLeast ? 0 : top;   // the value that means no limit
    typed.type = "number"; typed.min = 0; typed.max = top; typed.step = 1; typed.inputMode = "numeric";
    range.type = "range"; range.min = atLeast ? 0 : step; range.max = top; range.step = step;
    typed.setAttribute("aria-label", before.trim() || key);
    range.setAttribute("aria-label", before.trim() || key);
    const show = () => {
      typed.value = filter[key] === null ? free : filter[key];
      range.value = filter[key] === null ? free : Math.min(filter[key], top);
      label.title = filter[key] === null ? none : "";
      label.classList.toggle("set", filter[key] !== null);
    };
    const choose = value => {
      if (atLeast) filter[key] = value === null || value <= 0 ? null : Math.min(value, top);
      else filter[key] = value === null || value >= top ? null : Math.max(0, value);
      show(); draw();
    };
    range.oninput = () => choose(Number(range.value));
    // A typed value counts once it is complete: on Enter or on leaving the box, not at each digit
    typed.onchange = () => choose(typed.value === "" ? null : Math.round(Number(typed.value)));
    typed.onkeydown = event => { if (event.key === "Enter") typed.blur(); };
    show();
  };
  check("flights", t("filter_flights"));
  check("direct", t("filter_direct"));
  // The trains and buses of every city: the ones no flight goes to, and the ones a flight goes to as well
  const modes = new Set([...byGround, ...state.points].flatMap(city => (city.ground || []).map(option => option.mode)));
  if (modes.has("train")) check("train", t("filter_train"));
  if (modes.has("bus")) check("bus", t("filter_bus"));
  const top = Math.max(10, Math.ceil(Math.max(0, ...state.points.map(p => p.total_eur), ...byGround.map(g => fareOf(g) || 0)) / 10) * 10);
  slider("price", t("filter_price", {limit: "\u0000"}), top, 10, " €", t("any_price"));
  const longest = Math.max(1, ...state.points.map(p => p.minutes || 0), ...byGround.map(g => g.minutes));
  slider("hours", t("filter_hours", {limit: "\u0000"}), Math.ceil(longest / 60), 1, " h", t("any_time"));
  slider("leave", t("filter_leave", {limit: "\u0000"}), 23, 1, " h", t("any_hour"), true);   // from 0 h: any hour
  slider("arrive", t("filter_arrive", {limit: "\u0000"}), 24, 1, " h", t("any_hour"));
  const countries = {};
  for (const p of [...state.points, ...byGround]) if (p.country) countries[p.country] = (countries[p.country] || 0) + 1;
  const label = add(box, "label", t("filter_country") + " ");
  const select = add(label, "select");
  add(select, "option", t("filter_all")).value = "";
  for (const code of Object.keys(countries).sort((a, b) => countryName(a).localeCompare(countryName(b), data.locale)))
    add(select, "option", countryName(code) + " (" + countries[code] + ")").value = code;
  if (!countries[filter.country]) filter.country = "";
  select.value = filter.country;
  select.onchange = () => { filter.country = select.value; draw(); };
  check("looked", t("filter_looked"));
  const reset = add(box, "button", t("show_all"));
  reset.onclick = () => { Object.assign(filter, SHOW_ALL); describeFilters(); draw(); };
  add(box, "span", "", "count").id = "count";
}

// The card of a city reached by train or bus, beside the flights' cards: its fare, as its dot has it (or why it has none), how
// and with whom, the quickest trip and its first departures. Its dot is ringed in purple, as on the map
function groundCard(city, options, key, fare, colour, open) {
  const best = cheapestGround(options), first = byPrice(options)[0];
  const card = add(rows, "li", undefined, "city");
  card.dataset.to = key;
  card.dataset.price = fare === null ? "" : fare;
  card.tabIndex = 0;
  card.classList.toggle("picked", picked === key);
  card.onclick = () => open();   // which brings its route into view (explainGround)
  card.onkeydown = event => { if (event.key === "Enter") card.onclick(); };
  const modes = [...new Set(options.map(option => option.mode))].map(modeName).join(", ");
  const top = add(card, "div", undefined, "top");
  const dot = add(top, "span", undefined, "dot");
  dot.style.background = colour;
  dot.style.boxShadow = "0 0 0 2px " + GROUND_COLOUR;
  add(top, "strong", city.name);
  add(top, "b", fare !== null ? eur(fare) : options.some(option => option.price) ? t("not_sold_those_days") : "-", "total");
  add(card, "p", t("no_flight_modes", {modes}) + " · " + hours(Math.min(...options.map(option => option.minutes))), "way");
  add(card, "p", [...new Set(options.map(option => option.operator))].join(", "), "way");
  const foot = add(card, "p", undefined, "foot");
  add(foot, "span", t("col_leaves") + ": " + first.times.slice(0, 3).join(", ") + (first.times.length > 3 ? " …" : "")
      + (data.round_trip && best && best.price.stay ? " · " + t("col_in_city") + " " + stayText(best.price.stay) : ""));
  add(foot, "span", modes, "badge");
}

// The dots and the rows that the filters let through. The colours are by the price among every destination, so a
// filter does not change the colour of a dot. A city is coloured by its cheapest way there, by air or by train or
// bus; one reached by train or bus too has a purple ring around its dot.
function draw() {
  if (routeMaker) showRoute(routeMaker, false);
  const totals = [...state.points.map(cityPrice), ...(state.by_ground || []).map(fareOf).filter(fare => fare !== null)].sort((a, b) => a - b);
  const band = total => Math.min(colours.length - 1, Math.floor(colours.length * totals.filter(t => t < total).length / Math.max(1, totals.length)));
  markers.clearLayers();
  rows.replaceChildren();
  // A city a flight goes to is shown by its flight, by its trains and buses, or both, as the filters let each through
  const byAir = new Set(state.points.filter(shownFlight)), byLand = new Set(state.points.filter(p => shownByGround(p.ground, p.country)));
  for (const p of state.points.filter(p => byLand.has(p) && !byAir.has(p))) {   // its trains and buses only: as a city with no flight
    const best = cheapestGround(p.ground), fare = best ? totalOf(best) : null;
    L.circleMarker([p.lat, p.lon], {radius: 6, color: GROUND_COLOUR, weight: 2, fillColor: fare === null ? GROUND_COLOUR : colours[band(fare)],
                                    fillOpacity: fare === null ? 0.12 : 0.85})
      .addTo(markers).bindTooltip(tip(p.name + " · " + t("by_modes", {modes: [...new Set(p.ground.map(option => option.mode))].map(modeName).join(t("or"))})
                                  + (fare === null ? "" : " · " + eur(fare)) + (best && best.price.stay ? " · " + stayText(best.price.stay) : "") + " " + t("flights_hidden")))
      .on("click", () => explain(p));
  }
  const points = state.points.filter(p => byAir.has(p));
  for (const p of points) {
    const both = byLand.has(p), colour = colours[band(both ? cityPrice(p) : p.total_eur)];
    if (both)
      L.circleMarker([p.lat, p.lon], {radius: 10, color: GROUND_COLOUR, weight: 2, fill: false, interactive: false}).addTo(markers);
    L.circleMarker([p.lat, p.lon], {radius: 7, fillColor: colour, color: p.confirmed ? "#1d1d1f" : colour, fillOpacity: 0.85, weight: p.confirmed ? 2 : 1})
      .addTo(markers).bindTooltip(tip(p.name + " · " + eur(p.total_eur) + (p.stay ? " · " + stayText(p.stay) : "") + (both ? groundText(p) : "") + (p.confirmed ? " · " + t("looked_up") : ""))).on("click", () => explain(p));
    // Its card: the city and the total in the colour of its dot, the way there and back, what the total is made of
    // and how far the ways to and from the airports were looked up
    const card = add(rows, "li", undefined, "city");
    card.dataset.to = p.to;
    card.dataset.price = p.total_eur;
    card.tabIndex = 0;
    card.classList.toggle("picked", picked === p.to);
    card.onclick = () => explain(p);   // which brings its whole route into view
    card.onkeydown = event => { if (event.key === "Enter") card.onclick(); };
    const top = add(card, "div", undefined, "top");
    add(top, "span", undefined, "dot").style.background = colour;
    add(top, "strong", p.name + " (" + p.to + ")" + groundText(p));
    add(top, "b", eur(p.total_eur), "total");
    add(card, "p", t("col_out") + ": " + leg(p, p.from) + " · " + p.airline, "way");
    if (p.back)
      add(card, "p", t("col_back") + ": " + leg(p.back, p.back.from) + (p.back.to !== p.from ? " → " + p.back.to : "")
          + (p.back.airline !== p.airline ? " · " + p.back.airline : ""), "way");
    const foot = add(card, "p", undefined, "foot");
    add(foot, "span", t(data.round_trip ? "col_flights" : "col_flight") + " " + eur(p.price_eur) + " · " + t("col_airports") + " "
        + eur(p.ground_eur) + " (" + worst(p.access || []) + ")" + (data.round_trip && p.stay ? " · " + t("col_in_city") + " " + stayText(p.stay) : ""));
    const how = p.confirmed ? "looked_up" : (p.access || []).some(way => way.looked_up || way.checked) ? "partly" : "estimated";
    add(foot, "span", t(how), "badge " + how);
  }
  const byGround = state.by_ground || [];
  const ground = byGround.filter(shownGround);
  for (const g of ground) {
    const by = [...new Set(g.ground.map(option => option.operator))].join(", ");
    const fare = fareOf(g), best = cheapestGround(g.ground);
    // A city priced has the colour of its price among every destination, in the purple ring of a city with no flight
    L.circleMarker([g.lat, g.lon], {radius: 6, color: GROUND_COLOUR, weight: 2, fillColor: fare === null ? GROUND_COLOUR : colours[band(fare)],
                                    fillOpacity: fare === null ? 0.12 : 0.85})
      .addTo(markers).bindTooltip(tip(g.name + " · " + t("no_flight_modes", {modes: g.modes.map(modeName).join(t("or"))}) + (fare === null ? "" : " · " + eur(fare))
                                  + (best && best.price.stay ? " · " + stayText(best.price.stay) : "") + ", " + hours(g.minutes) + " · " + by))
      .on("click", () => explainGround(g));
    groundCard(g, g.ground, "~" + g.key, fare, fare === null ? GROUND_COLOUR : colours[band(fare)], () => explainGround(g));
  }
  // A city with a flight that the filters show by its trains and buses only has a card of that kind too
  for (const p of state.points.filter(p => byLand.has(p) && !byAir.has(p))) {
    const best = cheapestGround(p.ground), fare = best ? totalOf(best) : null;
    groundCard(p, p.ground, p.to, fare, fare === null ? GROUND_COLOUR : colours[band(fare)], () => explain(p));
  }
  // Every card by its price, the cheapest first, as the dots are coloured; the cities with no price last
  const price = card => (card.dataset.price === "" ? Infinity : Number(card.dataset.price));
  rows.append(...[...rows.children].sort((a, b) => price(a) - price(b)));
  // Every city reached by train or bus, with no flight or with one: the priced ones first, cheapest first, as the
  // flights; then the others, quickest first
  const listed = [...ground.map(g => ({city: g, options: g.ground, flight: null, key: "~" + g.key, open: () => explainGround(g)})),
                  ...state.points.filter(p => byLand.has(p)).map(p => ({city: p, options: p.ground, flight: p, key: p.to, open: () => explain(p)}))];
  const priceOf = entry => (cheapestGround(entry.options) ? totalOf(cheapestGround(entry.options)) : null);
  const quickest = entry => Math.min(...entry.options.map(option => option.minutes));
  listed.sort((a, b) => (priceOf(a) === null) - (priceOf(b) === null) || (priceOf(a) || 0) - (priceOf(b) || 0) || quickest(a) - quickest(b));
  document.getElementById("by-ground").hidden = !listed.length && !byGround.length;
  const groundRows = document.getElementById("ground-rows");
  groundRows.replaceChildren();
  for (const entry of listed) {
    const {city, options, flight} = entry, best = cheapestGround(options), fare = priceOf(entry);
    const row = groundRows.insertRow();
    row.dataset.to = entry.key;
    row.classList.toggle("picked", picked === entry.key);
    row.onclick = () => entry.open();   // which brings its route into view (explain, explainGround)
    const back = options.map(option => option.back).filter(Boolean);
    const asked = options.some(option => option.price);
    const first = byPrice(options)[0];
    for (const [value, number] of [[city.name], [fare !== null ? eur(fare) : asked ? t("not_sold_those_days") : "-", 1],
                                   ...(data.round_trip ? [[best && best.price.stay ? stayText(best.price.stay) : ""]] : []),
                                   [flight ? eur(flight.total_eur) : t("none"), 1], [[...new Set(options.map(option => option.mode))].map(modeName).join(", ")],
                                   [[...new Set(options.map(option => option.operator))].join(", ")], [hours(quickest(entry))],
                                   [first.times.slice(0, 3).join(", ") + (first.times.length > 3 ? " …" : "")],
                                   ...(data.round_trip ? [[options.some(option => option.price && option.price.back) ? t("sold_for_days")
                                                           : back.some(way => way.times.length) ? t("straight_back") : back.length ? t("none_straight") : t("not_looked_up")]] : [])]) {
      const cell = row.insertCell(); cell.textContent = value; if (number) cell.className = "n";
    }
  }
  const count = document.getElementById("count");
  const reachable = byGround.length + state.points.filter(p => p.ground && p.ground.length).length;
  if (count) count.textContent = t("showing", {shown: points.length, all: state.points.length})
    + (reachable ? " " + t("showing_ground", {shown: listed.length, all: reachable}) : "");
}

const groundHead = document.getElementById("ground-head");
for (const [title, number] of [[t("col_city")], [t(data.round_trip ? "col_there_back" : "col_price"), 1], ...(data.round_trip ? [[t("col_in_city")]] : []),
                               [t("col_flight_all"), 1], [t("col_by")], [t("col_operators")], [t("col_quickest")], [t("col_leaves")], ...(data.round_trip ? [[t("col_back_timetable")]] : [])])
  add(groundHead, "th", title, number ? "n" : "");

function render() {
  document.getElementById("about").textContent = t("about_line", {days: data.days, count: state.points.length, sources: data.sources});
  const seen = state.coverage || {};
  if (seen.flown)   // how much of what is flown from here the map found: the honest measure of what it may miss
    document.getElementById("about").textContent += " " + t("coverage_line", {found: seen.found, flown: seen.flown, airports: seen.airports.join(", ")})
      + (seen.ryanair_no_fare ? t("coverage_ryanair", {count: seen.ryanair_no_fare}) : "")
      + (seen.missing.length ? t("coverage_missing", {count: seen.missing.length}) : ".");
  describeStatus();
  // The filters are made again with the new destinations, but not under the traveller's hand: a slider being dragged
  // or a value being typed would be lost. They follow at the next change after the traveller is done.
  if (!document.getElementById("filters").contains(document.activeElement)) describeFilters();
  draw();
  const discarded = document.getElementById("discarded");
  discarded.replaceChildren();
  if (state.discarded.length) {
    add(discarded, "h2", t("left_out_title"));
    const list = add(add(discarded, "div", undefined, "note"), "ul");
    for (const gone of state.discarded) add(list, "li", t("left_out_item", {city: place(gone.to), from: gone.from, date: gone.date, reason: gone.reason}));
  }
  const sections = document.getElementById("sections");
  sections.replaceChildren();
  for (const part of state.sections) {
    add(sections, "h3", part.title);
    if (part.note) add(sections, "p", part.note, "note");
    if (!part.header.length) continue;
    const table = add(add(sections, "div", undefined, "wrap"), "table");
    const top = table.createTHead().insertRow();
    part.header.forEach(title => add(top, "th", title));
    const body = table.createTBody();
    for (const values of part.rows) { const row = body.insertRow(); values.forEach(value => { row.insertCell().textContent = value; }); }
  }
  const open = picked && state.points.find(p => p.to === picked);
  const openGround = picked && (state.by_ground || []).find(g => "~" + g.key === picked);
  if (open) explain(open, false);
  else if (openGround) explainGround(openGround, false);
}

// While the timetables are looked up, or a price asked for is on its way, the page in the app asks for the
// destinations again: one request at a time, more often for a price, which takes seconds
let following = false;
const pricing = () => Object.values(state.asked_prices || {}).includes("waiting");
function follow() {
  if (!(data.planner_link || data.state_link) || following || !(state.running || pricing())) return;
  following = true;
  setTimeout(() => fetch(data.state_link || "state").then(response => response.json())
    .then(next => { following = false; if (next.version !== state.version) { state = next; render(); } follow(); })
    .catch(() => { following = false; lost = true; describeStatus(); }), pricing() ? 1500 : 4000);
}
render();
follow();
