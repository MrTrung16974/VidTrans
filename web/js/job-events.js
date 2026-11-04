// Live job progress over one WebSocket instead of polling the whole job list.
// The server pushes only jobs that changed (coalesced to ~2 messages/second);
// a slow HTTP poll takes over only while the socket is down.

const FALLBACK_POLL_MS = 15000;
const MAX_RECONNECT_MS = 30000;
// The server sends at least a ping every 25 s. A socket that stays silent for
// longer is dead without a close event (sleeping phone, network switch, tab
// frozen in the background), so it is dropped and opened again.
const STALE_SOCKET_MS = 60000;
const WATCHDOG_MS = 10000;

export function eventsUrl(path, locationLike = location) {
  const scheme = locationLike.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${locationLike.host}${path}`;
}

export function jobEventsUrl(locationLike = location) {
  return eventsUrl("/api/v1/jobs/events", locationLike);
}

// Patch the visible list in place. Returns true when the change can affect
// filtering, ordering or totals, in which case the caller reloads the list.
export function applyJobChanges(jobs, message, { statusFilter = "" } = {}) {
  if (message.resync) return { jobs, reload: true };
  let reload = false;
  const removed = new Set(message.removed || []);
  let next = removed.size ? jobs.filter(job => !removed.has(job.job_id)) : jobs.slice();
  const indexById = new Map(next.map((job, index) => [job.job_id, index]));
  for (const item of message.items || []) {
    const index = indexById.get(item.job_id);
    if (index === undefined) {
      reload = true; // a new job: let the server apply filters and ordering
      continue;
    }
    if (statusFilter && item.status !== statusFilter) reload = true;
    next[index] = item;
  }
  const positions = message.queue_positions;
  if (positions) {
    next = next.map(job => {
      const position = positions[job.job_id];
      if (position === job.queue_position) return job;
      const updated = { ...job };
      if (position) updated.queue_position = position;
      else delete updated.queue_position;
      return updated;
    });
  }
  return { jobs: next, reload };
}

export function createJobEvents({ onChanges, ...options }) {
  return createLiveEvents({ ...options, path: "/api/v1/jobs/events", messageType: "jobs", onMessage: onChanges });
}

// One server-push socket with reconnect backoff; `poll` keeps the page fresh
// over HTTP every `fallbackPollMs` only while the socket is down.
export function createLiveEvents({
  path, messageType, onMessage, onReconnected = () => {}, poll, isActive, fallbackPollMs = FALLBACK_POLL_MS,
  staleSocketMs = STALE_SOCKET_MS, now = () => Date.now(),
}) {
  let socket = null;
  let attempts = 0;
  let reconnectTimer = null;
  let pollTimer = null;
  let watchdogTimer = null;
  let lastMessageAt = 0;
  let stopped = true;

  function socketIsStale() {
    return Boolean(socket) && now() - lastMessageAt > staleSocketMs;
  }

  // Forget a socket that may never fire onclose, then fall back to polling.
  function dropSocket() {
    const current = socket;
    socket = null;
    if (!current) return;
    current.onmessage = null;
    current.onclose = null;
    try { current.close(); } catch { /* already closed */ }
  }

  function checkSocket() {
    if (stopped || !socketIsStale()) return;
    dropSocket();
    poll();
    startPolling();
    attempts = 0;
    clearTimeout(reconnectTimer);
    connect();
  }

  function startPolling() {
    if (!pollTimer) pollTimer = setInterval(poll, fallbackPollMs);
  }

  function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    const delay = Math.min(MAX_RECONNECT_MS, 1000 * 2 ** attempts);
    attempts += 1;
    reconnectTimer = setTimeout(connect, delay);
  }

  function connect() {
    if (stopped || socket || !isActive()) return;
    if (typeof WebSocket === "undefined") {
      startPolling();
      return;
    }
    let current;
    try {
      current = new WebSocket(eventsUrl(path));
    } catch {
      startPolling();
      scheduleReconnect();
      return;
    }
    socket = current;
    lastMessageAt = now();
    current.onmessage = event => {
      lastMessageAt = now();
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "ready") {
        attempts = 0;
        stopPolling();
        // Anything that changed while disconnected is picked up here once.
        onReconnected();
      } else if (message.type === messageType) {
        onMessage(message);
      }
    };
    current.onclose = () => {
      if (socket === current) socket = null;
      if (stopped) return;
      // An HTTP poll also surfaces an expired session (401 -> login screen).
      poll();
      startPolling();
      scheduleReconnect();
    };
  }

  return {
    start() {
      stopped = false;
      attempts = 0;
      if (!watchdogTimer) watchdogTimer = setInterval(checkSocket, WATCHDOG_MS);
      connect();
    },
    // The page became visible or the network came back: timers were frozen,
    // so reconnect now instead of waiting for the backoff or a dead socket.
    resume() {
      if (stopped) return;
      if (socket && !socketIsStale()) return;
      dropSocket();
      attempts = 0;
      clearTimeout(reconnectTimer);
      connect();
    },
    stop() {
      stopped = true;
      clearTimeout(reconnectTimer);
      clearInterval(watchdogTimer);
      watchdogTimer = null;
      stopPolling();
      const current = socket;
      socket = null;
      if (current) current.close();
    },
  };
}
