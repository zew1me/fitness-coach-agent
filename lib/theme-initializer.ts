export const themeInitializer = `
(() => {
  const storageKey = "fitness-theme-preference";
  let mode = "system";

  try {
    const saved = window.localStorage.getItem(storageKey);
    mode = saved === "light" || saved === "dark" || saved === "system" ? saved : "system";
  } catch {
    mode = "system";
  }

  let systemDark = false;
  try {
    systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    systemDark = false;
  }

  const resolved = mode === "system" ? (systemDark ? "dark" : "light") : mode;
  document.documentElement.dataset.themeMode = mode;
  document.documentElement.dataset.theme = resolved;
})();
`;
