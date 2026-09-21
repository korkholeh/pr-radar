/* Placement for `templates/partials/info_tip.html`.
 *
 * Opening and closing stay in CSS (`.info-tip:hover`/`:focus-within`), so a bubble works with
 * this file absent and an htmx swap needs no re-initialisation. What CSS cannot do is keep the
 * bubble inside the window: an icon near the right edge, or in the bottom row of a long page,
 * would otherwise open its explanation past the viewport. This re-anchors the open bubble as
 * `position: fixed`, centred on its icon, flipped above when there is no room below, and clamped
 * to the viewport on both axes. Listeners are delegated from `document`, so cards that arrive
 * later are covered too.
 */
(function () {
  var MARGIN = 8;
  var PREFERRED_WIDTH = 320;
  var open = null;

  function bubbleOf(tip) {
    return tip.querySelector(".info-tip-bubble");
  }

  function place(tip) {
    var trigger = tip.querySelector(".info-tip-trigger");
    var bubble = bubbleOf(tip);
    if (!trigger || !bubble) return;

    var viewportWidth = document.documentElement.clientWidth;
    var viewportHeight = document.documentElement.clientHeight;
    var anchor = trigger.getBoundingClientRect();

    // Measure at the width it will actually be drawn at, before deciding where to put it. The
    // fallback rules pin the bubble to the trigger's inline end and push it down by a margin;
    // both have to go, or `left`/`top` computed here would be applied to an offset box.
    bubble.style.position = "fixed";
    bubble.style.insetInlineEnd = "auto";
    bubble.style.marginTop = "0px";
    bubble.style.left = "0px";
    bubble.style.top = "0px";
    bubble.style.width = Math.min(PREFERRED_WIDTH, viewportWidth - 2 * MARGIN) + "px";
    bubble.style.maxWidth = "none";
    var width = bubble.offsetWidth;
    var height = bubble.offsetHeight;

    // Taller than the window (a long explanation on a short phone screen): cap it and let the
    // text scroll rather than letting it run off both ends. A scrollable bubble has to take the
    // pointer, and since it lives inside `.info-tip` that also keeps it open while scrolled.
    var available = viewportHeight - 2 * MARGIN;
    if (height > available) {
      bubble.style.maxHeight = available + "px";
      bubble.style.overflowY = "auto";
      bubble.style.pointerEvents = "auto";
      height = bubble.offsetHeight;
    }

    var left = anchor.left + anchor.width / 2 - width / 2;
    left = Math.min(Math.max(left, MARGIN), Math.max(MARGIN, viewportWidth - width - MARGIN));

    var below = anchor.bottom + MARGIN;
    var above = anchor.top - height - MARGIN;
    var top = below + height + MARGIN > viewportHeight && above >= MARGIN ? above : below;
    top = Math.min(Math.max(top, MARGIN), Math.max(MARGIN, viewportHeight - height - MARGIN));

    bubble.style.left = Math.round(left) + "px";
    bubble.style.top = Math.round(top) + "px";
  }

  function reset(tip) {
    var bubble = bubbleOf(tip);
    if (!bubble) return;
    bubble.style.position = "";
    bubble.style.insetInlineEnd = "";
    bubble.style.marginTop = "";
    bubble.style.left = "";
    bubble.style.top = "";
    bubble.style.width = "";
    bubble.style.maxWidth = "";
    bubble.style.maxHeight = "";
    bubble.style.overflowY = "";
    bubble.style.pointerEvents = "";
  }

  function show(tip) {
    if (open && open !== tip) reset(open);
    open = tip;
    place(tip);
  }

  function hide(tip) {
    if (open !== tip) return;
    reset(tip);
    open = null;
  }

  function tipFrom(target) {
    return target && target.closest ? target.closest(".info-tip") : null;
  }

  document.addEventListener("pointerover", function (event) {
    var tip = tipFrom(event.target);
    if (tip) show(tip);
    // A tip holding the keyboard focus is still open by CSS after the pointer leaves it, so its
    // placement has to stay too — otherwise the bubble jumps to the fallback position.
    else if (open && !open.contains(document.activeElement)) hide(open);
  });

  document.addEventListener("focusin", function (event) {
    var tip = tipFrom(event.target);
    if (tip) show(tip);
  });

  document.addEventListener("focusout", function (event) {
    var tip = tipFrom(event.target);
    // A pointer still resting on the icon keeps the bubble open; CSS decides that, so only drop
    // the placement when the pointer is elsewhere too.
    if (tip && !tip.matches(":hover")) hide(tip);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && open) {
      var trigger = open.querySelector(".info-tip-trigger");
      if (trigger) trigger.blur();
      hide(open);
    }
  });

  // An open bubble is positioned in viewport coordinates, so it has to follow the page.
  window.addEventListener(
    "scroll",
    function () {
      if (open) place(open);
    },
    true,
  );
  window.addEventListener("resize", function () {
    if (open) place(open);
  });
  // The element the bubble is anchored to can vanish under it (an htmx fragment swap).
  document.addEventListener("htmx:afterSwap", function () {
    if (open && !open.isConnected) {
      open = null;
    }
  });
})();
