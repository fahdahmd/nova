/**
 * Nova UI controller.
 *
 * Owns the WebSocket, the message list, the orb state machine, the microphone,
 * playback, the settings drawer and the confirmation modal.
 */

import { Connection } from './ws.js';
import { Microphone, Playback } from './audio.js';
import { Orb } from './orb.js';
import { renderMarkdown } from './markdown.js';
import { Panels, showToast, formatWhen } from './panels.js';

const el = (id) => document.getElementById(id);

/* ─────────────────────────────── tiny api client ─────────────────────────── */
const api = {
  async request(method, path, body) {
    const options = {
      method,
      credentials: 'same-origin',
      headers: { 'X-Requested-With': 'nova' },
    };
    if (body !== undefined && body !== null) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (response.status === 401) {
      showToast({
        kind: 'error',
        title: 'Session expired',
        body: 'Reopen the tokenized link Nova printed at startup.',
        timeout: 0,
      });
      return null;
    }
    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const payload = await response.json();
        if (payload && payload.detail) detail = payload.detail;
      } catch {
        /* keep the generic message */
      }
      showToast({ kind: 'error', title: 'Something went wrong', body: detail });
      return null;
    }
    if (response.status === 204) return true;
    const text = await response.text();
    return text ? JSON.parse(text) : true;
  },
  get(path) {
    return this.request('GET', path);
  },
  post(path, body) {
    return this.request('POST', path, body);
  },
  remove(path) {
    return this.request('DELETE', path);
  },
};

/* ──────────────────────────────── app state ─────────────────────────────── */
const ui = {
  messages: el('messages'),
  emptyState: el('empty-state'),
  composer: el('composer'),
  input: el('composer-input'),
  send: el('send-btn'),
  mic: el('mic-btn'),
  stop: el('stop-btn'),
  connection: el('connection'),
  stateLabel: el('state-label'),
  modelLabel: el('model-label'),
  confirmDialog: el('confirm-dialog'),
};

const orb = new Orb(el('orb'));
const playback = new Playback();
let streamingBubble = null;
let assistantState = 'idle';
let recording = false;
let handsFree = false;
let voiceEnabled = true;
let pendingConfirm = null;
let config = {};

const microphone = new Microphone((pcm) => {
  if (!recording) return;
  connection.sendAudio(pcm.buffer);
});

/* ─────────────────────────────── connection ─────────────────────────────── */
const connection = new Connection({
  onState: setConnectionState,
  onEvent: handleEvent,
  onAudio: (buffer) => playback.enqueue(buffer),
});

function setConnectionState(state) {
  ui.connection.dataset.state = state;
  const label = { connecting: 'Connecting', open: 'Connected', closed: 'Reconnecting' }[state];
  ui.connection.querySelector('.conn-text').textContent = label;
  const offline = state !== 'open';
  ui.send.disabled = offline;
  ui.mic.disabled = offline || !config.stt_available || !microphone.supported;
  if (offline) setState('idle');
}

/* ──────────────────────────────── messages ─────────────────────────────── */
function hideEmptyState() {
  ui.emptyState.hidden = true;
}

function scrollToEnd() {
  ui.messages.scrollTo({ top: ui.messages.scrollHeight, behavior: 'smooth' });
}

function addMessage(role, text, { streaming = false } = {}) {
  hideEmptyState();
  const item = document.createElement('li');
  item.className = `msg ${role}`;
  if (streaming) item.classList.add('streaming');

  const label = document.createElement('span');
  label.className = 'msg-role';
  label.textContent = role === 'user' ? 'You' : config.app_name || 'Nova';

  const body = document.createElement('div');
  body.className = 'msg-body';
  if (text) body.append(renderMarkdown(text));

  item.append(label, body);
  ui.messages.append(item);
  scrollToEnd();
  return { item, body, raw: text || '' };
}

function appendToken(text) {
  if (!streamingBubble) streamingBubble = addMessage('assistant', '', { streaming: true });
  streamingBubble.raw += text;
  streamingBubble.body.replaceChildren(renderMarkdown(streamingBubble.raw));
  scrollToEnd();
}

function finishStreaming() {
  if (!streamingBubble) return;
  streamingBubble.item.classList.remove('streaming');
  if (!streamingBubble.raw.trim()) streamingBubble.item.remove();
  streamingBubble = null;
}

function discardStreaming() {
  if (!streamingBubble) return;
  streamingBubble.item.remove();
  streamingBubble = null;
}

