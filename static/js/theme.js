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
    var select = document.getElementById("theme-select");
    if (!select) return;
    select.addEventListener("change", function () {
      if (select.value === "dark" || select.value === "light") {
        setResolvedTheme(select.value);
      } else {
        applySystemPreference();
      }
    });
  });
})();
