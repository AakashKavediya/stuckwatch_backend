# stuckwatch

> **A privacy-first, local-only companion that detects proxy indicators of task paralysis (executive dysfunction) using webcam cues and on-screen context, emitting gentle, dismissible nudges.**

Built as a dedicated Python backend designed to run as a child process under a desktop app (e.g., Tauri). It communicates entirely via standard input/output using a newline-delimited JSON protocol.

---

## 1. Ethics & Privacy Manifesto

`stuckwatch` is engineered around strict, non-negotiable privacy and ethical constraints:

1. **100% Local Processing**: Operates entirely offline. No telemetry, no cloud APIs, and zero network calls.
2. **Zero Frame Persistence**: Video frames exist only as transient in-memory arrays. They are **never** saved to disk, logged, encoded, or transmitted. Every frame reference is explicitly dereferenced immediately after landmark extraction.
3. **Camera Off by Default**: The webcam is disabled upon startup. It activates **only** after an explicit `consent` command from the frontend and is instantly released whenever consent is revoked, the system is paused, or the backend is stopped.
4. **Camera-Free Mode**: Full functionality is available in `screen_only` mode, relying exclusively on non-intrusive activity context (idle time, app switching, typing cadence).
5. **Short Rolling Windows & Coarse Categories**: Only high-level numeric metrics (blink rate, gaze stability, idle seconds) are retained in short in-memory rolling buffers (max ~10 minutes). Window titles are **never** stored or emitted—they are momentarily checked in memory to map to coarse categories (`editor`, `browser`, `video`, `call`, `game`, `other`, `unknown`) and then dropped.
6. **No Keystroke or Cursor Logging**: Keyboard and mouse listeners record only event timestamps and aggregate counts. No characters, key identifiers, mouse coordinates, or trails are ever tracked.
7. **Uncertain Hints, Never Diagnoses**: All outputs use the wording *"possible indicator"* or *"might be stuck"*. It never uses diagnostic labels or clinical claims (no *"ADHD"*, *"anxious"*, *"autistic"*, or *"paralysed"*). The score is an uncertain proxy called `stuck_likelihood`.
8. **Gentle, Low-Pressure Nudges**:
   - Never escalates if ignored.
   - Unanswered nudges auto-expire silently after 2 minutes.
   - Long cooldowns that double on dismissal (up to 3 hours).
   - No sounds, streaks, or penalties.
   - Single command to pause for the day.
   - After repeated dismissals, the backend automatically lowers its sensitivity to be quieter.
9. **Full Data Wipe**: A `delete_data` command wipes all local settings, baselines, and aggregate counts immediately.

---

## 2. Requirements & Installation

### Supported Python Versions
- **Python 3.10 to 3.12** is required (for MediaPipe Face Mesh compatibility).

### Automated Setup (Recommended)
Run the cross-platform setup script (standard library only):
```bash
python setup_env.py
```
This script:
1. Checks for Python 3.10, then 3.11, then 3.12.
2. Creates the `.venv` virtual environment in the project root if missing.
3. Upgrades `pip` inside `.venv`.
4. Installs the pinned NumPy and MediaPipe vision stack, then `requirements.txt`.
5. Verifies the legacy `mediapipe.solutions` API and OpenCV/NumPy imports.
6. Prints the selected Python version and venv interpreter path.

### Setup

From the repository root:

```bash
python setup_env.py
python run.py --mode hybrid --camera-consent
```

Then send this JSON line to stdin:

```json
{"cmd":"start"}
```

For a deterministic intervention demo without a camera:

```powershell
python tools/demo_intervention.py
```

Simulation scenarios are available with `--mode simulate`:

```powershell
python run.py --mode simulate --scenario stuck_doc
python run.py --mode simulate --scenario watching_video
python run.py --mode simulate --scenario busy_typing
```

The intervention layer emits silent, dismissible `nudge` events only after a
continuous elevated score, protected-context filtering, cooldown, and daily
limit checks. UI responses use `nudge_response` with `tiny_step`, `rest`,
`not_now`, `i_am_fine`, or `change`.

The setup requires Python 3.10, 3.11, or 3.12. If setup reports that
`mediapipe.solutions` is unavailable, recreate the environment with a supported
interpreter and run `python setup_env.py` again. MediaPipe 0.10.14 is required
because this backend uses the legacy `mp.solutions.face_mesh` API.

