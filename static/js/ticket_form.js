(function () {
  var items = document.getElementById("items");
  var total = document.getElementById("id_items-TOTAL_FORMS");
  var tpl = document.getElementById("item-template");
  var addBtn = document.getElementById("add-item");
  if (!items || !total || !tpl) return;
  var max = parseInt(items.dataset.max, 10) || 20;

  function rows() { return items.querySelectorAll(".item-row"); }

  function refresh() {
    var n = rows().length;
    rows().forEach(function (r) {
      var rm = r.querySelector(".remove-item");
      if (rm) rm.classList.toggle("d-none", n === 1);
    });
    addBtn.disabled = n >= max;
  }

  // Переиндексация, чтобы индексы форм шли подряд после удаления строк.
  function reindex() {
    rows().forEach(function (row, i) {
      row.querySelectorAll("[name],[id],[for]").forEach(function (el) {
        ["name", "id", "for"].forEach(function (attr) {
          var v = el.getAttribute(attr);
          if (v) el.setAttribute(attr, v.replace(/items-(\d+|__prefix__)-/, "items-" + i + "-"));
        });
      });
    });
    total.value = rows().length;
  }

  addBtn.addEventListener("click", function () {
    if (rows().length >= max) return;
    items.insertAdjacentHTML("beforeend", tpl.innerHTML.replace(/__prefix__/g, rows().length));
    total.value = rows().length;
    refresh();
  });

  items.addEventListener("click", function (e) {
    if (!e.target.classList.contains("remove-item") || rows().length <= 1) return;
    e.target.closest(".item-row").remove();
    reindex();
    refresh();
  });

  // Поле «Где находитесь» нужно только если кабинета нет в списке.
  var sel = document.getElementById("id_location");
  var wrap = document.getElementById("location-text-wrap");
  var txt = document.getElementById("id_location_text");
  function toggleText() {
    if (!sel || !wrap) return;
    var show = sel.value === "other" || (txt && txt.value) || wrap.querySelector(".text-danger");
    wrap.classList.toggle("d-none", !show);
  }
  if (sel) { sel.addEventListener("change", toggleText); toggleText(); }
  refresh();
})();