function addToolTrace(tool, summary) {
  hideEmptyState();
  const item = document.createElement('li');
  item.className = 'msg tool-trace';
  item.dataset.tool = tool;

  const spark = document.createElement('span');
  spark.className = 'spark';

  const name = document.createElement('span');
  name.className = 'mono';
  name.textContent = tool;

  const detail = document.createElement('span');
  detail.textContent = summary || 'working…';

  item.append(spark, name, detail);
  ui.messages.append(item);
  scrollToEnd();
  return { item, detail };
}

const activeTraces = new Map();

/* ───────────────────────────── state machine ───────────────────────────── */
const STATE_LABELS = {
  idle: 'Ready',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
};

function setState(state, detail = '') {
  assistantState = state;
  orb.setState(state);
  ui.stateLabel.textContent = detail
    ? `${STATE_LABELS[state] || state} · ${detail}`
    : STATE_LABELS[state] || state;
  ui.stop.hidden = state === 'idle';
}

/* ───────────────────────────── event handling ──────────────────────────── */
function handleEvent(event) {
  switch (event.type) {
    case 'ready':
      onReady(event);
      break;

    case 'status':
      setState(event.state, event.detail);
      break;

    case 'token':
      appendToken(event.text);
      break;

    case 'stream_reset':
      discardStreaming();
      break;

    case 'message':
      finishStreaming();
      addMessage(event.role, event.content);
      break;

    case 'transcript':
      addMessage('user', event.text);
      break;

    case 'tool_start': {
      const trace = addToolTrace(event.tool, event.summary);
      activeTraces.set(event.tool, trace);
      break;
    }

    case 'tool_end': {
      const trace = activeTraces.get(event.tool);
      if (trace) {
        trace.item.dataset.ok = String(event.ok);
        trace.detail.textContent = event.ok
          ? `done in ${event.duration_ms} ms`
          : event.detail || 'failed';
        activeTraces.delete(event.tool);
      }
      break;
    }

    case 'confirm_request':
      openConfirm(event);
      break;

    case 'refresh':
      panels.refresh();
      break;

    case 'reminder':
      onReminder(event);
      break;

    case 'audio_header':
      playback.begin(event.sample_rate);
      break;

    case 'audio_end':
      if (event.interrupted) playback.stop();
      if (assistantState === 'speaking') setState('idle');
      break;

    case 'error':
      finishStreaming();
      showToast({
        kind: 'error',
        title: event.message,
        body: event.hint || '',
        timeout: event.hint ? 11000 : 7000,
      });
      setState('idle');
      break;

    case 'pong':
    default:
      break;
  }

  if (event.type === 'status' && event.state === 'idle') finishStreaming();
}

function onReady(event) {
  config = {
    app_name: event.app_name,
    stt_available: event.stt_available,
    voice_available: event.voice_available,
  };
  ui.modelLabel.textContent = event.model;
  ui.modelLabel.title = `Model: ${event.model}`;
  document.title = event.app_name;
  el('app-name').textContent = event.app_name;

  const preferences = event.preferences || {};
  applyPreferences(preferences, event);

  ui.mic.disabled = !event.stt_available || !microphone.supported;
  if (!microphone.supported) {
    ui.mic.title = 'This browser does not support microphone capture';
  } else if (!event.stt_available) {
    ui.mic.title = 'Speech to text is turned off in the settings';
  }

  el('set-voice-enabled').disabled = !event.voice_available;
  el('set-hands-free').disabled = !event.hands_free_available;
  setState('idle');
}

function onReminder(event) {
  showToast({
    kind: 'reminder',
    title: 'Reminder',
    body: `${event.text} · ${formatWhen(event.due_at)}`,
    timeout: 0,
  });
  panels.refresh();
}

/* ──────────────────────────── confirmation modal ───────────────────────── */
function openConfirm(request) {
  pendingConfirm = request;

  el('confirm-tool').textContent = request.tool;
  const risk = el('confirm-risk');
  risk.textContent = request.risk;
  risk.dataset.risk = request.risk;
  el('confirm-prompt').textContent = request.prompt;
  el('confirm-warning').hidden = !request.from_untrusted_content;

  const args = el('confirm-args');
  args.replaceChildren();
  for (const [key, value] of Object.entries(request.arguments || {})) {
    if (value === null || value === undefined || value === '') continue;
    const dt = document.createElement('dt');
    dt.textContent = key;
    const dd = document.createElement('dd');
    dd.textContent = typeof value === 'string' ? value : JSON.stringify(value);
    args.append(dt, dd);
  }

  ui.confirmDialog.showModal();
  el('confirm-deny').focus();
}

