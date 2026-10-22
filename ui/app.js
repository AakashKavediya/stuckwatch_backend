import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { sendNotification } from "@tauri-apps/plugin-notification";

const $ = (id) => document.getElementById(id);
let currentNudge = null;
const state = { demo: false, lastSignals: null };

function command(cmd, fields = {}) { return invoke("send_backend_command", { command: JSON.stringify({ cmd, ...fields }) }); }
function text(id, value) { $(id).textContent = value ?? "—"; }
function renderScore(event) {
  const percent = Math.round(Math.max(0, Math.min(1, event.stuck_likelihood || 0)) * 100);
  const band = percent < 35 ? "calm" : percent <= 65 ? "watching" : "possible";
  text("score", `${percent}%`); $("meterFill").style.width = `${percent}%`; $("meterFill").className = `meter-fill ${band}`;
  text("scoreBand", band); $("scoreBand").className = `band ${band}`;
  const reasons = event.reasons || []; $("reasons").innerHTML = reasons.length ? reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("") : "<li>None yet</li>";
  const video = event.app_category === "video"; $("suppressed").classList.toggle("hidden", !video);
  text("why", video ? "Video player in front -> suppressed, not asking." : `${event.app_category || "Document"} still for ${Math.round(event.idle_seconds || 0)}s, ${event.typing_rate || 0} typing/min -> asking, not assuming.`);
}
function renderSignals(event) {
  state.lastSignals = event; renderScore(event);
  text("blink", event.blink_rate_per_min == null ? "—" : `${event.blink_rate_per_min}/min`);
  text("gaze", event.gaze_stability == null ? "—" : event.gaze_stability); text("idle", `${event.idle_seconds ?? "—"}s`);
  text("typing", `${event.typing_rate ?? "—"}/min`); text("switches", event.app_switches_per_min); text("category", event.app_category);
  text("face", event.face_detected == null ? "—" : event.face_detected ? "yes" : "no"); text("mode", `mode: ${event.mode || $("mode").textContent.replace("mode: ", "")}`);
  text("vision", event.vision_status); text("visionDetail", event.vision_detail || "No additional camera detail.");
}
function renderStatus(event) {
  text("state", event.state); text("stateBadge", event.state); text("mode", `mode: ${event.mode}`);
  text("cameraBadge", `Camera: ${event.camera_active ? "ON" : "OFF"}`); text("consentBadge", `Consent: ${event.consent ? "granted" : "not granted"}`);
  $("pauseBadge").classList.toggle("hidden", !event.paused_until);
  if (event.paused_until) text("pauseBadge", `Paused until ${new Date(event.paused_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
  text("vision", event.vision_status); text("visionDetail", event.vision_detail || "No additional camera detail.");
}
function addLog(line, error = false) { const el = $("log"); el.textContent += `[${error ? "ERR" : "LOG"}] ${line}\n`; el.scrollTop = el.scrollHeight; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
async function showNudge(event) {
  currentNudge = event; const win = await WebviewWindow.getByLabel("nudge");
  if (!win) await new WebviewWindow("nudge", { url: "nudge.html", title: "stuckwatch nudge", width: 380, height: 300, resizable: false, focus: false, alwaysOnTop: true, decorations: false });
  await new Promise((resolve) => setTimeout(resolve, 150));
  await invoke("broadcast_nudge", { payload: JSON.stringify(event) });
  sendNotification({ title: "stuckwatch: a tiny check-in", body: event.message });
}
function history(choice) { const list = $("history"); if (list.children.length === 1 && list.firstElementChild.textContent === "No nudges yet.") list.innerHTML = ""; const li = document.createElement("li"); li.textContent = `${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} — ${choice}`; list.prepend(li); }

listen("backend-event", ({ payload }) => {
  const event = typeof payload === "string" ? JSON.parse(payload) : payload;
  if (event.type === "signals") renderSignals(event); else if (event.type === "status") renderStatus(event);
  else if (event.type === "nudge") showNudge(event); else if (event.type === "tiny_step") addLog(`Step suggestion: ${event.text}`);
  else if (event.type === "rest_started") addLog("Break started for 10 minutes.");
});
listen("backend-log", ({ payload }) => { const line = String(payload); addLog(line, /ERROR|WARNING|Traceback/.test(line)); });
listen("nudge-response", ({ payload }) => { const result = typeof payload === "string" ? JSON.parse(payload) : payload; history(result.choice); });
$("demoMode").addEventListener("change", (e) => { state.demo = e.target.checked; $("demoBadge").classList.toggle("hidden", !state.demo); command("demo_mode", { enabled: state.demo }); });
$("consent").addEventListener("click", () => { if (confirm("Camera consent\n\nstuckwatch reads only coarse blink, gaze, and face-presence numbers. Nothing is stored or sent, and you can turn it off any time.")) command("consent", { camera: true }); });
document.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => { if (!$("demoMode").checked) $("demoMode").click(); command("demo_scenario", { scenario: button.dataset.scenario }); }));
$("reset").addEventListener("click", () => command("reset_demo"));
command("hello"); command("start");