### Controller UI demo

The repository includes a minimal Tauri controller in `ui/`. From the repository
root, run:

```powershell
npm install
npm run tauri dev
```

The controller starts the backend in local simulation mode. Turn on **Demo mode**
and use the three scenario buttons. Demo mode uses a 10-second sustain window,
30-second cooldown, and ignores the daily cap; the badge makes this explicit.
The camera consent dialog is separate and the preview-free indicator only exposes
coarse numeric signals.

30-second judge script:

1. Turn on **Demo mode** and click **Simulate: frozen at blank document**. The
   meter rises, possible indicators appear, and within about 10–15 seconds a
   bottom-right nudge card plus an OS notification appears.
2. Click **Tiny first step** and observe the returned small action, then click
   **Done**. The controller's nudge history records the choice.
3. Click **Reset**, then **Simulate: watching a video**. The meter may rise, but
   the visible “Suppressed: video player in front” label confirms no nudge.
4. Click **Reset**, then **Simulate: busy typing**. The meter stays calm and no
   nudge is emitted. **Not now**, **I'm fine**, and **Pause for today** are also
   visible response paths; Escape maps to **Not now**.

### Manual Setup

#### Windows (PowerShell)
```powershell
py -3.10 -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
python run.py --mode hybrid --camera-consent
```

#### macOS & Linux
```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python run.py --mode hybrid --camera-consent
```

---

## 3. Running stuckwatch

You can launch the backend using `run.py`, which ensures `.venv` exists and transparently forwards all arguments and standard I/O:

```bash
# Run quick self-test check
python run.py --self-test

# Run in synthetic simulation mode (ideal for UI testing without a webcam)
python run.py --mode simulate --debug

# Run in hybrid mode with explicit development consent
python run.py --mode hybrid --camera-consent

# Run in screen-only mode (activity signals only, no camera)
python run.py --mode screen_only

# Run with camera (camera starts OFF until explicit consent command)
python run.py --mode camera
```

### Command-Line Arguments
| Flag | Options / Format | Default | Description |
|---|---|---|---|
| `--mode` | `camera`, `screen_only`, `hybrid`, `simulate` | `hybrid` | Initial operational mode |
| `--camera-index` | Integer | `0` | Hardware webcam index |
| `--camera-consent` | Flag | Off | Pre-grant camera consent for local development/testing only |
| `--data-dir` | Path | `.stuckwatch_data` | Directory for local SQLite database |
| `--sensitivity` | `low`, `medium`, `high` | `medium` | Nudge trigger sensitivity |
| `--no-autostart` | Flag | `True` | Wait for `start` command before monitoring |
| `--autostart` | Flag | `False` | Immediately begin monitoring on launch |
| `--debug` | Flag | `False` | Stream debug diagnostic logs to `stderr` |
| `--self-test` | Flag | `False` | Run environment check, print JSON line, and exit |

---

## 4. IPC Protocol (JSON-lines)

Communication with the parent process (e.g., Tauri) occurs over standard streams:
- **`stdout`**: Emits **only** single-line JSON events. Every line ends with `\n` and is flushed immediately.
- **`stderr`**: Backend logging (never pollutes `stdout`).
- **`stdin`**: Receives single-line JSON commands from the frontend.

Every event includes `"v": 1` (protocol version) and `"ts"` (UNIX timestamp float).

### Events (Backend → Frontend)

#### `ready`
Emitted immediately on launch or in response to `hello`.
```json
{"v":1,"type":"ready","version":"1.0.0","modes":["camera","screen_only","hybrid","simulate"],"platform":"Windows-10-10.0.26100-SP0","ts":1771700000.123}
```

#### `status`
Emitted upon state changes, mode switches, consent toggles, or pause events.
```json
{"v":1,"type":"status","state":"monitoring","mode":"hybrid","camera_active":true,"consent":true,"paused_until":null,"sensitivity":"medium","vision_status":"active","hybrid":true,"ts":1771700002.456}
```

#### `signals`
Streamed periodically (~every 2 seconds) with derived numeric proxy signals.
```json
{"v":1,"type":"signals","blink_rate_per_min":14.2,"gaze_stability":0.89,"head_motion":0.012,"vision_available":true,"vision_status":"active","face_detected":true,"vision_warmup_seconds_left":null,"idle_seconds":124.0,"app_switches_per_min":0.0,"typing_rate":0.0,"app_category":"editor","stuck_likelihood":0.812,"confidence":0.85,"reasons":["no typing for 2 minutes in your editor","fixed gaze on screen with no recent typing"],"ts":1771700004.789}
```

