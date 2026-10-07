// "+ Add new…" in the managed drop-downs: asks for a name and adds it as an option.
(function () {
  "use strict";

  const NEW = "__new__";
  document.querySelectorAll("select[data-new-option]").forEach(function (select) {
    let previous = select.value;
    select.addEventListener("focus", function () { previous = select.value; });
    select.addEventListener("change", function () {
      if (select.value !== NEW) {
        previous = select.value;
        return;
      }
      const name = (window.prompt("New " + select.dataset.newOption + ":") || "").trim();
      if (!name) {
        select.value = previous;
        return;
      }
      // Reuse an existing entry if the name only differs in upper/lower case.
      const existing = Array.prototype.find.call(select.options, function (o) {
        return o.value !== NEW && o.value.toLowerCase() === name.toLowerCase();
      });
      if (existing) {
        select.value = existing.value;
      } else {
        select.insertBefore(new Option(name, name, true, true),
                            select.querySelector('option[value="' + NEW + '"]'));
      }
      previous = select.value;
    });
  });
})();
