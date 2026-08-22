const input = document.getElementById("backendUrl");
const status = document.getElementById("status");

chrome.storage.sync.get("backendUrl").then(({ backendUrl }) => {
  input.value = backendUrl || "http://127.0.0.1:8000";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({ backendUrl: input.value.trim().replace(/\/+$/, "") });
  status.textContent = "Saved ✓";
  setTimeout(() => (status.textContent = ""), 1500);
});
