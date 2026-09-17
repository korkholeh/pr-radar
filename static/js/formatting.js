(function (global) {
  "use strict";

  // gettext/ngettext/interpolate come from Django's JavaScriptCatalog (/jsi18n/), loaded before
  // this file. They resolve the current request's active language, so no locale argument here.

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) {
      return "—";
    }
    seconds = Math.round(seconds);
    if (seconds < 60) {
      return interpolate(ngettext("%(count)s second", "%(count)s seconds", seconds), { count: seconds }, true);
    }
    if (seconds < 3600) {
      var minutes = Math.round(seconds / 60);
      if (minutes < 60) {
        return interpolate(ngettext("%(count)s minute", "%(count)s minutes", minutes), { count: minutes }, true);
      }
      seconds = 3600;
    }
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var mins = Math.floor((seconds % 3600) / 60);
    if (days > 0) {
      // interpolate()'s named substitution only recognises %(key)s, never %(key)d.
      return interpolate(gettext("%(days)sd %(hours)sh"), { days: days, hours: hours }, true);
    }
    return interpolate(gettext("%(hours)sh %(minutes)sm"), { hours: hours, minutes: mins }, true);
  }

  global.prRadar = global.prRadar || {};
  global.prRadar.formatDuration = formatDuration;
})(window);
