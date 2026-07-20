/* Vanilla JS, no build step and no CDN dependency by design (see README) --
 * every page polls the JSON API on an interval and re-renders. Device
 * hostnames and alert messages ultimately come from the network (a device's
 * DHCP hostname is attacker-controllable), so everything untrusted goes
 * through escapeHtml() before it's ever concatenated into innerHTML.
 */
const NetmonDashboard = (() => {
  const REFRESH_MS = 15000;

  function escapeHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function api(path, options) {
    const resp = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (resp.status === 401) {
      window.location.href = "/login";
      throw new Error("Not authenticated");
    }
    if (!resp.ok) {
      throw new Error(`${path} returned ${resp.status}`);
    }
    return resp.status === 204 ? null : resp.json();
  }

  function timeAgo(isoString) {
    if (!isoString) return "never";
    const then = new Date(isoString).getTime();
    const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  function poll(fn) {
    fn();
    return setInterval(fn, REFRESH_MS);
  }

  // -- Networks list -------------------------------------------------------

  async function renderNetworksPage() {
    const grid = document.getElementById("networks-grid");
    poll(async () => {
      try {
        const networks = await api("/api/networks");
        if (networks.length === 0) {
          grid.innerHTML = `<div class="empty-state">No networks configured in config.yml.</div>`;
          return;
        }
        grid.innerHTML = networks
          .map(
            (n) => `
          <a class="card net-card" href="/networks/${escapeHtml(n.slug)}">
            <div class="name">${escapeHtml(n.name)}</div>
            <div class="stats">
              <span>${n.online_count}/${n.device_count} online</span>
              ${n.new_count > 0 ? `<span class="pill new">${n.new_count} new</span>` : ""}
            </div>
          </a>`
          )
          .join("");
      } catch (err) {
        grid.innerHTML = `<div class="empty-state">Couldn't load networks: ${escapeHtml(err.message)}</div>`;
      }
    });
  }

  // -- Single network device view ------------------------------------------

  function deviceRow(device, columns) {
    const nameCell = `
      <span class="nickname-view" data-mac="${escapeHtml(device.mac)}">
        ${escapeHtml(device.display_name)}
        ${device.is_new ? '<span class="pill new">new</span>' : ""}
      </span>`;
    const editButton = `<button class="edit-nickname-btn" data-mac="${escapeHtml(device.mac)}">Rename</button>`;

    if (columns === "connected") {
      return `<tr>
        <td>${nameCell}</td>
        <td class="mono dim">${escapeHtml(device.mac)}</td>
        <td class="dim">${escapeHtml(device.vendor || "—")}</td>
        <td class="dim">${device.is_wired ? "Wired" : "Wi-Fi"}</td>
        <td class="dim">${timeAgo(device.last_seen)}</td>
        <td>${editButton}</td>
      </tr>`;
    }
    return `<tr>
      <td>${nameCell}</td>
      <td class="mono dim">${escapeHtml(device.mac)}</td>
      <td class="dim">${escapeHtml(device.vendor || "—")}</td>
      <td class="dim">${timeAgo(device.last_seen)}</td>
      <td>${editButton}</td>
    </tr>`;
  }

  function attachNicknameHandlers(container, onSaved) {
    container.querySelectorAll(".edit-nickname-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mac = btn.dataset.mac;
        const nameSpan = container.querySelector(`.nickname-view[data-mac="${CSS.escape(mac)}"]`);
        const current = nameSpan.textContent.trim().replace(/\s*new\s*$/i, "");
        nameSpan.outerHTML = `
          <span class="nickname-edit" data-mac="${escapeHtml(mac)}">
            <input type="text" class="nickname-input" value="${escapeHtml(current)}" maxlength="100">
            <button class="save-nickname-btn primary">Save</button>
          </span>`;
        const wrap = container.querySelector(`.nickname-edit[data-mac="${CSS.escape(mac)}"]`);
        wrap.querySelector(".save-nickname-btn").addEventListener("click", async () => {
          const value = wrap.querySelector(".nickname-input").value;
          await api(`/api/devices/${encodeURIComponent(mac)}/label`, {
            method: "PATCH",
            body: JSON.stringify({ custom_name: value }),
          });
          onSaved();
        });
      });
    });
  }

  async function renderNetworkDetailPage(slug) {
    const title = document.getElementById("network-title");
    const connectedBody = document.querySelector("#connected-table tbody");
    const disconnectedBody = document.querySelector("#disconnected-table tbody");

    async function load() {
      try {
        const data = await api(`/api/networks/${encodeURIComponent(slug)}/devices`);
        title.textContent = data.network.name;

        const connected = data.devices.filter((d) => d.is_online);
        const disconnected = data.devices.filter((d) => !d.is_online);

        connectedBody.innerHTML =
          connected.map((d) => deviceRow(d, "connected")).join("") ||
          `<tr><td colspan="6" class="empty-state">Nothing connected right now.</td></tr>`;
        disconnectedBody.innerHTML =
          disconnected.map((d) => deviceRow(d, "disconnected")).join("") ||
          `<tr><td colspan="5" class="empty-state">No known disconnected devices.</td></tr>`;

        attachNicknameHandlers(connectedBody, load);
        attachNicknameHandlers(disconnectedBody, load);
      } catch (err) {
        title.textContent = "Network";
        connectedBody.innerHTML = `<tr><td colspan="6" class="empty-state">Couldn't load devices: ${escapeHtml(err.message)}</td></tr>`;
      }
    }
    poll(load);
  }

  // -- Infrastructure --------------------------------------------------------

  async function renderInfraPage() {
    const list = document.getElementById("infra-list");
    poll(async () => {
      try {
        const rows = await api("/api/infrastructure");
        if (rows.length === 0) {
          list.innerHTML = `<div class="empty-state">No switches or APs seen yet.</div>`;
          return;
        }
        list.innerHTML = rows
          .map((r) => {
            const alerting = !r.is_online && r.past_alert_threshold;
            const status = r.is_online
              ? `<span class="pill online">online</span>`
              : `<span class="pill offline">offline ${r.offline_minutes != null ? r.offline_minutes + "m" : ""}</span>`;
            return `<div class="card infra-card ${alerting ? "alerting" : ""}">
              <div>
                <div class="name" style="font-weight:600;">${escapeHtml(r.name)}</div>
                <div class="dim" style="font-size:0.82rem;">${escapeHtml(r.kind)} · ${escapeHtml(r.network)}</div>
              </div>
              ${status}
            </div>`;
          })
          .join("");
      } catch (err) {
        list.innerHTML = `<div class="empty-state">Couldn't load infrastructure: ${escapeHtml(err.message)}</div>`;
      }
    });
  }

  // -- Alerts -----------------------------------------------------------------

  async function renderAlertsPage() {
    const body = document.querySelector("#alerts-table tbody");
    const hideAckBox = document.getElementById("hide-acknowledged");

    async function load() {
      try {
        const params = hideAckBox.checked ? "?acknowledged=false" : "";
        const rows = await api(`/api/alerts${params}`);
        body.innerHTML =
          rows
            .map(
              (a) => `<tr>
            <td>${escapeHtml(a.type)}</td>
            <td><span class="pill severity-${escapeHtml(a.severity)}">${escapeHtml(a.severity)}</span></td>
            <td>${escapeHtml(a.message)}</td>
            <td class="dim">${timeAgo(a.created_at)}</td>
            <td>${a.acknowledged ? "" : `<button class="ack-btn" data-id="${a.id}">Acknowledge</button>`}</td>
          </tr>`
            )
            .join("") || `<tr><td colspan="5" class="empty-state">No alerts.</td></tr>`;

        body.querySelectorAll(".ack-btn").forEach((btn) => {
          btn.addEventListener("click", async () => {
            await api(`/api/alerts/${btn.dataset.id}/ack`, { method: "POST" });
            load();
          });
        });
      } catch (err) {
        body.innerHTML = `<tr><td colspan="5" class="empty-state">Couldn't load alerts: ${escapeHtml(err.message)}</td></tr>`;
      }
    }
    hideAckBox.addEventListener("change", load);
    poll(load);
  }

  // -- Uptime -----------------------------------------------------------------

  function drawSparkline(canvas, checks) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    if (checks.length === 0) return;

    const values = checks.map((c) => (c.response_ms != null ? c.response_ms : 0));
    const max = Math.max(...values, 1);
    const stepX = width / Math.max(checks.length - 1, 1);

    ctx.beginPath();
    checks.forEach((c, i) => {
      const x = i * stepX;
      const y = height - (values[i] / max) * (height - 8) - 4;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#2f8f82";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    checks.forEach((c, i) => {
      if (c.status === "down") {
        const x = i * stepX;
        ctx.fillStyle = "#b4453b";
        ctx.fillRect(x - 1, 0, 2, height);
      }
    });
  }

  async function renderUptimePage() {
    const list = document.getElementById("uptime-list");
    poll(async () => {
      try {
        const monitors = await api("/api/uptime");
        if (monitors.length === 0) {
          list.innerHTML = `<div class="empty-state">No website_monitors configured in config.yml.</div>`;
          return;
        }
        list.innerHTML = monitors
          .map(
            (m) => `
          <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:baseline;">
              <div>
                <span style="font-weight:600;">${escapeHtml(m.name)}</span>
                <span class="pill ${m.is_up ? "online" : "offline"}" style="margin-left:8px;">${m.is_up ? "up" : "down"}</span>
              </div>
              <div class="dim" style="font-size:0.85rem;">
                ${m.uptime_pct_24h != null ? m.uptime_pct_24h + "% (24h)" : "—"}
                ${m.last_response_ms != null ? " · " + Math.round(m.last_response_ms) + "ms" : ""}
              </div>
            </div>
            <canvas class="sparkline" id="spark-${escapeHtml(m.name).replace(/[^a-zA-Z0-9]/g, "_")}"></canvas>
            <div class="dim" style="font-size:0.8rem;">Last check: ${timeAgo(m.last_check_at)}</div>
          </div>`
          )
          .join("");

        monitors.forEach((m) => {
          const canvas = document.getElementById(`spark-${m.name.replace(/[^a-zA-Z0-9]/g, "_")}`);
          if (canvas) drawSparkline(canvas, m.checks);
        });
      } catch (err) {
        list.innerHTML = `<div class="empty-state">Couldn't load uptime data: ${escapeHtml(err.message)}</div>`;
      }
    });
  }

  return {
    renderNetworksPage,
    renderNetworkDetailPage,
    renderInfraPage,
    renderAlertsPage,
    renderUptimePage,
  };
})();

// Dark/light toggle -- runs on every page that includes this file. The
// initial theme (if any was saved) is already applied by the inline script
// in base.html's <head> before first paint; this just wires up the button
// and keeps the label in sync with the current state, including the case
// where nothing has been explicitly chosen yet (following the OS setting).
(function initThemeToggle() {
  const STORAGE_KEY = "netmon-theme";
  const button = document.getElementById("theme-toggle");
  if (!button) return; // login page doesn't extend base.html

  function currentTheme() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function render() {
    const theme = currentTheme();
    button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }

  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    localStorage.setItem(STORAGE_KEY, next);
    document.documentElement.setAttribute("data-theme", next);
    render();
  });

  render();
})();
