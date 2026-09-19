(function () {
  "use strict";

  // The filter drawer: a right-hand panel that is closed until the page's "Filters" button opens it.
  // Everything is delegated from the document, so a drawer that arrives with an htmx swap works
  // without re-binding; the drawer itself lives outside the swapped region, so its open state
  // survives an Apply.

  var lastTrigger = null;

  function drawerFor(trigger) {
    var id = trigger.getAttribute("data-drawer-toggle");
    return id ? document.getElementById(id) : null;
  }

  function isOpen(drawer) {
    return drawer.hasAttribute("data-open");
  }

  function triggersFor(drawer) {
    return document.querySelectorAll('[data-drawer-toggle="' + drawer.id + '"]');
  }

  function open(drawer, trigger) {
    if (isOpen(drawer)) return;
    lastTrigger = trigger || null;
    drawer.setAttribute("data-open", "");
    drawer.removeAttribute("aria-hidden");
    triggersFor(drawer).forEach(function (button) {
      button.setAttribute("aria-expanded", "true");
    });
    // Land on the first control rather than on the close button: the panel exists to be filled in.
    var first = drawer.querySelector(
      ".drawer-body select, .drawer-body input:not([type='hidden']), .drawer-body textarea"
    );
    if (first) {
      window.setTimeout(function () {
        first.focus();
      }, 0);
    }
  }

  function close(drawer) {
    if (!isOpen(drawer)) return;
    drawer.removeAttribute("data-open");
    drawer.setAttribute("aria-hidden", "true");
    triggersFor(drawer).forEach(function (button) {
      button.setAttribute("aria-expanded", "false");
    });
    if (lastTrigger && document.contains(lastTrigger)) {
      lastTrigger.focus();
    }
    lastTrigger = null;
  }

  function openDrawers() {
    return document.querySelectorAll(".drawer[data-open]");
  }

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    var trigger = event.target.closest("[data-drawer-toggle]");
    if (trigger) {
      var drawer = drawerFor(trigger);
      if (!drawer) return;
      event.preventDefault();
      if (isOpen(drawer)) {
        close(drawer);
      } else {
        open(drawer, trigger);
      }
      return;
    }

    var closer = event.target.closest("[data-drawer-close]");
    if (closer) {
      var owner = closer.closest(".drawer");
      if (!owner) return;
      // The scrim and the close button only close; a link that also closes (Reset) must still
      // be allowed to follow its href.
      if (closer.tagName !== "A") event.preventDefault();
      close(owner);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    // A date picker inside the drawer answers Escape first and stops the event there, so this
    // only ever closes the drawer once nothing inside it is open.
    openDrawers().forEach(function (drawer) {
      close(drawer);
    });
  });

  // Applying the filters is the end of the interaction: hand the page back to the reader.
  document.addEventListener("submit", function (event) {
    var drawer = event.target.closest(".drawer");
    if (drawer) close(drawer);
  });
})();
