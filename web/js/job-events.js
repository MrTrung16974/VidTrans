// Live job progress over one WebSocket instead of polling the whole job list.
// The server pushes only jobs that changed (coalesced to ~2 messages/second);
// a slow HTTP poll takes over only while the socket is down.

const FALLBACK_POLL_MS = 15000;
const MAX_RECONNECT_MS = 30000;

export function jobEventsUrl(locationLike = location) {
  const scheme = locationLike.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${locationLike.host}/api/v1/jobs/events`;
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

export function createJobEvents({ onChanges, onReconnected, poll, isActive }) {
  let socket = null;
  let attempts = 0;
  let reconnectTimer = null;
  let pollTimer = null;
  let stopped = true;

  function startPolling() {
    if (!pollTimer) pollTimer = setInterval(poll, FALLBACK_POLL_MS);
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
      current = new WebSocket(jobEventsUrl());
    } catch {
      startPolling();
      scheduleReconnect();
      return;
    }
    socket = current;
    current.onmessage = event => {
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "ready") {
        attempts = 0;
        stopPolling();
        // Anything that changed while disconnected is picked up here once.
        onReconnected();
      } else if (message.type === "jobs") {
        onChanges(message);
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
      connect();
    },
    stop() {
      stopped = true;
      clearTimeout(reconnectTimer);
      stopPolling();
      const current = socket;
      socket = null;
      if (current) current.close();
    },
  };
}
