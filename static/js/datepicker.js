(function () {
  "use strict";

  // A themed replacement for the browser's own date popup, which no stylesheet can reach. The
  // field stays a plain text input holding an ISO date (`YYYY-MM-DD`) — the same string the
  // browser's date input would have submitted — so the server side is unchanged and a page with
  // JavaScript switched off still takes a typed date.
  //
  // The panel is positioned `fixed` against the field's bounding box rather than nested next to
  // it: a filter drawer scrolls its body, and an absolutely positioned panel would be clipped by
  // that scroll container.

  var ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
  var WEEK_START = 1; // Monday, as in `Europe/Kyiv` (REPORT_TIMEZONE) and both shipped locales.
  var open = null; // { input: HTMLInputElement, panel: HTMLElement, view: Date, focused: Date }
  var skipNextFocus = false; // Set while returning focus to the field, so it does not reopen.

  // gettext comes from Django's JavaScriptCatalog, which is only loaded for a signed-in page;
  // the fallback keeps a calendar on a page without it (the sign-in form has no date field
  // today, but nothing stops one appearing). Spelling the calls `gettext("...")` is also what
  // makes `makemessages -d djangojs` see these strings.
  var gettext =
    window.gettext ||
    function (text) {
      return text;
    };

  function locale() {
    return document.documentElement.getAttribute("lang") || "en";
  }

  function pad(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function toISO(date) {
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  function parseISO(value) {
    var match = ISO_RE.exec((value || "").trim());
    if (!match) return null;
    var year = Number(match[1]);
    var month = Number(match[2]) - 1;
    var day = Number(match[3]);
    var date = new Date(year, month, day);
    var valid = date.getFullYear() === year && date.getMonth() === month && date.getDate() === day;
    return valid ? date : null;
  }

  function today() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function addMonths(date, count) {
    return new Date(date.getFullYear(), date.getMonth() + count, 1);
  }

  function addDays(date, count) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() + count);
  }

  function sameDay(a, b) {
    return (
      !!a &&
      !!b &&
      a.getFullYear() === b.getFullYear() &&
      a.getMonth() === b.getMonth() &&
      a.getDate() === b.getDate()
    );
  }

  function monthLabel(date) {
    return new Intl.DateTimeFormat(locale(), { month: "long", year: "numeric" }).format(date);
  }

  function dayLabel(date) {
    return new Intl.DateTimeFormat(locale(), { dateStyle: "long" }).format(date);
  }

  function weekdayNames() {
    // 2024-01-01 is a Monday, so counting from it gives the week in the order the grid draws it.
    var monday = new Date(2024, 0, 1);
    var format = new Intl.DateTimeFormat(locale(), { weekday: "short" });
    var names = [];
    for (var i = 0; i < 7; i += 1) {
      names.push(format.format(addDays(monday, i)));
    }
    return names;
  }

  function icon(paths, extraClass) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("class", extraClass || "h-4 w-4");
    svg.setAttribute("aria-hidden", "true");
    paths.forEach(function (d) {
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      svg.appendChild(path);
    });
    return svg;
  }

  function button(className, label) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = className;
    if (label) {
      element.title = label;
      var sr = document.createElement("span");
      sr.className = "sr-only";
      sr.textContent = label;
      element.appendChild(sr);
    }
    return element;
  }

  // Enhancement ---------------------------------------------------------------------------

  function enhance(input) {
    if (input.dataset.datepickerReady === "1") return;
    input.dataset.datepickerReady = "1";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("inputmode", "numeric");
    if (!input.getAttribute("placeholder")) input.setAttribute("placeholder", "YYYY-MM-DD");

    var field = document.createElement("span");
    field.className = "datepicker-field";
    input.parentNode.insertBefore(field, input);
    field.appendChild(input);

    var trigger = button("datepicker-trigger", gettext("Open the calendar"));
    trigger.insertBefore(
      icon(["M7 3v3M17 3v3M4 9h16", "M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z"]),
      trigger.firstChild
    );
    trigger.setAttribute("tabindex", "-1"); // The input itself is the control in the tab order.
    trigger.addEventListener("click", function () {
      if (open && open.input === input) {
        closePanel();
        refocus(input);
      } else {
        openPanel(input);
        refocus(input);
      }
    });
    field.appendChild(trigger);
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll("input[data-datepicker]").forEach(enhance);
  }

  // Panel ---------------------------------------------------------------------------------

  function buildPanel() {
    var panel = document.createElement("div");
    panel.className = "datepicker-pop";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", gettext("Choose a date"));

    var head = document.createElement("div");
    head.className = "datepicker-head";
    var previous = button("datepicker-nav", gettext("Previous month"));
    previous.insertBefore(icon(["m14 6-6 6 6 6"]), previous.firstChild);
    previous.addEventListener("click", function () {
      move(addMonths(open.view, -1), false);
    });
    var title = document.createElement("div");
    title.className = "datepicker-title";
    title.setAttribute("aria-live", "polite");
    var next = button("datepicker-nav", gettext("Next month"));
    next.insertBefore(icon(["m10 6 6 6-6 6"]), next.firstChild);
    next.addEventListener("click", function () {
      move(addMonths(open.view, 1), false);
    });
    head.appendChild(previous);
    head.appendChild(title);
    head.appendChild(next);

    var weekdays = document.createElement("div");
    weekdays.className = "datepicker-grid";
    weekdayNames().forEach(function (name) {
      var cell = document.createElement("div");
      cell.className = "datepicker-weekday";
      cell.setAttribute("aria-hidden", "true");
      cell.textContent = name;
      weekdays.appendChild(cell);
    });

    var grid = document.createElement("div");
    grid.className = "datepicker-grid";
    grid.setAttribute("role", "grid");

    var footer = document.createElement("div");
    footer.className = "datepicker-footer";
    var todayButton = document.createElement("button");
    todayButton.type = "button";
    todayButton.className = "btn btn-ghost btn-sm";
    todayButton.textContent = gettext("Today");
    todayButton.addEventListener("click", function () {
      select(today());
    });
    var clearButton = document.createElement("button");
    clearButton.type = "button";
    clearButton.className = "btn btn-ghost btn-sm";
    clearButton.textContent = gettext("Clear");
    clearButton.addEventListener("click", function () {
      var input = open.input;
      input.value = "";
      closePanel();
      refocus(input);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    footer.appendChild(todayButton);
    footer.appendChild(clearButton);

    panel.appendChild(head);
    panel.appendChild(weekdays);
    panel.appendChild(grid);
    panel.appendChild(footer);
    // Named with an underscore: `panel.title` is the title *attribute*, which would stringify
    // the element assigned to it.
    panel._title = title;
    panel._grid = grid;
    // A click inside the panel must not travel on to the "click outside closes" listener.
    panel.addEventListener("mousedown", function (event) {
      event.preventDefault(); // Keep the focus on the input while the panel is used.
      event.stopPropagation();
    });
    return panel;
  }

  function render() {
    var panel = open.panel;
    var view = open.view;
    var selected = parseISO(open.input.value);
    var now = today();

    panel._title.textContent = monthLabel(view);

    var first = startOfMonth(view);
    var offset = (first.getDay() - WEEK_START + 7) % 7;
    var cursor = addDays(first, -offset);

    panel._grid.textContent = "";
    for (var i = 0; i < 42; i += 1) {
      var date = addDays(cursor, i);
      var cell = document.createElement("button");
      cell.type = "button";
      cell.className = "datepicker-day";
      if (date.getMonth() !== view.getMonth()) cell.className += " datepicker-day-outside";
      if (sameDay(date, now)) cell.className += " datepicker-day-today";
      if (sameDay(date, selected)) cell.className += " datepicker-day-selected";
      if (sameDay(date, open.focused)) cell.className += " datepicker-day-focused";
      cell.textContent = String(date.getDate());
      cell.setAttribute("data-date", toISO(date));
      cell.setAttribute("aria-label", dayLabel(date));
      if (sameDay(date, selected)) cell.setAttribute("aria-current", "date");
      cell.setAttribute("tabindex", "-1");
      cell.addEventListener("click", function (event) {
        select(parseISO(event.currentTarget.getAttribute("data-date")));
      });
      panel._grid.appendChild(cell);
    }
    position();
  }

  function position() {
    var box = open.input.getBoundingClientRect();
    var panel = open.panel;
    var width = panel.offsetWidth;
    var height = panel.offsetHeight;
    var gap = 6;
    var left = Math.min(Math.max(8, box.left), window.innerWidth - width - 8);
    var top = box.bottom + gap;
    if (top + height > window.innerHeight - 8) {
      // No room underneath: sit above the field instead, and as a last resort pin to the top.
      top = Math.max(8, box.top - height - gap);
    }
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  function move(view, keepFocusedDay) {
    open.view = startOfMonth(view);
    if (!keepFocusedDay) open.focused = null;
    render();
  }

  function select(date) {
    if (!date) return;
    var input = open.input;
    input.value = toISO(date);
    closePanel();
    refocus(input);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function openPanel(input) {
    if (open && open.input === input) return;
    closePanel();
    var value = parseISO(input.value);
    open = {
      input: input,
      panel: buildPanel(),
      view: startOfMonth(value || today()),
      focused: value || today(),
    };
    document.body.appendChild(open.panel);
    input.setAttribute("aria-expanded", "true");
    render();
  }

  // Returning the focus to the field must not bounce the calendar straight back open.
  function refocus(input) {
    skipNextFocus = true;
    input.focus();
    skipNextFocus = false;
  }

  function closePanel() {
    if (!open) return;
    open.input.removeAttribute("aria-expanded");
    open.panel.remove();
    open = null;
  }

  // Interaction ---------------------------------------------------------------------------

  document.addEventListener("click", function (event) {
    if (!open) return;
    if (!(event.target instanceof Element)) return;
    if (event.target === open.input || open.panel.contains(event.target)) return;
    if (event.target.closest(".datepicker-field")) return;
    closePanel();
  });

  document.addEventListener("focusin", function (event) {
    var target = event.target;
    var wasSkipped = skipNextFocus;
    skipNextFocus = false;
    if (target instanceof HTMLInputElement && target.matches("input[data-datepicker]")) {
      if (!wasSkipped) openPanel(target);
      return;
    }
    if (!open) return;
    if (target instanceof Element && (open.panel.contains(target) || target.closest(".datepicker-field"))) {
      return;
    }
    closePanel();
  });

  document.addEventListener("keydown", function (event) {
    if (!open) return;
    if (event.target !== open.input) return;

    var focused = open.focused || parseISO(open.input.value) || today();
    var step = null;

    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation(); // Escape closes the calendar, not the drawer behind it.
        closePanel();
        return;
      case "Enter":
        if (open.focused) {
          event.preventDefault();
          select(open.focused);
        }
        return;
      case "ArrowLeft":
        step = -1;
        break;
      case "ArrowRight":
        step = 1;
        break;
      case "ArrowUp":
        step = -7;
        break;
      case "ArrowDown":
        step = 7;
        break;
      case "PageUp":
        event.preventDefault();
        open.focused = addMonths(focused, -1);
        open.focused = new Date(open.focused.getFullYear(), open.focused.getMonth(), focused.getDate());
        move(open.focused, true);
        return;
      case "PageDown":
        event.preventDefault();
        open.focused = addMonths(focused, 1);
        open.focused = new Date(open.focused.getFullYear(), open.focused.getMonth(), focused.getDate());
        move(open.focused, true);
        return;
      default:
        return;
    }

    event.preventDefault();
    open.focused = addDays(focused, step);
    open.view = startOfMonth(open.focused);
    render();
  });

  // A typed date moves the calendar to the month it names.
  document.addEventListener("input", function (event) {
    if (!open || event.target !== open.input) return;
    var typed = parseISO(open.input.value);
    if (typed) {
      open.focused = typed;
      move(typed, true);
    }
  });

  window.addEventListener("resize", function () {
    if (open) position();
  });

  window.addEventListener(
    "scroll",
    function () {
      if (open) position();
    },
    true
  );

  document.addEventListener("DOMContentLoaded", function () {
    enhanceAll(document);
  });

  // htmx swaps bring new fields in; the drawer itself is never swapped, but a filter form
  // rendered as a fragment is.
  document.body.addEventListener("htmx:afterSettle", function (event) {
    closePanel();
    enhanceAll(event.target);
  });
})();
