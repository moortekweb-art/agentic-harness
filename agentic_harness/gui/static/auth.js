function restoreAuthToken() {
  state.authToken = sessionStorage.getItem(TOKEN_KEY) || "";
}

function clearAuthToken() {
  state.authToken = "";
  sessionStorage.removeItem(TOKEN_KEY);
}

function showTokenDialog() {
  if (state.authPromptPromise) return state.authPromptPromise;
  state.authPromptPromise = new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "token-dialog";
    dialog.innerHTML = `
      <form method="dialog">
        <div class="dialog-head">
          <h2>Access token required</h2>
          <button value="cancel" title="Cancel">${iconMarkup("x")}<span>Cancel</span></button>
        </div>
        <label class="field-label" for="authTokenInput">Token</label>
        <input id="authTokenInput" name="authTokenInput" type="password" autocomplete="off" />
        <div class="actions compact">
          <button class="primary" value="confirm">${iconMarkup("arrow-right")}<span>Continue</span></button>
        </div>
      </form>
    `;
    document.body.appendChild(dialog);
    const input = dialog.querySelector("input");
    dialog.addEventListener("close", () => {
      const value = dialog.returnValue === "confirm" && input ? input.value.trim() : "";
      dialog.remove();
      state.authPromptPromise = null;
      resolve(value);
    });
    dialog.showModal();
    if (input) input.focus();
  });
  return state.authPromptPromise;
}
