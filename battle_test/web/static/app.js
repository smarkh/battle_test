// Small progressive enhancements: the form's mode switch, document tabs,
// and live progress over server-sent events. Pages work without JS.

// New case form: show only the panel for the chosen complaint mode.
const modeInputs = document.querySelectorAll("input[data-mode]");
function showMode() {
  const chosen = document.querySelector("input[data-mode]:checked");
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.hidden = chosen && panel.dataset.panel !== chosen.value;
  });
}
modeInputs.forEach((input) => input.addEventListener("change", showMode));
if (modeInputs.length) showMode();

// Results: document tabs.
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", "false"));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = true));
    tab.setAttribute("aria-selected", "true");
    document.getElementById(tab.dataset.tab).hidden = false;
  });
});

// Progress: stream stages and draft text; reload into the results when done.
const progress = document.getElementById("progress");
if (progress) {
  const stages = document.getElementById("stages");
  const live = document.getElementById("live");
  const liveTitle = document.getElementById("live-title");
  const queue = document.getElementById("queue");
  const status = document.getElementById("status");
  const events = new EventSource(`/cases/${progress.dataset.case}/events`);

  events.addEventListener("queue", (e) => {
    queue.hidden = false;
    queue.textContent = `Waiting in the queue: position ${JSON.parse(e.data).position}. Runs go one at a time.`;
  });
  events.addEventListener("stage", (e) => {
    const { title, role } = JSON.parse(e.data);
    queue.hidden = true;
    status.hidden = false;
    status.textContent = `Running: ${title} (${role})…`;
    stages.querySelectorAll("li.current").forEach((li) => li.classList.replace("current", "complete"));
    const li = document.createElement("li");
    li.className = "current";
    li.textContent = `${title} (${role})`;
    stages.appendChild(li);
    liveTitle.textContent = title;
    live.textContent = "";
  });
  events.addEventListener("text", (e) => {
    const nearBottom = live.scrollHeight - live.scrollTop - live.clientHeight < 40;
    live.textContent += JSON.parse(e.data).text;
    if (nearBottom) live.scrollTop = live.scrollHeight;
  });
  for (const name of ["done", "failed"]) {
    events.addEventListener(name, () => {
      events.close();
      window.location.reload();
    });
  }
}
