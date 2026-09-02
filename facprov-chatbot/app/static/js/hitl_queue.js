(function () {
  document.querySelectorAll(".hitl-approve-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const taskId = btn.dataset.taskId;
      const table = document.getElementById(`fields-${taskId}`);
      const edited = {};
      table.querySelectorAll("input[data-field]").forEach((input) => {
        edited[input.dataset.field] = input.value;
      });
      const resultDiv = document.getElementById(`result-${taskId}`);
      btn.disabled = true;
      try {
        const resp = await fetch(`/hitl/api/tasks/${taskId}/approve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ edited_payload: edited }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          resultDiv.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${data.error}</span>`;
          btn.disabled = false;
          return;
        }
        resultDiv.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i> Committed — provider #${data.entity_ref_id} (${data.status}) — refresh to update the list.</span>`;
      } catch (e) {
        resultDiv.innerHTML = `<span class="text-danger">Request failed: ${e}</span>`;
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll(".hitl-reject-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const taskId = btn.dataset.taskId;
      const resultDiv = document.getElementById(`result-${taskId}`);
      btn.disabled = true;
      const resp = await fetch(`/hitl/api/tasks/${taskId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_notes: "Rejected from review queue" }),
      });
      if (resp.ok) {
        resultDiv.innerHTML = `<span class="text-muted"><i class="bi bi-x-circle"></i> Rejected — refresh to update the list.</span>`;
      }
    });
  });
})();