ui.confirmDialog.addEventListener('close', () => {
  if (!pendingConfirm) return;
  const approved = ui.confirmDialog.returnValue === 'approve';
  connection.send({
    type: 'confirm',
    request_id: pendingConfirm.request_id,
    approved,
  });
  if (!approved) setState('idle');
  pendingConfirm = null;
});

/* ───────────────────────────────── sending ─────────────────────────────── */
function sendMessage(text) {
  const message = text.trim();
  if (!message) return;
  if (!connection.ready) {
    showToast({ kind: 'error', title: 'Not connected', body: 'Reconnecting - try again shortly.' });
    return;
  }
  playback.stop();
  addMessage('user', message);
  connection.send({ type: 'chat', text: message });
  setState('thinking');
}

ui.composer.addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage(ui.input.value);
  ui.input.value = '';
  resizeInput();
});

ui.input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    ui.composer.requestSubmit();
  }
});

function resizeInput() {
  ui.input.style.height = 'auto';
  ui.input.style.height = `${Math.min(ui.input.scrollHeight, 160)}px`;
}
ui.input.addEventListener('input', resizeInput);

for (const button of document.querySelectorAll('#suggestions button')) {
  button.addEventListener('click', () => sendMessage(button.dataset.prompt || ''));
}

/* ──────────────────────────────── microphone ───────────────────────────── */
async function startRecording() {
  if (recording || ui.mic.disabled) return;
  playback.stop();
  connection.send({ type: 'cancel' });

  try {
    await microphone.start();
  } catch (error) {
    showToast({
      kind: 'error',
      title: 'Microphone unavailable',
      body: 'Grant microphone access for this page, then try again.',
    });
    return;
  }

  recording = true;
  ui.mic.setAttribute('aria-pressed', 'true');
  connection.send({
    type: 'audio_start',
    mode: handsFree ? 'hands_free' : 'push_to_talk',
    sample_rate: 16000,
  });
  setState('listening');
  pollMicLevel();
}

async function stopRecording({ cancelled = false } = {}) {
  if (!recording) return;
  recording = false;
  ui.mic.setAttribute('aria-pressed', 'false');
  await microphone.stop();
  connection.send({ type: 'audio_end', cancelled });
  if (cancelled) setState('idle');
}

function pollMicLevel() {
  if (!recording) {
    orb.setLevel(0);
    return;
  }
  orb.setLevel(microphone.level * 2.2);
  requestAnimationFrame(pollMicLevel);
}

ui.mic.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  if (handsFree) {
    if (recording) stopRecording();
    else startRecording();
    return;
  }
  startRecording();
});

for (const name of ['pointerup', 'pointerleave', 'pointercancel']) {
  ui.mic.addEventListener(name, () => {
    if (!handsFree) stopRecording();
  });
}

ui.stop.addEventListener('click', () => interrupt());

function interrupt() {
  playback.stop();
  connection.send({ type: 'cancel' });
  if (recording) stopRecording({ cancelled: true });
  finishStreaming();
  setState('idle');
}

/* ──────────────────────────── keyboard shortcuts ───────────────────────── */
function typingInField(target) {
  return (
    target instanceof HTMLElement &&
    (target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.isContentEditable)
  );
}

let spaceHeld = false;

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    if (ui.confirmDialog.open) return;
    if (!el('settings-drawer').hidden) {
      closeSettings();
      return;
    }
    interrupt();
    return;
  }

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    if (recording) stopRecording();
    else startRecording();
    return;
  }

  if (event.code === 'Space' && !typingInField(event.target) && !spaceHeld) {
    if (ui.confirmDialog.open) return;
    event.preventDefault();
    spaceHeld = true;
    startRecording();
  }
});

window.addEventListener('keyup', (event) => {
  if (event.code === 'Space' && spaceHeld) {
    spaceHeld = false;
    if (!handsFree) stopRecording();
  }
});

/* ───────────────────────────────── settings ────────────────────────────── */
function openSettings() {
  el('settings-drawer').hidden = false;
  el('settings-drawer').setAttribute('aria-hidden', 'false');
  el('drawer-backdrop').hidden = false;
  el('settings-toggle').setAttribute('aria-expanded', 'true');
  el('settings-close').focus();
}

