(function () {
  function currentPreference() {
    return document.documentElement.getAttribute("data-theme-preference");
  }

  function applySystemPreference() {
    if (currentPreference() !== "system") return;
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setResolvedTheme(dark ? "dark" : "light");
  }

  function setResolvedTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme: theme } }));
  }

  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applySystemPreference);

  document.addEventListener("DOMContentLoaded", function () {
    // The toggle submits the preference it is about to store; repaint the page with it right
    // away so the click is not answered only once the redirect has landed.
    var form = document.querySelector("[data-theme-switcher]");
    if (!form) return;
    form.addEventListener("submit", function () {
      var next = form.querySelector("[data-theme-next]");
      if (!next) return;
      document.documentElement.setAttribute("data-theme-preference", next.value);
      if (next.value === "dark" || next.value === "light") {
        setResolvedTheme(next.value);
      } else {
        applySystemPreference();
      }
    });
  });
})();