Vision status is one of `active`, `warming_up`, `no_consent`, `paused`,
`screen_only_mode`, `camera_unavailable`, or `no_face`. Vision values are
`null` whenever consent is absent, monitoring is paused/stopped, the camera is
unavailable, or no face is detected. Signals are emitted only while monitoring
or calibrating.

#### `nudge`
Emitted when sustained high stuck likelihood is detected and no suppression rules apply.
```json
{"v":1,"type":"nudge","id":"d4e5f6a7-0001-4000-8000-abcdef012345","message":"Been on this a while. Want a hand, or are you good?","options":["tiny_start","rest","not_now"],"reasons":["no typing for 3 minutes in your editor","fixed gaze on screen with no recent typing"],"ts":1771700060.100}
```

#### `calibration_progress`
Emitted every second during personal baseline calibration.
```json
{"v":1,"type":"calibration_progress","percent":42,"ts":1771700075.000}
```

#### `calibration_done`
Emitted when calibration finishes or is finalized.
```json
{"v":1,"type":"calibration_done","ok":true,"samples":120,"ts":1771700180.000}
```

#### `ack`
Emitted in response to commands.
```json
{"v":1,"type":"ack","cmd":"consent","ok":true,"error":null,"ts":1771700005.100}
```

#### `error`
Emitted for recoverable warnings or runtime fallbacks (e.g., camera hardware loss).
```json
{"v":1,"type":"error","code":"CAMERA_READ_FAILED","message":"Webcam frame acquisition stopped repeatedly.","ts":1771700010.200}
```

---

### Commands (Frontend → Backend)

Send as a single JSON object per line to the child process's `stdin`.

| Command | JSON Payload | Description |
|---|---|---|
| `hello` | `{"cmd":"hello"}` | Request `ready` and current `status` |
| `consent` | `{"cmd":"consent","camera":true}` | Grant or revoke camera consent |
| `set_mode` | `{"cmd":"set_mode","mode":"camera"}` | Switch mode: `camera`, `screen_only`, `hybrid`, or `simulate` |
| `vision_test` | `{"cmd":"vision_test"}` | With consent, run a five-second in-memory camera check and return face ratio/FPS |
| `start` | `{"cmd":"start"}` | Start or resume monitoring |
| `stop` | `{"cmd":"stop"}` | Stop monitoring and release camera |
| `pause` | `{"cmd":"pause","minutes":25}` | Pause nudges and release camera for N minutes |
| `pause_today` | `{"cmd":"pause_today"}` | Pause nudges and release camera until local midnight |
| `resume` | `{"cmd":"resume"}` | Cancel pause and resume monitoring |
| `calibrate_start` | `{"cmd":"calibrate_start","seconds":180}` | Start personal baseline capture |
| `calibrate_cancel`| `{"cmd":"calibrate_cancel"}` | Cancel ongoing calibration |
| `feedback` | `{"cmd":"feedback","nudge_id":"...","choice":"not_now"}` | Choices: `tiny_start`, `rest`, `not_now`, `dismissed`, `change` |
| `set_sensitivity` | `{"cmd":"set_sensitivity","level":"high"}` | Levels: `low` (0.85), `medium` (0.75), `high` (0.65) |
| `delete_data` | `{"cmd":"delete_data"}` | Wipe all stored local settings, baselines, and database |
| `get_status` | `{"cmd":"get_status"}` | Request current status event |
| `shutdown` | `{"cmd":"shutdown"}` | Cleanly release resources and exit with code 0 |

---

## 5. How Tauri Should Launch stuckwatch

In Tauri (Rust), spawn the backend as a child process using `std::process::Command`.

### Recommended Spawn Path
- **Windows**: `.venv\\Scripts\\python.exe`
- **macOS / Linux**: `.venv/bin/python`

