(function () {
  "use strict";

  // A tag widget over a plain `<select multiple>`: the chosen values are chips in a box, the rest
  // live in a searchable drop-down. The native control stays in the DOM, hidden, and remains the
  // only source of truth — it is what the form submits, so the query string, the Django form and
  // the no-JavaScript fallback are all untouched.
  //
  // Like the date picker, the menu is positioned `fixed` against the box rather than nested
  // inside it: a filter drawer scrolls its body and would otherwise clip it.

  // gettext comes from Django's JavaScriptCatalog. Spelling the calls out is also what makes
  // `makemessages -d djangojs` see these strings.
  var gettext =
    window.gettext ||
    function (text) {
      return text;
    };
  var interpolate =
    window.interpolate ||
    function (text, params) {
      return text.replace(/%\((\w+)\)s/g, function (match, key) {
        return params[key];
      });
    };

  var open = null; // The one widget whose menu is showing.
  var idSeq = 0;

  function optionsOf(select) {
    return Array.prototype.slice.call(select.options);
  }

  function labelOf(option) {
    return (option.textContent || "").trim();
  }

  function icon(paths, extraClass) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
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

  // Enhancement ---------------------------------------------------------------------------

  function enhance(select) {
    if (select.dataset.tagselectReady === "1") return;
    select.dataset.tagselectReady = "1";

    var widget = {
      select: select,
      root: document.createElement("div"),
      control: document.createElement("div"),
      tags: document.createElement("span"),
      menu: null,
      search: null,
      list: null,
      active: -1, // Index into the currently shown options, for the keyboard.
      shown: [],
    };

    idSeq += 1;
    widget.root.className = "tagselect";
    widget.control.className = "tagselect-control";
    widget.control.setAttribute("role", "combobox");
    widget.control.setAttribute("aria-haspopup", "listbox");
    widget.control.setAttribute("aria-expanded", "false");
    widget.control.setAttribute("tabindex", "0");
    widget.control.id = (select.id || "tagselect-" + idSeq) + "-control";
    // A stable hook for the e2e suite, which can no longer reach the hidden native control.
    widget.control.setAttribute("data-testid", "tagselect-" + (select.name || select.id || idSeq));

    // The field's own <label for="..."> points at the hidden select; name the box after it too.
    var label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
    if (label) {
      if (!label.id) label.id = select.id + "-label";
      widget.control.setAttribute("aria-labelledby", label.id);
    }

    widget.tags.className = "tagselect-tags";
    widget.control.appendChild(widget.tags);
    widget.control.appendChild(icon(["m6 9 6 6 6-6"], "tagselect-chevron h-4 w-4"));

    select.parentNode.insertBefore(widget.root, select);
    widget.root.appendChild(select);
    widget.root.appendChild(widget.control);
    select.hidden = true; // A hidden control is still submitted; only a disabled one is not.
    select.setAttribute("tabindex", "-1");

    widget.control.addEventListener("click", function () {
      if (open === widget) {
        closeMenu();
      } else {
        openMenu(widget);
      }
    });
    widget.control.addEventListener("keydown", function (event) {
      onControlKeyDown(widget, event);
    });

    renderTags(widget);
  }

  function enhanceAll(root) {
    var scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll("select[multiple]").forEach(function (select) {
      if (select.closest(".filter-form") || select.hasAttribute("data-tagselect")) enhance(select);
    });
  }

  // Rendering -----------------------------------------------------------------------------

  function renderTags(widget) {
    var select = widget.select;
    var selected = optionsOf(select).filter(function (option) {
      return option.selected;
    });

    widget.tags.textContent = "";
    if (!selected.length) {
      var placeholder = document.createElement("span");
      placeholder.className = "tagselect-placeholder";
      placeholder.textContent = gettext("Any");
      widget.tags.appendChild(placeholder);
      return;
    }

    selected.forEach(function (option) {
      var tag = document.createElement("span");
      tag.className = "tag";
      var text = document.createElement("span");
      text.className = "tag-label";
      text.textContent = labelOf(option);
      tag.appendChild(text);

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "tag-remove";
      remove.title = interpolate(gettext("Remove %(item)s"), { item: labelOf(option) }, true);
      remove.appendChild(icon(["m6 6 12 12M18 6 6 18"], "h-3 w-3"));
      var sr = document.createElement("span");
      sr.className = "sr-only";
      sr.textContent = remove.title;
      remove.appendChild(sr);
      remove.addEventListener("click", function (event) {
        // Removing a tag must not also toggle the menu the box would open.
        event.stopPropagation();
        setSelected(widget, option, false);
        widget.control.focus();
      });
      tag.appendChild(remove);
      widget.tags.appendChild(tag);
    });
  }

  function renderOptions(widget) {
    var query = (widget.search.value || "").trim().toLowerCase();
    widget.shown = optionsOf(widget.select).filter(function (option) {
      return !query || labelOf(option).toLowerCase().indexOf(query) !== -1;
    });
    if (widget.active >= widget.shown.length) widget.active = widget.shown.length - 1;

    widget.list.textContent = "";
    if (!widget.shown.length) {
      var empty = document.createElement("p");
      empty.className = "tagselect-empty";
      empty.textContent = gettext("Nothing matches");
      widget.list.appendChild(empty);
      return;
    }

    widget.shown.forEach(function (option, index) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "tagselect-option";
      if (option.selected) row.className += " tagselect-option-selected";
      if (index === widget.active) row.className += " tagselect-option-active";
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", option.selected ? "true" : "false");
      row.setAttribute("tabindex", "-1");

      var check = icon(["m5 12 5 5 9-9"], "tagselect-check h-4 w-4");
      row.appendChild(check);
      var text = document.createElement("span");
      text.textContent = labelOf(option);
      row.appendChild(text);

      row.addEventListener("mousedown", function (event) {
        event.preventDefault(); // Keep the focus in the search field.
      });
      row.addEventListener("click", function () {
        widget.active = index;
        setSelected(widget, option, !option.selected);
      });
      widget.list.appendChild(row);
    });
  }

  function setSelected(widget, option, selected) {
    option.selected = selected;
    widget.select.dispatchEvent(new Event("change", { bubbles: true }));
    renderTags(widget);
    if (open === widget) {
      renderOptions(widget);
      position(widget);
    }
  }

  // Menu ----------------------------------------------------------------------------------

  function buildMenu(widget) {
    var menu = document.createElement("div");
    menu.className = "tagselect-menu";

    var search = document.createElement("input");
    search.type = "search";
    search.className = "tagselect-search";
    search.setAttribute("autocomplete", "off");
    search.setAttribute("placeholder", gettext("Search"));
    search.setAttribute("aria-label", gettext("Search"));
    search.addEventListener("input", function () {
      widget.active = widget.select.options.length ? 0 : -1;
      renderOptions(widget);
      position(widget);
    });
    search.addEventListener("keydown", function (event) {
      onSearchKeyDown(widget, event);
    });

    var list = document.createElement("div");
    list.className = "tagselect-options";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-multiselectable", "true");

    menu.appendChild(search);
    menu.appendChild(list);
    widget.menu = menu;
    widget.search = search;
    widget.list = list;
    return menu;
  }

  function openMenu(widget) {
    closeMenu();
    open = widget;
    document.body.appendChild(buildMenu(widget));
    widget.control.setAttribute("aria-expanded", "true");
    widget.active = widget.select.options.length ? 0 : -1;
    renderOptions(widget);
    position(widget);
    widget.search.focus();
  }

  function closeMenu() {
    if (!open) return;
    var widget = open;
    open = null;
    widget.control.setAttribute("aria-expanded", "false");
    if (widget.menu) widget.menu.remove();
    widget.menu = widget.search = widget.list = null;
  }

  function position(widget) {
    var box = widget.control.getBoundingClientRect();
    var menu = widget.menu;
    menu.style.width = Math.max(box.width, 200) + "px";
    var height = menu.offsetHeight;
    var gap = 4;
    var left = Math.min(Math.max(8, box.left), window.innerWidth - menu.offsetWidth - 8);
    var top = box.bottom + gap;
    if (top + height > window.innerHeight - 8) {
      top = Math.max(8, box.top - height - gap);
    }
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  // Keyboard ------------------------------------------------------------------------------

  function onControlKeyDown(widget, event) {
    if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown") {
      event.preventDefault();
      openMenu(widget);
      return;
    }
    if (event.key === "Backspace") {
      var selected = optionsOf(widget.select).filter(function (option) {
        return option.selected;
      });
      if (!selected.length) return;
      event.preventDefault();
      setSelected(widget, selected[selected.length - 1], false);
    }
  }

  function moveActive(widget, step) {
    if (!widget.shown.length) return;
    var next = widget.active + step;
    if (next < 0) next = widget.shown.length - 1;
    if (next >= widget.shown.length) next = 0;
    widget.active = next;
    renderOptions(widget);
    var row = widget.list.children[widget.active];
    if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
  }

  function onSearchKeyDown(widget, event) {
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        event.stopPropagation(); // Escape closes the menu, not the drawer behind it.
        closeMenu();
        widget.control.focus();
        return;
      case "ArrowDown":
        event.preventDefault();
        moveActive(widget, 1);
        return;
      case "ArrowUp":
        event.preventDefault();
        moveActive(widget, -1);
        return;
      case "Enter":
        event.preventDefault(); // Never submit the filter form from the option search.
        if (widget.active >= 0 && widget.shown[widget.active]) {
          var option = widget.shown[widget.active];
          setSelected(widget, option, !option.selected);
        }
        return;
      case "Backspace":
        if (widget.search.value) return;
        var selected = optionsOf(widget.select).filter(function (item) {
          return item.selected;
        });
        if (!selected.length) return;
        event.preventDefault();
        setSelected(widget, selected[selected.length - 1], false);
        return;
      case "Tab":
        closeMenu();
        return;
      default:
    }
  }

  // Interaction ---------------------------------------------------------------------------

  document.addEventListener("mousedown", function (event) {
    if (!open) return;
    if (!(event.target instanceof Element)) return;
    if (open.menu.contains(event.target) || open.root.contains(event.target)) return;
    closeMenu();
  });

  document.addEventListener("focusin", function (event) {
    if (!open) return;
    if (!(event.target instanceof Element)) return;
    if (open.menu.contains(event.target) || open.root.contains(event.target)) return;
    closeMenu();
  });

  window.addEventListener("resize", function () {
    if (open) position(open);
  });

  window.addEventListener(
    "scroll",
    function () {
      if (open) position(open);
    },
    true
  );

  document.addEventListener("DOMContentLoaded", function () {
    enhanceAll(document);
  });

  document.body.addEventListener("htmx:afterSettle", function (event) {
    closeMenu();
    enhanceAll(event.target);
  });
})();
