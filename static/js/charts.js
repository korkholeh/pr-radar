(function () {
  "use strict";

  // Colours travel from the server as CSS **token names** (`--series-ai`, `--series-1`, ...),
  // never as literals (plan §4, tests/test_no_hardcoded_colors.py) — resolved here at draw time
  // with getComputedStyle so a `themechange` redraw always picks up the theme's current values.
  function resolveToken(token) {
    return getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  }

  var instances = {};

  function destroyChart(key) {
    if (instances[key]) {
      instances[key].destroy();
      delete instances[key];
    }
  }

  function buildDatasets(payload) {
    return payload.datasets.map(function (dataset) {
      var color = resolveToken(dataset.color_token);
      return {
        label: dataset.label,
        data: dataset.data,
        backgroundColor: color,
        borderColor: color,
        borderWidth: payload.type === "line" ? 2 : 1,
        tension: 0.2,
        fill: false,
      };
    });
  }

  function formatValue(unit, value) {
    if (value === null || value === undefined) return "—";
    if (unit === "ratio") return (value * 100).toFixed(1) + "%";
    if (unit === "duration" && window.prRadar && window.prRadar.formatDuration) {
      return window.prRadar.formatDuration(value);
    }
    return String(value);
  }

  function buildConfig(payload) {
    var gridColor = resolveToken("--grid");
    var tooltipBg = resolveToken("--tooltip-bg");
    return {
      type: payload.type,
      data: { labels: payload.labels, datasets: buildDatasets(payload) },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            stacked: payload.stacked,
            title: { display: true, text: payload.x_title },
            grid: { color: gridColor },
          },
          y: {
            stacked: payload.stacked,
            title: { display: true, text: payload.y_title },
            grid: { color: gridColor },
          },
        },
        plugins: {
          tooltip: {
            backgroundColor: tooltipBg,
            callbacks: {
              label: function (context) {
                return context.dataset.label + ": " + formatValue(payload.unit, context.parsed.y);
              },
            },
          },
        },
      },
    };
  }

  function renderCanvas(canvas, payload) {
    var key = canvas.getAttribute("data-chart-key");
    destroyChart(key);
    instances[key] = new Chart(canvas.getContext("2d"), buildConfig(payload));
  }

  function renderError(canvas) {
    var key = canvas.getAttribute("data-chart-key");
    destroyChart(key);
    var message = window.gettext ? window.gettext("Could not load this chart.") : "Could not load this chart.";
    var card = canvas.closest("[data-testid='chart-card']") || canvas.parentElement;
    if (card) {
      var note = document.createElement("p");
      note.className = "text-sm text-[var(--bad)]";
      note.setAttribute("data-testid", "chart-error-" + key);
      note.textContent = message;
      canvas.style.display = "none";
      card.appendChild(note);
    }
  }

  function initOne(canvas) {
    var url = canvas.getAttribute("data-chart-url");
    if (!url) return;
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error("Chart request failed: " + response.status);
        return response.json();
      })
      .then(function (payload) {
        if (!payload.empty) {
          renderCanvas(canvas, payload);
        }
      })
      .catch(function () {
        renderError(canvas);
      });
  }

  function initCharts(root) {
    (root || document).querySelectorAll("[data-chart-key]").forEach(initOne);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initCharts(document);
  });
  document.addEventListener("themechange", function () {
    initCharts(document);
  });
  document.body.addEventListener("htmx:afterSwap", function (event) {
    initCharts(event.target);
  });

  window.prRadar = window.prRadar || {};
  window.prRadar.initCharts = initCharts;
})();
