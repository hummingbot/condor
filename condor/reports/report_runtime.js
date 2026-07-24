(function () {
  "use strict";

  const specElement = document.getElementById("condor-report-spec");
  if (!specElement) return;

  let spec;
  try {
    spec = JSON.parse(specElement.textContent || "{}");
  } catch (error) {
    console.error("Invalid Condor report specification", error);
    return;
  }

  const components = spec.components || [];
  const datasets = spec.datasets || {};
  const autoRefreshSeconds = Number(spec.auto_refresh_seconds);
  const autoRefreshEnabled = Number.isFinite(autoRefreshSeconds) && autoRefreshSeconds >= 1;
  const offlineSnapshot = autoRefreshEnabled && window.location.protocol === "file:";
  let autoRefreshPaused = false;
  let autoRefreshTimer = null;
  const rowsBySource = {};
  for (const [source, rows] of Object.entries(datasets)) {
    rowsBySource[source] = (rows || []).map((row, index) => ({
      id: source + ":" + index,
      row: row,
    }));
  }

  const filterState = new Map();
  const selectionState = new Map();
  const tableState = new Map();
  let compactChartLayout = window.innerWidth <= 800;
  let uiRevision = 0;

  function reloadReport() {
    const url = new URL(window.location.href);
    url.searchParams.set("_condor_refresh", Date.now().toString());
    window.location.replace(url.toString());
  }

  function startAutoRefresh() {
    if (!autoRefreshEnabled || offlineSnapshot || autoRefreshPaused || autoRefreshTimer) return;
    autoRefreshTimer = window.setInterval(reloadReport, autoRefreshSeconds * 1000);
  }

  function pauseAutoRefresh() {
    if (!autoRefreshEnabled || offlineSnapshot || autoRefreshPaused) return;
    autoRefreshPaused = true;
    if (autoRefreshTimer) window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
    updateActions();
  }

  function resumeAutoRefresh() {
    if (!autoRefreshEnabled || offlineSnapshot || !autoRefreshPaused) return;
    autoRefreshPaused = false;
    updateActions();
    reloadReport();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function valueKey(value) {
    return typeof value + ":" + String(value);
  }

  function isDateValue(value) {
    if (typeof value !== "string") return false;
    return /^\d{4}-\d{2}-\d{2}(?:[T ]|$)/.test(value) && !Number.isNaN(Date.parse(value));
  }

  function comparable(value, valueType, boundary) {
    if (value === null || value === undefined || value === "") return null;
    if (["date", "datetime"].includes(valueType) || (valueType === "auto" && isDateValue(value))) {
      const text = String(value);
      let normalized = text;
      if (valueType === "date" && boundary === "max" && /^\d{4}-\d{2}-\d{2}$/.test(text)) {
        normalized += "T23:59:59.999Z";
      } else if (valueType === "datetime" && boundary === "max" && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}$/.test(text)) {
        normalized = text.replace(" ", "T") + ":59.999Z";
      } else if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text)) {
        normalized = text.replace(" ", "T") + "Z";
      }
      const parsed = Date.parse(normalized);
      return Number.isNaN(parsed) ? null : parsed;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function finiteNumber(value) {
    if (value === null || value === undefined || (typeof value === "string" && !value.trim())) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function rangeMatches(value, state) {
    if (!state.touched || (state.min == null && state.max == null)) return true;
    const candidate = comparable(value, state.valueType);
    if (candidate == null) return false;
    return (state.min == null || candidate >= state.min) && (state.max == null || candidate <= state.max);
  }

  function advanceUiRevision() {
    uiRevision += 1;
    return uiRevision;
  }

  function inferRangeType(component, entries) {
    if (component.value_type !== "auto") return component.value_type;
    const value = entries.map((entry) => entry.row[component.field]).find((item) => item != null && item !== "");
    if (!isDateValue(value)) return "number";
    return /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? "date" : "datetime";
  }

  function displayRangeValue(value, valueType) {
    if (value == null) return "";
    if (valueType === "number") return String(value);
    const options = valueType === "date"
      ? { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" }
      : { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" };
    return new Intl.DateTimeFormat(undefined, options).format(new Date(value));
  }

  function filteredEntries(source, consumerId) {
    let entries = rowsBySource[source] || [];
    for (const component of components) {
      if (component.type !== "filter" || component.source !== source) continue;
      const state = filterState.get(component.id);
      if (!state) continue;
      if (component.filter_type === "select" && state.values && state.values.size) {
        entries = entries.filter((entry) => state.values.has(valueKey(entry.row[component.field])));
      }
      if (component.filter_type === "range" && state.touched && (state.min != null || state.max != null)) {
        entries = entries.filter((entry) => rangeMatches(entry.row[component.field], state));
      }
    }

    const selection = selectionState.get(source);
    if (selection && selection.origin !== consumerId) {
      const consumer = components.find((component) => component.id === consumerId);
      if (selection.mode !== "drilldown" || (consumer && consumer.type === "drilldown")) {
        entries = entries.filter((entry) => selection.ids.has(entry.id));
      }
    }
    return entries;
  }

  function formatNumber(value, format) {
    if (!Number.isFinite(Number(value))) return String(value ?? "");
    const match = String(format || "").match(/\.(\d+)f/);
    const decimals = match ? Number(match[1]) : null;
    const options = {
      useGrouping: String(format || "").includes(","),
    };
    if (decimals != null) {
      options.minimumFractionDigits = decimals;
      options.maximumFractionDigits = decimals;
    } else {
      options.maximumFractionDigits = 6;
    }
    return new Intl.NumberFormat(undefined, options).format(Number(value));
  }

  function formatValue(value, config) {
    if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
    const rendered = typeof value === "number" ? formatNumber(value, config.format) : String(value);
    return String(config.prefix || "") + rendered + String(config.suffix || "");
  }

  function aggregate(entries, field, operation) {
    if (operation === "count") return entries.length;
    const values = entries
      .map((entry) => entry.row[field])
      .filter((value) => value !== null && value !== undefined && value !== "");
    if (!values.length) return null;
    if (operation === "first") return values[0];
    if (operation === "last") return values[values.length - 1];
    const numbers = values.map(Number).filter(Number.isFinite);
    if (!numbers.length) return null;
    if (operation === "change_pct") {
      return numbers[0] === 0 ? null : (numbers[numbers.length - 1] / numbers[0] - 1) * 100;
    }
    if (operation === "sum") return numbers.reduce((sum, value) => sum + value, 0);
    if (operation === "mean") return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
    if (operation === "min") return Math.min(...numbers);
    if (operation === "max") return Math.max(...numbers);
    if (operation === "median") {
      numbers.sort((a, b) => a - b);
      const middle = Math.floor(numbers.length / 2);
      return numbers.length % 2 ? numbers[middle] : (numbers[middle - 1] + numbers[middle]) / 2;
    }
    return null;
  }

  function initialRangeState(existing, lower, upper, valueType) {
    return existing || { min: lower, max: upper, valueType: valueType, touched: false };
  }

  function renderFilter(component) {
    const element = document.getElementById("report-component-" + component.id);
    const entries = rowsBySource[component.source] || [];
    if (!element) return;

    if (component.filter_type === "select") {
      const values = [];
      const seen = new Set();
      for (const entry of entries) {
        const value = entry.row[component.field];
        const key = valueKey(value);
        if (!seen.has(key)) {
          seen.add(key);
          values.push({ key: key, value: value });
        }
      }
      values.sort((a, b) => String(a.value).localeCompare(String(b.value), undefined, { numeric: true }));
      const state = filterState.get(component.id) || { values: new Set() };
      filterState.set(component.id, state);
      if (!component.multiple) {
        element.innerHTML =
          '<div class="report-panel report-control"><label>' + escapeHtml(component.label) + "</label>" +
          '<select><option value="">All</option>' +
          values.map((item) => '<option value="' + escapeHtml(item.key) + '">' + escapeHtml(item.value) + "</option>").join("") +
          "</select>" + (component.help_text ? '<div class="report-helper">' + escapeHtml(component.help_text) + "</div>" : "") + "</div>";
        const select = element.querySelector("select");
        select.addEventListener("change", function () {
          pauseAutoRefresh();
          state.values = new Set(Array.from(select.selectedOptions).map((option) => option.value).filter(Boolean));
          renderAll();
        });
        return;
      }

      const allLabel = "All " + String(component.label).toLowerCase();
      const summary = () => state.values.size === 0
        ? allLabel
        : state.values.size === 1
          ? String((values.find((item) => state.values.has(item.key)) || {}).value || "1 selected")
          : state.values.size + " selected";
      element.innerHTML =
        '<div class="report-panel report-control"><label>' + escapeHtml(component.label) + "</label>" +
        '<div class="multi-select"><button class="multi-select-toggle" type="button" aria-expanded="false"><span>' + escapeHtml(summary()) + "</span></button>" +
        '<div class="multi-select-menu" role="listbox" aria-multiselectable="true" hidden>' +
        values.map((item) => '<label class="multi-select-option"><input type="checkbox" value="' + escapeHtml(item.key) + '"' + (state.values.has(item.key) ? " checked" : "") + "><span>" + escapeHtml(item.value) + "</span></label>").join("") +
        '<button class="multi-select-clear" type="button">Clear selection</button></div></div>' +
        (component.help_text ? '<div class="report-helper">' + escapeHtml(component.help_text) + "</div>" : "") + "</div>";
      const toggle = element.querySelector(".multi-select-toggle");
      const menu = element.querySelector(".multi-select-menu");
      const updateSummary = () => { toggle.querySelector("span").textContent = summary(); };
      const closeMenu = () => { menu.hidden = true; toggle.setAttribute("aria-expanded", "false"); };
      toggle.addEventListener("click", function () {
        menu.hidden = !menu.hidden;
        toggle.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
      });
      menu.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => checkbox.addEventListener("change", function () {
        pauseAutoRefresh();
        state.values = new Set(Array.from(menu.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value));
        updateSummary();
        renderAll();
      }));
      menu.querySelector(".multi-select-clear").addEventListener("click", function () {
        pauseAutoRefresh();
        state.values.clear();
        menu.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => { checkbox.checked = false; });
        updateSummary();
        renderAll();
      });
      element.addEventListener("keydown", function (event) {
        if (event.key === "Escape") { closeMenu(); toggle.focus(); }
      });
      document.addEventListener("click", function (event) {
        if (!element.contains(event.target)) closeMenu();
      });
      return;
    }

    const valueType = inferRangeType(component, entries);
    const values = entries
      .map((entry) => comparable(entry.row[component.field], valueType))
      .filter((value) => value != null);
    const lower = values.length ? Math.min(...values) : null;
    const upper = values.length ? Math.max(...values) : null;
    const state = initialRangeState(filterState.get(component.id), lower, upper, valueType);
    state.valueType = valueType;
    filterState.set(component.id, state);
    const inputType = valueType === "date" ? "date" : valueType === "datetime" ? "datetime-local" : "number";
    const toInput = (value) => {
      if (value == null) return "";
      if (valueType === "date") return new Date(value).toISOString().slice(0, 10);
      if (valueType === "datetime") return new Date(value).toISOString().slice(0, 16);
      return String(value);
    };
    const limits = values.length
      ? ' min="' + escapeHtml(toInput(lower)) + '" max="' + escapeHtml(toInput(upper)) + '"'
      : "";
    const rangeLabels = valueType === "number" ? ["Minimum", "Maximum"] : ["Start date", "End date"];
    const available = values.length
      ? displayRangeValue(lower, valueType) + " to " + displayRangeValue(upper, valueType)
      : "No values";
    element.innerHTML =
      '<div class="report-panel report-control"><div class="control-label">' + escapeHtml(component.label) + "</div>" +
      '<div class="range-inputs"><label class="range-field"><span>' + rangeLabels[0] + '</span><input class="range-min" type="' + inputType + '" value="' + escapeHtml(toInput(state.min)) + '"' + limits + "></label>" +
      '<label class="range-field"><span>' + rangeLabels[1] + '</span><input class="range-max" type="' + inputType + '" value="' + escapeHtml(toInput(state.max)) + '"' + limits + "></label></div>" +
      '<div class="report-status">Available: ' + escapeHtml(available) + "</div>" +
      (component.help_text ? '<div class="report-helper">' + escapeHtml(component.help_text) + "</div>" : "") + "</div>";
    const update = function () {
      pauseAutoRefresh();
      state.touched = true;
      const minInput = element.querySelector(".range-min");
      const maxInput = element.querySelector(".range-max");
      const minValue = minInput.value;
      const maxValue = maxInput.value;
      const minimum = minValue ? comparable(minValue, valueType, "min") : null;
      const maximum = maxValue ? comparable(maxValue, valueType, "max") : null;
      if (minimum != null && maximum != null && minimum > maximum) {
        minInput.value = maxValue;
        maxInput.value = minValue;
        state.min = comparable(maxValue, valueType, "min");
        state.max = comparable(minValue, valueType, "max");
      } else {
        state.min = minimum;
        state.max = maximum;
      }
      renderAll();
    };
    element.querySelector(".range-min").addEventListener("change", update);
    element.querySelector(".range-max").addEventListener("change", update);
  }

  function renderMetric(component) {
    const element = document.getElementById("report-component-" + component.id);
    if (!element) return;
    const entries = filteredEntries(component.source, component.id);
    const value = aggregate(entries, component.field, component.aggregate);
    element.innerHTML =
      '<div class="report-panel metric-card"><div class="label">' + escapeHtml(component.label) + "</div>" +
      '<div class="value">' + escapeHtml(formatValue(value, component)) + "</div>" +
      '<div class="context">' + entries.length + " matching rows</div></div>";
  }

  function groupedRows(entries, colorField) {
    const groups = new Map();
    for (const entry of entries) {
      const color = colorField ? entry.row[colorField] : "";
      const key = valueKey(color);
      if (!groups.has(key)) groups.set(key, { name: color, entries: [] });
      groups.get(key).entries.push(entry);
    }
    return Array.from(groups.values());
  }

  function groupColor(component, groupName) {
    const colorMap = component.color_map || {};
    return colorMap[String(groupName)] || null;
  }

  function aggregatePoints(entries, component) {
    const encodings = component.encodings || {};
    const points = new Map();
    for (const entry of entries) {
      const x = entry.row[component.x];
      const key = valueKey(x);
      if (!points.has(key)) points.set(key, { x: x, entries: [] });
      points.get(key).entries.push(entry);
    }
    return Array.from(points.values()).map((point) => ({
      x: point.x,
      y: aggregate(point.entries, component.y, component.aggregate),
      ids: point.entries.map((entry) => entry.id),
      text: encodings.text ? point.entries[0].row[encodings.text] : null,
      size: encodings.size ? aggregate(point.entries, encodings.size, "mean") : null,
    }));
  }

  function histogramTrace(component, entries) {
    const values = entries
      .map((entry) => ({ entry: entry, value: finiteNumber(entry.row[component.x]) }))
      .filter((item) => item.value != null);
    if (!values.length) return { type: "bar", x: [], y: [], customdata: [] };
    const minimum = Math.min(...values.map((item) => item.value));
    const maximum = Math.max(...values.map((item) => item.value));
    const binCount = maximum === minimum ? 1 : Math.max(1, component.bins || 30);
    const width = maximum === minimum ? 1 : (maximum - minimum) / binCount;
    const bins = Array.from({ length: binCount }, (_, index) => ({
      center: maximum === minimum ? minimum : minimum + width * (index + 0.5),
      entries: [],
    }));
    for (const item of values) {
      const index = maximum === minimum ? 0 : Math.min(binCount - 1, Math.floor((item.value - minimum) / width));
      bins[index].entries.push(item.entry);
    }
    return {
      type: "bar",
      x: bins.map((bin) => bin.center),
      y: bins.map((bin) => bin.entries.length),
      width: maximum === minimum ? Math.max(1, Math.abs(minimum) * 0.02) : width * 0.92,
      customdata: bins.map((bin) => bin.entries.map((entry) => entry.id)),
      marker: { color: "#d4a845", opacity: 0.82 },
      name: component.title,
      hovertemplate: "%{x}<br>Count: %{y}<extra></extra>",
    };
  }

  function treemapTrace(component, entries) {
    const encodings = component.encodings || {};
    const points = entries
      .map((entry) => ({
        id: entry.id,
        label: entry.row[component.x],
        value: finiteNumber(entry.row[component.y]),
        color: component.color ? finiteNumber(entry.row[component.color]) : null,
      }))
      .filter((point) => point.label != null && Number.isFinite(point.value) && point.value > 0);
    const colorValues = points.map((point) => point.color).filter(Number.isFinite);
    const colorLimit = Math.max(0, ...colorValues.map((value) => Math.abs(value))) || 1;
    const valueConfig = {
      format: encodings.value_format || ",.0f",
      prefix: encodings.value_prefix || "",
      suffix: encodings.value_suffix || "",
    };
    const colorConfig = {
      format: encodings.color_format || ".2f",
      prefix: encodings.color_prefix || "",
      suffix: encodings.color_suffix || "",
    };
    const valueLabel = encodings.value_label || String(component.y).replaceAll("_", " ");
    const colorLabel = encodings.color_label || String(component.color || "color").replaceAll("_", " ");
    const totalValue = points.reduce((sum, point) => sum + point.value, 0);
    const transparent = "rgba(0,0,0,0)";
    const tileLineColor = document.documentElement.classList.contains("light") ? "#d8dce8" : "rgba(15, 23, 42, 0.82)";
    let rootId = "__condor_treemap_root__:" + (component.id || "chart");
    while (points.some((point) => String(point.id) === rootId)) rootId += "_";
    const formattedColors = points.map((point) => {
      if (!Number.isFinite(point.color)) return "N/A";
      const formatted = formatValue(point.color, colorConfig);
      return point.color > 0 ? "+" + formatted : formatted;
    });
    const trace = {
      type: "treemap",
      ids: [rootId, ...points.map((point) => point.id)],
      labels: ["", ...points.map((point) => point.label)],
      parents: ["", ...points.map(() => rootId)],
      values: [totalValue, ...points.map((point) => point.value)],
      branchvalues: "total",
      customdata: [null, ...points.map((point) => point.id)],
      text: ["", ...formattedColors],
      texttemplate: ["", ...points.map((point) => (
        component.color && point.value / totalValue >= 0.02
          ? "<b>%{label}</b><br><b>%{text}</b>"
          : "<b>%{label}</b>"
      ))],
      textposition: "middle center",
      hovertext: ["", ...points.map((point, index) => {
        const details = [String(point.label), valueLabel + ": " + formatValue(point.value, valueConfig)];
        if (component.color) details.push(colorLabel + ": " + formattedColors[index]);
        return details.join("<br>");
      })],
      hovertemplate: "%{hovertext}<extra></extra>",
      pathbar: { visible: false },
      tiling: { pad: 3 },
      textfont: { color: "#ffffff", size: compactChartLayout ? 15 : 20 },
      marker: {
        line: {
          color: [transparent, ...points.map(() => tileLineColor)],
          width: [0, ...points.map(() => 2)],
        },
      },
    };
    if (component.color) {
      trace.marker.colors = [transparent, ...points.map((point) => Number.isFinite(point.color) ? point.color : 0)];
      trace.marker.colorscale = [
        [0, "#ea3943"],
        [0.5, "#475569"],
        [1, "#16c784"],
      ];
      trace.marker.cmin = -colorLimit;
      trace.marker.cmax = colorLimit;
      trace.marker.cmid = 0;
      trace.marker.colorbar = {
        title: { text: colorLabel },
        orientation: "h",
        x: 0.5,
        y: -0.1,
        len: 0.5,
        thickness: 10,
      };
    } else {
      trace.marker.colors = [transparent, ...points.map(() => "#d4a845")];
    }
    return trace;
  }

  function chartTraces(component, entries) {
    if (component.chart_type === "treemap") return [treemapTrace(component, entries)];
    if (component.chart_type === "histogram") return [histogramTrace(component, entries)];
    if (component.chart_type === "candlestick") {
      const encodings = component.encodings || {};
      const trace = {
        type: "candlestick",
        x: entries.map((entry) => entry.row[component.x]),
        open: entries.map((entry) => entry.row[encodings.open]),
        high: entries.map((entry) => entry.row[encodings.high]),
        low: entries.map((entry) => entry.row[encodings.low]),
        close: entries.map((entry) => entry.row[encodings.close]),
        customdata: entries.map((entry) => entry.id),
        name: component.title,
        increasing: { line: { color: "#22c55e" } },
        decreasing: { line: { color: "#ef4444" } },
      };
      const traces = [trace];
      for (const overlay of encodings.overlays || []) {
        traces.push({
          type: "scatter",
          mode: "lines",
          x: entries.map((entry) => entry.row[component.x]),
          y: entries.map((entry) => entry.row[overlay.y]),
          customdata: entries.map((entry) => entry.id),
          name: overlay.label || overlay.y,
          line: overlay.line || {},
        });
      }
      return traces;
    }

    if (component.chart_type === "box") {
      return groupedRows(entries, component.color).map((group) => {
        const color = groupColor(component, group.name);
        const hoverFields = (component.encodings && component.encodings.hover_fields) || [];
        const valueLabel = component.y_label || String(component.y).replaceAll("_", " ");
        return {
          type: "box",
          x: group.entries.map((entry) => entry.row[component.x]),
          y: group.entries.map((entry) => entry.row[component.y]),
          customdata: group.entries.map((entry) => entry.id),
          text: group.entries.map((entry) => component.selection_field ? entry.row[component.selection_field] : ""),
          hovertext: group.entries.map((entry) => hoverFields.map((field) => {
            const label = String(field).replaceAll("_", " ");
            return label + ": " + String(entry.row[field] ?? "");
          }).join("<br>")),
          name: component.color ? String(group.name) : component.title,
          boxpoints: "all",
          jitter: 0,
          pointpos: 0,
          marker: color ? { color: color, size: 7, opacity: 0.78 } : { size: 7, opacity: 0.78 },
          line: color ? { color: color } : {},
          fillcolor: color || undefined,
          opacity: 0.72,
          hovertemplate: hoverFields.length
            ? "%{hovertext}<extra>%{fullData.name}</extra>"
            : component.selection_field
              ? "%{text}<br>%{x}<br>" + valueLabel + ": %{y}<extra>%{fullData.name}</extra>"
              : undefined,
        };
      });
    }

    return groupedRows(entries, component.color).map((group) => {
      const encodings = component.encodings || {};
      const points = component.aggregate ? aggregatePoints(group.entries, component) : group.entries.map((entry) => ({
        x: entry.row[component.x],
        y: entry.row[component.y],
        ids: entry.id,
        text: encodings.text ? entry.row[encodings.text] : null,
        size: encodings.size ? Number(entry.row[encodings.size]) : null,
      }));
      const horizontal = component.chart_type === "horizontal_bar";
      const color = groupColor(component, group.name);
      const trace = {
        type: ["bar", "horizontal_bar"].includes(component.chart_type) ? "bar" : "scatter",
        x: points.map((point) => horizontal ? point.y : point.x),
        y: points.map((point) => horizontal ? point.x : point.y),
        customdata: points.map((point) => point.ids),
        name: component.color ? String(group.name) : component.title,
      };
      if (trace.type === "scatter") trace.mode = component.chart_type === "scatter" ? "markers" : "lines";
      if (horizontal) trace.orientation = "h";
      if (component.chart_type === "area") trace.fill = "tozeroy";
      if (color) {
        trace.marker = { ...(trace.marker || {}), color: color };
        trace.line = { ...(trace.line || {}), color: color };
      }
      const hasMarkerSizes = component.chart_type === "scatter" && points.some((point) => Number.isFinite(point.size) && point.size > 0);
      if (hasMarkerSizes) {
        trace.marker = {
          ...(trace.marker || {}),
          size: points.map((point) => Number.isFinite(point.size) && point.size > 0 ? point.size : 8),
          sizemode: "diameter",
        };
      }
      if (points.some((point) => point.text != null)) {
        trace.text = points.map((point) => point.text);
        if (component.chart_type === "scatter") {
          trace.mode = "markers+text";
          trace.textposition = encodings.text_position || "top center";
        } else if (["line", "area"].includes(component.chart_type)) {
          trace.mode = "lines+text";
          trace.textposition = encodings.text_position || "top center";
        } else {
          const negativeHorizontal = horizontal && points.every((point) => Number(point.y) < 0);
          trace.textposition = negativeHorizontal ? "inside" : "outside";
          if (negativeHorizontal) {
            trace.insidetextanchor = "middle";
            trace.textfont = { color: "#f8fafc" };
          }
        }
        trace.cliponaxis = false;
      }
      return trace;
    });
  }

  function selectedIds(event) {
    const ids = new Set();
    for (const point of event && event.points ? event.points : []) {
      const data = point.customdata;
      if (Array.isArray(data)) data.forEach((id) => ids.add(id));
      else if (data != null) ids.add(data);
    }
    return ids;
  }

  function setSelection(source, ids, origin, mode) {
    pauseAutoRefresh();
    if (ids.size) selectionState.set(source, { ids: ids, origin: origin, mode: mode || "filter" });
    else selectionState.delete(source);
    renderAll(origin);
    const component = components.find((item) => item.id === origin);
    if (component) renderSelectionStatus(component);
  }

  function renderSelectionStatus(component) {
    if (!component.selection_label) return;
    const element = document.getElementById("report-component-" + component.id);
    const status = element && element.querySelector(".selection-status");
    if (!status) return;
    const selection = selectionState.get(component.source);
    const ids = selection && selection.origin === component.id ? selection.ids : new Set();
    const noun = String(component.selection_label);
    const plural = noun.endsWith("s") ? noun : noun + "s";
    const selectedEntries = (rowsBySource[component.source] || []).filter((entry) => ids.has(entry.id));
    const field = component.selection_field || component.x;
    const labels = Array.from(new Set(selectedEntries.map((entry) => String(entry.row[field] ?? "")).filter(Boolean)));
    if (!ids.size) {
      status.innerHTML = '<div class="selection-summary">No ' + escapeHtml(noun) + ' selected</div>';
      return;
    }
    const chips = labels.slice(0, 3).map((label) => '<span class="selection-chip">' + escapeHtml(label) + "</span>").join("");
    const remaining = labels.length > 3 ? '<span class="selection-chip">+' + (labels.length - 3) + " more</span>" : "";
    status.innerHTML = '<div class="selection-summary"><strong>' + ids.size + " " + escapeHtml(ids.size === 1 ? noun : plural) + " selected</strong>" + chips + remaining + '</div><button class="selection-clear" type="button">Clear ' + escapeHtml(noun) + " selection</button>";
    status.querySelector(".selection-clear").addEventListener("click", function () {
      pauseAutoRefresh();
      selectionState.delete(component.source);
      renderAll();
    });
  }

  function applyCategoryOrder(layout, component, horizontal) {
    if (!component.category_order || !component.category_order.length) return;
    const categoryAxis = horizontal ? layout.yaxis : layout.xaxis;
    categoryAxis.categoryorder = "array";
    categoryAxis.categoryarray = component.category_order;
  }

  function applyAxisEncodings(layout, component) {
    const encodings = component.encodings || {};
    const scales = new Set(["linear", "log", "date", "category"]);
    if (Array.isArray(encodings.x_range)) layout.xaxis.range = encodings.x_range;
    if (Array.isArray(encodings.y_range)) layout.yaxis.range = encodings.y_range;
    if (scales.has(encodings.x_scale)) layout.xaxis.type = encodings.x_scale;
    if (scales.has(encodings.y_scale)) layout.yaxis.type = encodings.y_scale;
  }

  function referenceLineShape(line) {
    const axis = line.axis === "x" ? "x" : "y";
    return {
      type: "line",
      xref: axis === "x" ? "x" : "paper",
      yref: axis === "y" ? "y" : "paper",
      x0: axis === "x" ? line.value : 0,
      x1: axis === "x" ? line.value : 1,
      y0: axis === "y" ? line.value : 0,
      y1: axis === "y" ? line.value : 1,
      line: { color: line.color || "#94a3b8", width: line.width || 1, dash: line.dash || "dash" },
      name: line.label || "Reference line",
      showlegend: Boolean(line.label),
    };
  }

  function renderChart(component) {
    const element = document.getElementById("report-component-" + component.id);
    if (!element || !window.Plotly) return;
    let chart = element.querySelector(".report-chart");
    if (!chart) {
      element.innerHTML = '<div class="report-panel"><div class="report-chart"></div>' +
        (component.selection_help ? '<div class="report-helper selection-help">' + escapeHtml(component.selection_help) + "</div>" : "") +
        (component.selection_label ? '<div class="selection-status"></div>' : "") + "</div>";
      chart = element.querySelector(".report-chart");
    }
    chart.style.height = String(component.height || 420) + "px";
    const entries = filteredEntries(component.source, component.id);
    const light = document.documentElement.classList.contains("light");
    const horizontal = component.chart_type === "horizontal_bar";
    const treemap = component.chart_type === "treemap";
    const referenceLines = (component.encodings && component.encodings.reference_lines) || [];
    const layout = {
      title: { text: component.title, font: { size: 15 } },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: light ? "#141727" : "#e8e6e3", size: 11 },
      colorway: light
        ? ["#b8860b", "#2563eb", "#7c3aed", "#0891b2", "#64748b"]
        : ["#d4a845", "#56b4e9", "#a78bfa", "#22d3ee", "#94a3b8"],
      margin: {
        l: treemap ? 8 : horizontal ? (compactChartLayout ? 105 : 170) : (component.y_label ? 70 : 55),
        r: treemap ? 8 : 25,
        t: 55,
        b: treemap ? 75 : component.x_label ? 65 : 50,
      },
      hovermode: "closest",
      dragmode: treemap ? false : ["scatter", "box"].includes(component.chart_type) ? "lasso" : "zoom",
      uirevision: component.id + ":" + uiRevision,
      xaxis: { gridcolor: light ? "#e5e7eb" : "#1c2541", title: component.x_label ? { text: component.x_label } : undefined },
      yaxis: { gridcolor: light ? "#e5e7eb" : "#1c2541", title: component.y_label ? { text: component.y_label } : undefined },
      showlegend: !treemap && Boolean(component.color || component.chart_type === "candlestick" || referenceLines.some((line) => line.label)),
      barmode: "group",
      boxmode: "group",
    };
    if (treemap) layout.uniformtext = { minsize: compactChartLayout ? 13 : 16, mode: "hide" };
    applyCategoryOrder(layout, component, horizontal);
    applyAxisEncodings(layout, component);
    if (component.chart_type === "candlestick") layout.xaxis.rangeslider = { visible: false };
    for (const line of referenceLines) {
      layout.shapes = layout.shapes || [];
      layout.shapes.push(referenceLineShape(line));
    }
    renderSelectionStatus(component);
    window.Plotly.react(chart, chartTraces(component, entries), layout, {
      responsive: true,
      displaylogo: false,
    }).then(function () {
      if (typeof chart.on !== "function") return;
      if (typeof chart.removeAllListeners === "function") chart.removeAllListeners();
      chart.on("plotly_relayouting", pauseAutoRefresh);
      chart.on("plotly_relayout", pauseAutoRefresh);
      chart.on("plotly_legendclick", pauseAutoRefresh);
      if (!component.cross_filter) return;
      chart.on("plotly_selected", (event) => setSelection(component.source, selectedIds(event), component.id, component.selection_mode));
      chart.on("plotly_click", (event) => setSelection(component.source, selectedIds(event), component.id, component.selection_mode));
      chart.on("plotly_deselect", () => setSelection(component.source, new Set(), component.id, component.selection_mode));
    });
  }

  function normalizeColumns(component, entries) {
    const configured = component.columns && component.columns.length
      ? component.columns
      : (entries[0] ? Object.keys(entries[0].row) : []);
    return configured.map((column) => typeof column === "string"
      ? { field: column, label: column.replaceAll("_", " ") }
      : { ...column, field: column.field || column.name, label: column.label || column.field || column.name });
  }

  function csvCell(value) {
    let text = String(value ?? "");
    if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text;
  }

  function downloadCsv(component, columns, entries) {
    const lines = [columns.map((column) => csvCell(column.label)).join(",")];
    for (const entry of entries) {
      lines.push(columns.map((column) => csvCell(entry.row[column.field])).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = (component.title || component.id).replace(/[^a-z0-9_-]+/gi, "_") + ".csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function renderTable(component, drilldown) {
    const element = document.getElementById("report-component-" + component.id);
    if (!element) return;
    if (drilldown && !selectionState.has(component.source)) {
      element.innerHTML = '<div class="report-panel"><h2>' + escapeHtml(component.title) + '</h2><div class="empty-state">Select chart points to inspect their source rows.</div></div>';
      return;
    }

    let entries = filteredEntries(component.source, component.id);
    const columns = normalizeColumns(component, entries.length ? entries : (rowsBySource[component.source] || []));
    const state = tableState.get(component.id) || { search: "", page: 0, sortField: null, sortDirection: 1 };
    tableState.set(component.id, state);
    if (state.search) {
      const query = state.search.toLowerCase();
      entries = entries.filter((entry) => columns.some((column) => String(entry.row[column.field] ?? "").toLowerCase().includes(query)));
    }
    if (state.sortField) {
      entries = entries.slice().sort((a, b) => {
        const left = a.row[state.sortField];
        const right = b.row[state.sortField];
        if (left == null) return 1;
        if (right == null) return -1;
        const result = typeof left === "number" && typeof right === "number"
          ? left - right
          : String(left).localeCompare(String(right), undefined, { numeric: true });
        return result * state.sortDirection;
      });
    }
    const pageSize = component.page_size || 25;
    const pageCount = Math.max(1, Math.ceil(entries.length / pageSize));
    state.page = Math.min(state.page, pageCount - 1);
    const pageRows = entries.slice(state.page * pageSize, (state.page + 1) * pageSize);
    const toolbar =
      '<div class="table-toolbar">' +
      (component.searchable ? '<input class="table-search" type="search" placeholder="Search rows" value="' + escapeHtml(state.search) + '">' : "") +
      '<button class="table-export" type="button">Export CSV</button></div>';
    const header = columns.map((column) => {
      const marker = state.sortField === column.field ? (state.sortDirection > 0 ? " ▲" : " ▼") : "";
      return '<th data-field="' + escapeHtml(column.field) + '">' + escapeHtml(column.label) + marker + "</th>";
    }).join("");
    const body = pageRows.map((entry) => "<tr>" + columns.map((column) =>
      "<td>" + escapeHtml(formatValue(entry.row[column.field], column)) + "</td>"
    ).join("") + "</tr>").join("");
    element.innerHTML =
      '<div class="report-panel"><h2>' + escapeHtml(component.title || "Data") + "</h2>" + toolbar +
      '<div class="table-wrap"><table class="data-table"><thead><tr>' + header + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      '<div class="table-footer"><span>' + entries.length + " rows</span><span>" +
      '<button class="page-prev" type="button">Previous</button> Page ' + (state.page + 1) + " of " + pageCount +
      ' <button class="page-next" type="button">Next</button></span></div></div>';

    const search = element.querySelector(".table-search");
    if (search) search.addEventListener("input", function () {
      pauseAutoRefresh();
      const cursor = search.selectionStart;
      state.search = search.value;
      state.page = 0;
      renderTable(component, drilldown);
      const nextSearch = element.querySelector(".table-search");
      if (nextSearch) {
        nextSearch.focus();
        if (cursor != null) nextSearch.setSelectionRange(cursor, cursor);
      }
    });
    element.querySelector(".table-export").addEventListener("click", () => downloadCsv(component, columns, entries));
    element.querySelectorAll("th[data-field]").forEach((headerCell) => headerCell.addEventListener("click", function () {
      if (!component.sortable) return;
      pauseAutoRefresh();
      const field = headerCell.dataset.field;
      if (state.sortField === field) state.sortDirection *= -1;
      else {
        state.sortField = field;
        state.sortDirection = 1;
      }
      renderTable(component, drilldown);
    }));
    element.querySelector(".page-prev").addEventListener("click", function () {
      const page = Math.max(0, state.page - 1);
      if (page !== state.page) pauseAutoRefresh();
      state.page = page;
      renderTable(component, drilldown);
    });
    element.querySelector(".page-next").addEventListener("click", function () {
      const page = Math.min(pageCount - 1, state.page + 1);
      if (page !== state.page) pauseAutoRefresh();
      state.page = page;
      renderTable(component, drilldown);
    });
  }

  function updateActions() {
    const actions = document.getElementById("report-actions");
    if (!actions || (!components.length && !autoRefreshEnabled)) return;
    const activeFilters = Array.from(filterState.values()).filter((state) =>
      (state.values && state.values.size) || (state.touched && (state.min != null || state.max != null))
    ).length;
    const selections = Array.from(selectionState.values()).reduce((total, state) => total + state.ids.size, 0);
    let liveControls = "";
    if (autoRefreshEnabled && offlineSnapshot) {
      liveControls = '<div class="live-controls"><span class="live-badge offline">Offline snapshot</span>' +
        '<span class="report-status">Auto-refresh is disabled for downloaded reports.</span></div>';
    } else if (autoRefreshEnabled) {
      const interval = autoRefreshSeconds + (autoRefreshSeconds === 1 ? " second" : " seconds");
      liveControls = '<div class="live-controls"><span class="live-badge">Live</span>' +
        '<span class="report-status">' + (autoRefreshPaused ? "Paused; refresh every " : "Refresh every ") + interval + "</span>" +
        '<button id="report-refresh-toggle" type="button">' + (autoRefreshPaused ? "Resume" : "Pause") + "</button></div>";
    }
    actions.classList.add("active");
    actions.innerHTML = (components.length ? '<button id="report-reset" type="button">Reset view</button>' +
      (selections ? '<button id="report-clear-selection" type="button">Clear selection</button>' : "") +
      '<span class="report-status">' + activeFilters + " active filters" +
      (selections ? " · " + selections + " selected rows" : "") + "</span>" : "") + liveControls;
    const refreshToggle = document.getElementById("report-refresh-toggle");
    if (refreshToggle) refreshToggle.addEventListener("click", function () {
      if (autoRefreshPaused) resumeAutoRefresh();
      else pauseAutoRefresh();
    });
    const clearSelection = document.getElementById("report-clear-selection");
    if (clearSelection) clearSelection.addEventListener("click", function () {
      pauseAutoRefresh();
      selectionState.clear();
      renderAll();
    });
    const reset = document.getElementById("report-reset");
    if (reset) reset.addEventListener("click", function () {
      pauseAutoRefresh();
      filterState.clear();
      selectionState.clear();
      tableState.clear();
      advanceUiRevision();
      initialize();
    });
  }

  function renderAll(skipComponentId) {
    for (const component of components) {
      if (component.id === skipComponentId) continue;
      if (component.type === "metric") renderMetric(component);
      else if (component.type === "chart") renderChart(component);
      else if (component.type === "data_table") renderTable(component, false);
      else if (component.type === "drilldown") renderTable(component, true);
    }
    updateActions();
  }

  function initialize() {
    for (const component of components) {
      if (component.type === "filter") renderFilter(component);
    }
    renderAll();
  }

  window.CondorReportRuntime = {
    pauseAutoRefresh: pauseAutoRefresh,
    setTheme: function () {
      for (const component of components) {
        if (component.type === "chart") renderChart(component);
      }
    },
    __test: {
      advanceUiRevision: advanceUiRevision,
      aggregate: aggregate,
      applyAxisEncodings: applyAxisEncodings,
      applyCategoryOrder: applyCategoryOrder,
      chartTraces: chartTraces,
      comparable: comparable,
      csvCell: csvCell,
      displayRangeValue: displayRangeValue,
      histogramTrace: histogramTrace,
      inferRangeType: inferRangeType,
      initialRangeState: initialRangeState,
      rangeMatches: rangeMatches,
      referenceLineShape: referenceLineShape,
    },
  };

  if (typeof window.addEventListener === "function") {
    window.addEventListener("resize", function () {
      const nextCompactLayout = window.innerWidth <= 800;
      if (nextCompactLayout === compactChartLayout) return;
      compactChartLayout = nextCompactLayout;
      for (const component of components) {
        if (component.type === "chart") renderChart(component);
      }
    });
  }

  initialize();
  startAutoRefresh();
})();
