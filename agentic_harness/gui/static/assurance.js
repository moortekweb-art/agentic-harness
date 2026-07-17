(function () {
  "use strict";

  function reviewPayload(task) {
    const value = task?.metadata?.specification_review;
    return value && typeof value === "object" ? value : {};
  }

  function populateDialog(task, elements) {
    const review = reviewPayload(task);
    const amendment = review.kind === "amendment";
    elements.title.textContent = amendment
      ? "Approve changed completion conditions"
      : "Approve completion conditions";
    elements.help.textContent = review.reason || (
      amendment
        ? "Review the proposed change before the task continues."
        : "Review what the harness will require before the task starts."
    );
    const conditions = Array.isArray(review.conditions) ? review.conditions : [];
    elements.requirements.value = conditions
      .map((item) => String(item?.text || "").trim())
      .filter(Boolean)
      .join("\n");
    elements.submit.textContent = amendment ? "Approve change" : "Approve and start";
    elements.dialog.showModal();
    return {
      goal_id: String(review.goal_id || task?.id || ""),
      goal_spec_sha256: String(review.goal_spec_sha256 || ""),
      version: Number.isInteger(review.version) ? review.version : -1,
    };
  }

  function requirementsFromText(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  window.HarnessAssurance = Object.freeze({
    populateDialog,
    requirementsFromText,
    reviewPayload,
  });
})();
