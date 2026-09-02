(function () {
  async function load() {
    let resp, data;
    try {
      resp = await fetch("/dashboard/api/summary", { headers: { Accept: "application/json" } });
      if (!resp.ok) return;
      data = await resp.json();
    } catch (e) {
      console.error("Dashboard: failed to load summary", e);
      return;
    }

    // Each section below is independently wrapped — one failing (e.g. a
    // CDN script that didn't load) must never block the others. This is
    // what actually broke before: an uncaught error inside the first
    // chart call silently aborted every step after it, including both
    // tables, even though they have nothing to do with charting.
    renderKpis(data);
    renderCharts(data);
    renderTables(data);
  }

  function renderKpis(data) {
    try {
      const llm = (data.totals && data.totals.llm) || {};
      document.getElementById("kpiLlmCalls").textContent = (llm.call_count || 0).toLocaleString();
      document.getElementById("kpiCost").textContent = "$" + Number(llm.total_cost_usd || 0).toFixed(4);
      document.getElementById("kpiLatency").textContent = Math.round(llm.avg_latency_ms || 0).toLocaleString();
      document.getElementById("kpiErrors").textContent = (llm.error_count || 0).toLocaleString();
    } catch (e) {
      console.error("Dashboard: failed to render KPIs", e);
    }
  }

  function renderCharts(data) {
    if (typeof Chart === "undefined") {
      console.error("Dashboard: Chart.js did not load (blocked script or network issue) — skipping charts");
      showChartFallback("costChart", "Chart library failed to load.");
      showChartFallback("callsChart", "Chart library failed to load.");
      return;
    }

    const daily = data.daily || [];
    const labels = daily.map((d) => d.summary_date);

    try {
      new Chart(document.getElementById("costChart").getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [{
            label: "Cost (USD)",
            data: daily.map((d) => d.total_cost_usd),
            borderColor: "#3b5bfd",
            backgroundColor: "rgba(59,91,253,0.15)",
            fill: true,
            tension: 0.25,
          }],
        },
        options: { responsive: true, plugins: { legend: { display: false } } },
      });
    } catch (e) {
      console.error("Dashboard: failed to render cost chart", e);
      showChartFallback("costChart", "Unable to render this chart.");
    }

    try {
      new Chart(document.getElementById("callsChart").getContext("2d"), {
        type: "bar",
        data: {
          labels,
          datasets: [
            { label: "LLM Calls", data: daily.map((d) => d.total_llm_calls), backgroundColor: "#3b5bfd" },
            { label: "HTTP Calls", data: daily.map((d) => d.total_http_calls), backgroundColor: "#9fb0ff" },
          ],
        },
        options: { responsive: true },
      });
    } catch (e) {
      console.error("Dashboard: failed to render calls chart", e);
      showChartFallback("callsChart", "Unable to render this chart.");
    }
  }

  function showChartFallback(canvasId, message) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !canvas.parentNode) return;
    const note = document.createElement("div");
    note.className = "text-muted small p-3";
    note.textContent = message;
    canvas.replaceWith(note);
  }

  function renderTables(data) {
    try {
      const llmBody = document.getElementById("llmCallsBody");
      (data.recent_llm_calls || []).forEach((c) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${c.operation}</td><td>${c.model_name}</td>
          <td><span class="badge ${c.call_status === 'SUCCESS' ? 'bg-success' : 'bg-danger'}">${c.call_status}</span></td>
          <td>${(c.prompt_tokens || 0) + (c.completion_tokens || 0)}</td>
          <td>$${Number(c.total_cost_usd || 0).toFixed(6)}</td>
          <td>${c.latency_ms ?? "—"} ms</td>
          <td>${c.created_at}</td>`;
        llmBody.appendChild(tr);
      });
    } catch (e) {
      console.error("Dashboard: failed to render LLM calls table", e);
    }

    try {
      const httpBody = document.getElementById("httpCallsBody");
      (data.recent_http_calls || []).forEach((c) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${c.http_method}</td><td>${c.endpoint}</td>
          <td><span class="badge ${c.response_status && c.response_status < 400 ? 'bg-success' : 'bg-danger'}">${c.response_status ?? "—"}</span></td>
          <td>${c.latency_ms ?? "—"} ms</td>
          <td>${c.created_at}</td>`;
        httpBody.appendChild(tr);
      });
    } catch (e) {
      console.error("Dashboard: failed to render HTTP calls table", e);
    }
  }

  load();
})();