### Example Rust Spawn & I/O Loop (Tauri)
```rust
use std::path::Path;
use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

pub fn spawn_stuckwatch() -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "windows")]
    let python_path = ".venv\\Scripts\\python.exe";
    #[cfg(not(target_os = "windows"))]
    let python_path = ".venv/bin/python";

    if !Path::new(python_path).exists() {
        // Set the frontend's state to "Setting up..." before running setup_env.py.
        return Err("Setting up... .venv is missing; run python setup_env.py".into());
    }

    let mut child = Command::new(python_path)
        .args(&["-m", "stuckwatch", "--mode", "simulate"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit()) // Logs go to console stderr
        .spawn()?;

    let mut stdin = child.stdin.take().expect("Failed to open stdin");
    let stdout = child.stdout.take().expect("Failed to open stdout");

    // Spawn reader thread
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(msg) = line {
                // Forward JSON message to Tauri webview via emit
                println!("[Tauri received] {}", msg);
            }
        }
    });

    // Send command to start
    writeln!(stdin, "{{\"cmd\":\"start\"}}")?;
    stdin.flush()?;

    Ok(())
}
```

> **Note on Parent Process Termination**: If the Tauri app closes or crashes, the child process detects `EOF` on `stdin` and automatically triggers a clean shutdown, releasing all camera hardware handles.

---

## 6. Permissions & Platform Notes

- **Camera Permissions**:
  - **macOS**: Ensure your app or terminal has Camera access under *System Settings → Privacy & Security → Camera*.
  - **Windows**: Ensure camera access is allowed under *Settings → Privacy & security → Camera*.
  - **Linux**: Ensure the current user belongs to the `video` group (`sudo usermod -aG video $USER`).
- **Input Monitoring (pynput)**:
  - **macOS**: Requires *System Settings → Privacy & Security → Accessibility* and *Input Monitoring*. If denied, `stuckwatch` emits a non-fatal `error` event and continues running with idle fallback tracking.
  - **Linux (Wayland)**: Global input hooks may be restricted under Wayland without display server privileges. `stuckwatch` degrades gracefully.
- **Active Window Detection**:
  - On Windows, uses `pywin32` / `pygetwindow`.
  - On macOS, uses `AppKit.NSWorkspace` or AppleScript.
  - On Linux, uses `xdotool` on X11.

---

## 7. One-Minute Manual Test Walkthrough

Test the entire integration in simulation mode without a camera:

1. **Launch `stuckwatch`**:
   ```bash
   python run.py --mode simulate --debug
   ```
2. **Handshake (`stdin`)**:
   Type or paste:
   ```json
   {"cmd":"hello"}
   ```
   *Expected response on `stdout`:*
   ```json
   {"v":1,"type":"ready",...}
   {"v":1,"type":"status","state":"idle","mode":"simulate",...}
   ```
3. **Start Monitoring (`stdin`)**:
   ```json
   {"cmd":"start"}
   ```
   *Expected response:*
   ```json
   {"v":1,"type":"ack","cmd":"start","ok":true,...}
   {"v":1,"type":"status","state":"monitoring",...}
   ```
4. **Watch Signals Stream**:
   Every 2 seconds, a `signals` JSON line appears. Around ~40–60 seconds, the synthetic simulator enters the stagnant editor phase:
   - `idle_seconds` climbs
   - `stuck_likelihood` exceeds 0.75
5. **Observe Nudge**:
   A gentle `nudge` event will be emitted:
   ```json
   {"v":1,"type":"nudge","id":"...","message":"Been on this a while. Want a hand, or are you good?","options":["tiny_start","rest","not_now"],...}
   ```
6. **Reply with Feedback (`stdin`)**:
   ```json
   {"cmd":"feedback","nudge_id":"...","choice":"tiny_start"}
   ```
   *Expected response:*
   ```json
   {"v":1,"type":"ack","cmd":"feedback","ok":true,...}
   ```
7. **Clean Exit (`stdin`)**:
   ```json
   {"cmd":"shutdown"}
   ```
   *The process exits with code 0.*

---

## 8. Running Automated Tests

Run the full pytest suite (which runs completely offline without camera access):
```bash
python -m pytest tests -v
```

Tests verify:
- EAR calculation and geometry formulas
- Blink detection with 2-frame debouncing
- Gaze stability and dispersion metrics
- Estimator monotonicity and explainable plain-language reasons
- All suppression rules (video, call, fullscreen, away from desk, cooldown)
- Exponential cooldown doubling on dismissal
- Silent auto-expiration without escalation
- IPC event and command validation
- **Static code privacy audit**: Asserts that `cv2.imwrite`, `cv2.VideoWriter`, and all network libraries (`requests`, `urllib.request`, etc.) are 100% absent from the codebase.
