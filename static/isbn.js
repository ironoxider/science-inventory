// Textbook form: look up an ISBN (typed, scanned with a USB scanner, or read from a photo)
// and fill in the empty fields with the book's details.
(function () {
  "use strict";

  const button = document.querySelector("[data-isbn-lookup]");
  if (!button) return;
  const form = button.closest("form");
  const input = form.elements["isbn"];
  const statusEl = form.querySelector(".isbn-status");

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "isbn-status small-note " + (kind || "");
  }

  async function lookup() {
    const isbn = (input.value || "").trim();
    if (!isbn) {
      setStatus("Type or scan an ISBN first.", "error");
      return;
    }
    button.disabled = true;
    setStatus("Looking up " + isbn + "…", "muted");
    try {
      const resp = await fetch(button.dataset.url + "?isbn=" + encodeURIComponent(isbn));
      const body = await resp.json();
      if (!resp.ok) throw new Error(body.error || "Lookup failed.");
      input.value = body.isbn;
      const filled = [];
      Object.keys(body.fields).forEach(function (name) {
        const el = form.elements[name];
        if (el && !el.value.trim()) {
          el.value = body.fields[name];
          el.classList.add("autofilled");
          filled.push(name);
        }
      });
      setStatus(filled.length ? "Filled in " + filled.join(", ") + ". Please check them."
                              : "Found the book; the fields were already filled in.", "success");
    } catch (e) {
      setStatus(e.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener("click", lookup);
  // A USB barcode scanner types the ISBN and presses Enter: look it up instead of submitting.
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      lookup();
    }
  });
  document.addEventListener("isbn-found", lookup);
})();
