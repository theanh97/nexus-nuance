(function () {
  const root = document.body;
  const toggle = document.getElementById("themeToggle");
  if (!toggle) return;

  const saved = localStorage.getItem("autodev-theme");
  if (saved === "dark") root.classList.add("dark");

  toggle.addEventListener("click", function () {
    root.classList.toggle("dark");
    localStorage.setItem("autodev-theme", root.classList.contains("dark") ? "dark" : "light");
  });
})();
