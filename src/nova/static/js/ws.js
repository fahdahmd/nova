/**
 * WebSocket connection with automatic reconnection.
 *
 * JSON frames are events; binary frames are always synthesised audio.
 */

const RETRY_BASE_MS = 600;
const RETRY_MAX_MS = 8000;
const PING_INTERVAL_MS = 25000;

export class Connection {
  /**
   * @param {{onEvent: (event: object) => void,
   *          onAudio: (buffer: ArrayBuffer) => void,
   *          onState: (state: 'connecting'|'open'|'closed') => void}} handlers
   */
  constructor(handlers) {
    this.handlers = handlers;
    this.socket = null;
    this.attempt = 0;
    this.pingTimer = null;
    this.closedByUs = false;
  }

  get ready() {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  connect() {
    this.closedByUs = false;
    this.handlers.onState('connecting');

    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;

    socket.addEventListener('open', () => {
      this.attempt = 0;
      this.handlers.onState('open');
      this.pingTimer = window.setInterval(() => this.send({ type: 'ping' }), PING_INTERVAL_MS);
    });

    socket.addEventListener('message', (event) => {
      if (typeof event.data === 'string') {
        let parsed;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          return;
        }
        this.handlers.onEvent(parsed);
      } else {
        this.handlers.onAudio(event.data);
      }
    });

    socket.addEventListener('close', () => {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
      this.socket = null;
      this.handlers.onState('closed');
      if (!this.closedByUs) this.scheduleReconnect();
    });

    socket.addEventListener('error', () => {
      // 'close' always follows, which is where reconnection is handled.
    });
  }

  scheduleReconnect() {
    this.attempt += 1;
    const delay = Math.min(RETRY_BASE_MS * 2 ** (this.attempt - 1), RETRY_MAX_MS);
    window.setTimeout(() => {
      if (!this.closedByUs) this.connect();
    }, delay);
  }

  /** @param {object} payload */
  send(payload) {
    if (!this.ready) return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }

  /** @param {ArrayBuffer|Int16Array} chunk */
  sendAudio(chunk) {
    if (!this.ready) return false;
    this.socket.send(chunk);
    return true;
  }

  close() {
    this.closedByUs = true;
    if (this.socket) this.socket.close();
  }
}
