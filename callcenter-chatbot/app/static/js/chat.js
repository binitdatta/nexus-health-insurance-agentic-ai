(function () {
  const chatWindow = document.getElementById("chatWindow");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");
  const turnCostBadge = document.getElementById("turnCostBadge");

  function appendBubble(role, html) {
    const wrap = document.createElement("div");
    wrap.className = role === "user" ? "d-flex justify-content-end" : "d-flex justify-content-start";
    const bubble = document.createElement("div");
    bubble.className = (role === "user" ? "chat-bubble-user" : "chat-bubble-assistant") + " p-3 mb-3";
    bubble.style.maxWidth = "80%";
    bubble.innerHTML = html;
    wrap.appendChild(bubble);
    chatWindow.appendChild(wrap);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    return bubble;
  }

  function renderTable(container, spec) {
    const tpl = document.getElementById("tableTemplate").content.cloneNode(true);
    const thead = tpl.querySelector("thead");
    const tbody = tpl.querySelector("tbody");
    const headRow = document.createElement("tr");
    (spec.columns || []).forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    (spec.rows || []).forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => {
        const td = document.createElement("td");
        td.textContent = cell === null || cell === undefined ? "" : cell;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    container.appendChild(tpl);
  }

  function renderChart(container, spec) {
    const canvasWrap = document.createElement("div");
    canvasWrap.className = "mt-2 bg-white p-2 rounded border";
    canvasWrap.style.maxWidth = "560px";
    const canvas = document.createElement("canvas");
    canvasWrap.appendChild(canvas);
    container.appendChild(canvasWrap);

    const chartType = spec.chart_type === "pie" ? "pie" : (spec.chart_type || "bar");
    new Chart(canvas.getContext("2d"), {
      type: chartType,
      data: {
        labels: spec.labels || [],
        datasets: (spec.series || []).map((s, i) => ({
          label: s.name || `Series ${i + 1}`,
          data: s.values || [],
          backgroundColor: ["#3b5bfd", "#2f7bff", "#7c94ff", "#0a1240", "#9fb0ff"],
          borderColor: "#3b5bfd",
          borderWidth: chartType === "line" ? 2 : 1,
          fill: chartType === "line" ? false : true,
        })),
      },
      options: { responsive: true, plugins: { legend: { display: (spec.series || []).length > 1 } } },
    });
  }

  function renderHitl(container, hitl) {
    const tpl = document.getElementById("hitlTemplate").content.cloneNode(true);
    const card = tpl.querySelector(".hitl-card") || tpl.firstElementChild;
    tpl.querySelector(".hitl-rationale").textContent = hitl.rationale || "";

    const fieldsTable = tpl.querySelector(".hitl-fields");
    const inputs = {};
    Object.entries(hitl.proposed_payload || {}).forEach(([key, value]) => {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.className = "text-nowrap";
      th.textContent = key;
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "form-control form-control-sm";
      input.value = value === null || value === undefined ? "" : value;
      input.dataset.field = key;
      inputs[key] = input;
      td.appendChild(input);
      tr.appendChild(th);
      tr.appendChild(td);
      fieldsTable.appendChild(tr);
    });
    (hitl.missing_fields || []).forEach((field) => {
      if (inputs[field]) return;
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.className = "text-nowrap text-warning";
      th.textContent = field + " *";
      const td = document.createElement("td");
      const input = document.createElement("input");
      input.type = "text";
      input.className = "form-control form-control-sm border-warning";
      input.placeholder = "required — please fill in";
      input.dataset.field = field;
      inputs[field] = input;
      td.appendChild(input);
      tr.appendChild(th);
      tr.appendChild(td);
      fieldsTable.appendChild(tr);
    });

    if (hitl.missing_fields && hitl.missing_fields.length) {
      tpl.querySelector(".hitl-missing").textContent =
        "The AI left these fields for you to confirm before approving: " + hitl.missing_fields.join(", ");
    }

    const resultDiv = tpl.querySelector(".hitl-result");
    const approveBtn = tpl.querySelector(".hitl-approve");
    const rejectBtn = tpl.querySelector(".hitl-reject");

    approveBtn.addEventListener("click", async () => {
      const edited = {};
      Object.entries(inputs).forEach(([field, input]) => { edited[field] = input.value; });
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      try {
        const resp = await fetch(`/hitl/api/tasks/${hitl.task_id}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ edited_payload: edited }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          resultDiv.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${data.error}</span>`;
          approveBtn.disabled = false;
          rejectBtn.disabled = false;
          return;
        }
        resultDiv.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i> Committed — call #${data.entity_ref_id} (${data.status})</span>`;
      } catch (e) {
        resultDiv.innerHTML = `<span class="text-danger">Request failed: ${e}</span>`;
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    rejectBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      const resp = await fetch(`/hitl/api/tasks/${hitl.task_id}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_notes: "Rejected from chat" }),
      });
      if (resp.ok) {
        resultDiv.innerHTML = `<span class="text-muted"><i class="bi bi-x-circle"></i> Rejected</span>`;
      }
    });

    container.appendChild(tpl);
  }

  async function sendMessage(message) {
    appendBubble("user", escapeHtml(message));
    const thinkingBubble = appendBubble("assistant", '<span class="text-muted"><span class="spinner-border spinner-border-sm me-2"></span>Thinking…</span>');

    let resp, data;
    try {
      resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      data = await resp.json();
    } catch (e) {
      thinkingBubble.innerHTML = `<span class="text-danger">Request failed: ${e}</span>`;
      return;
    }

    if (!resp.ok) {
      thinkingBubble.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${data.error || "Something went wrong"}</span>`;
      return;
    }

    thinkingBubble.innerHTML = window.marked ? marked.parse(data.answer_markdown || "") : escapeHtml(data.answer_markdown || "");

    const render = data.render || { type: "none" };
    if (render.type === "table" && render.spec) {
      renderTable(thinkingBubble, render.spec);
    } else if (render.type === "chart" && render.spec) {
      renderChart(thinkingBubble, render.spec);
    }

    if (data.hitl) {
      renderHitl(thinkingBubble, data.hitl);
    }

    if (data.turn_cost_usd) {
      turnCostBadge.style.display = "inline-block";
      turnCostBadge.textContent = `Last turn: $${data.turn_cost_usd.toFixed(6)} · ${data.turn_tokens} tokens`;
    }

    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    chatInput.value = "";
    sendMessage(message);
  });
})();