function closeSettings() {
  el('settings-drawer').hidden = true;
  el('settings-drawer').setAttribute('aria-hidden', 'true');
  el('drawer-backdrop').hidden = true;
  el('settings-toggle').setAttribute('aria-expanded', 'false');
  el('settings-toggle').focus();
}

el('settings-toggle').addEventListener('click', () => {
  if (el('settings-drawer').hidden) openSettings();
  else closeSettings();
});
el('settings-close').addEventListener('click', closeSettings);
el('drawer-backdrop').addEventListener('click', closeSettings);

function savePreference(key, value) {
  connection.send({ type: 'preference', key, value: String(value) });
  api.post('/api/preferences', { key, value: String(value) });
}

function applyPreferences(preferences, ready) {
  const theme = preferences.theme || 'dark';
  document.documentElement.dataset.theme = theme;
  updateThemeButton(theme);

  voiceEnabled = preferences.voice_enabled !== 'false' && Boolean(ready.voice_available);
  el('set-voice-enabled').checked = voiceEnabled;

  const muted = preferences.muted === 'true';
  el('set-muted').checked = muted;
  playback.setMuted(muted);

  const rate = Number(preferences.speech_rate || '1') || 1;
  el('set-rate').value = String(rate);
  el('rate-value').textContent = `${rate.toFixed(1)}×`;
  playback.setRate(rate);

  handsFree = preferences.hands_free === 'true';
  el('set-hands-free').checked = handsFree;

  el('set-thinking').checked = preferences.thinking === 'true' || Boolean(ready.thinking);
}

el('set-voice-enabled').addEventListener('change', (event) => {
  voiceEnabled = event.target.checked;
  savePreference('voice_enabled', voiceEnabled);
  if (!voiceEnabled) playback.stop();
});

el('set-muted').addEventListener('change', (event) => {
  playback.setMuted(event.target.checked);
  savePreference('muted', event.target.checked);
});

el('set-rate').addEventListener('input', (event) => {
  const rate = Number(event.target.value);
  el('rate-value').textContent = `${rate.toFixed(1)}×`;
  playback.setRate(rate);
});
el('set-rate').addEventListener('change', (event) => {
  savePreference('speech_rate', Number(event.target.value).toFixed(1));
});

el('set-hands-free').addEventListener('change', (event) => {
  handsFree = event.target.checked;
  savePreference('hands_free', handsFree);
  ui.mic.title = handsFree
    ? 'Click to start, it stops on silence'
    : 'Hold to talk (Space), or Ctrl+K';
});

el('set-thinking').addEventListener('change', (event) => {
  savePreference('thinking', event.target.checked);
});

el('clear-chat').addEventListener('click', async () => {
  if (!window.confirm('Clear the conversation history?')) return;
  await api.remove('/api/messages');
  ui.messages.replaceChildren();
  ui.emptyState.hidden = false;
  showToast({ kind: 'success', title: 'Conversation cleared' });
});

/* ────────────────────────────────── theme ─────────────────────────────── */
function updateThemeButton(theme) {
  const button = el('theme-toggle');
  const next = theme === 'dark' ? 'light' : 'dark';
  button.setAttribute('aria-label', `Switch to ${next} theme`);
}

el('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  updateThemeButton(next);
  savePreference('theme', next);
});

/* ──────────────────────────── speaking indicator ───────────────────────── */
function followPlayback() {
  if (playback.playing) {
    orb.setLevel(playback.outputLevel());
    if (assistantState !== 'speaking' && !recording) setState('speaking');
  } else if (assistantState === 'speaking' && !recording) {
    orb.setLevel(0);
    setState('idle');
  }
  requestAnimationFrame(followPlayback);
}

/* ──────────────────────────────── bootstrap ────────────────────────────── */
const panels = new Panels({ api, onToast: showToast });

async function restoreHistory() {
  const data = await api.get('/api/messages');
  if (!data || !Array.isArray(data.messages) || data.messages.length === 0) return;
  for (const message of data.messages) {
    if (message.role === 'user' || message.role === 'assistant') {
      addMessage(message.role, message.content);
    }
  }
  hideEmptyState();
}

async function boot() {
  orb.start();
  setState('idle');
  followPlayback();
  await panels.refresh();
  await restoreHistory();
  connection.connect();
}

boot();
