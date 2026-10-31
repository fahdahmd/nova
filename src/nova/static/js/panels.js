/**
 * Side panels, settings drawer and toasts.
 *
 * Every node is created programmatically with textContent, so nothing from the
 * database or the model is ever interpreted as markup.
 */

const el = (id) => document.getElementById(id);

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
const TIME = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/** @param {string} iso */
export function formatWhen(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const diffSeconds = (date.getTime() - Date.now()) / 1000;
  const absolute = Math.abs(diffSeconds);
  if (absolute < 60) return RELATIVE.format(Math.round(diffSeconds), 'second');
  if (absolute < 3600) return RELATIVE.format(Math.round(diffSeconds / 60), 'minute');
  if (absolute < 86400) return RELATIVE.format(Math.round(diffSeconds / 3600), 'hour');
  if (absolute < 604800) return RELATIVE.format(Math.round(diffSeconds / 86400), 'day');
  return TIME.format(date);
}

function emptyRow(message) {
  const row = document.createElement('li');
  row.className = 'empty-row';
  row.textContent = message;
  return row;
}

function iconButton(label, glyph, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'row-btn';
  button.setAttribute('aria-label', label);
  button.title = label;
  button.textContent = glyph;
  button.addEventListener('click', onClick);
  return button;
}

export class Panels {
  /** @param {{api: object, onToast: Function}} options */
  constructor({ api, onToast }) {
    this.api = api;
    this.toast = onToast;
    this.state = { notes: [], todos: [], reminders: [], memory: [], audit: [] };
    this.setupTabs();
    this.setupForms();
  }

  setupTabs() {
    this.tabs = Array.from(document.querySelectorAll('.tab'));
    this.tabs.forEach((tab, position) => {
      tab.addEventListener('click', () => this.selectTab(tab));
      tab.addEventListener('keydown', (event) => {
        const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!offset) return;
        event.preventDefault();
        const next = this.tabs[(position + offset + this.tabs.length) % this.tabs.length];
        next.focus();
        this.selectTab(next);
      });
    });
  }

  selectTab(tab) {
    for (const candidate of this.tabs) {
      const selected = candidate === tab;
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      const panel = el(candidate.getAttribute('aria-controls'));
      if (panel) panel.hidden = !selected;
    }
  }

  setupForms() {
    el('todo-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = el('todo-input');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      await this.api.post('/api/todos', { text });
      await this.refresh();
    });

    el('note-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = el('note-input');
      const title = input.value.trim();
      if (!title) return;
      input.value = '';
      await this.api.post('/api/notes', { title, body: '' });
      await this.refresh();
    });

    el('clear-audit').addEventListener('click', async () => {
      await this.api.remove('/api/audit');
      await this.refresh();
      this.toast({ kind: 'success', title: 'Activity cleared' });
    });

    el('clear-memory').addEventListener('click', async () => {
      if (!window.confirm('Delete every remembered fact? This cannot be undone.')) return;
      await this.api.remove('/api/memory');
      await this.refresh();
      this.toast({ kind: 'success', title: 'Memory cleared' });
    });
  }

  async refresh() {
    const data = await this.api.get('/api/state');
    if (!data) return null;
    this.state = data;
    this.renderTodos(data.todos || []);
    this.renderNotes(data.notes || []);
    this.renderReminders(data.reminders || []);
    this.renderMemory(data.memory || []);
    this.renderAudit(data.audit || []);
    this.renderConfig(data.config || {});
    return data;
  }

  // ------------------------------------------------------------------ todos
  renderTodos(todos) {
    const list = el('todos-list');
    list.replaceChildren();
    const open = todos.filter((todo) => !todo.done);
    el('count-todos').textContent = String(open.length);

    if (todos.length === 0) {
      list.append(emptyRow('Nothing to do.'));
      return;
    }

    for (const todo of todos) {
      const row = document.createElement('li');

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = todo.done;
      checkbox.disabled = todo.done;
      checkbox.setAttribute('aria-label', `Complete: ${todo.text}`);
      checkbox.addEventListener('change', async () => {
        await this.api.post(`/api/todos/${todo.id}/complete`, null);
        await this.refresh();
      });

      const body = document.createElement('span');
      body.className = 'grow';
      const label = document.createElement('span');
      label.textContent = todo.text;
      if (todo.done) label.className = 'done';
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = formatWhen(todo.created_at);
      body.append(label, meta);

      row.append(
        checkbox,
        body,
        iconButton(`Delete: ${todo.text}`, '×', async () => {
          await this.api.remove(`/api/todos/${todo.id}`);
          await this.refresh();
        }),
      );
      list.append(row);
    }
  }

  // ------------------------------------------------------------------ notes
  renderNotes(notes) {
    const list = el('notes-list');
    list.replaceChildren();
    el('count-notes').textContent = String(notes.length);

    if (notes.length === 0) {
      list.append(emptyRow('No notes yet.'));
      return;
    }

    for (const note of notes) {
      const row = document.createElement('li');
      const body = document.createElement('span');
      body.className = 'grow';

      const title = document.createElement('strong');
      title.textContent = note.title;
      body.append(title);

      if (note.body) {
        const preview = document.createElement('span');
        preview.className = 'meta';
        preview.textContent = note.body.length > 120
          ? `${note.body.slice(0, 120)}…`
          : note.body;
        body.append(preview);
      }

      const when = document.createElement('span');
      when.className = 'meta';
      when.textContent = formatWhen(note.created_at);
      body.append(when);

      row.append(
        body,
        iconButton(`Delete note: ${note.title}`, '×', async () => {
          await this.api.remove(`/api/notes/${note.id}`);
          await this.refresh();
        }),
      );
      list.append(row);
    }
  }

  // -------------------------------------------------------------- reminders
  renderReminders(reminders) {
    const list = el('reminders-list');
    list.replaceChildren();
    const pending = reminders.filter((item) => item.status === 'pending');
    el('count-reminders').textContent = String(pending.length);

    if (reminders.length === 0) {
      list.append(emptyRow('No reminders set.'));
      return;
    }

    for (const reminder of reminders) {
      const row = document.createElement('li');
      const body = document.createElement('span');
      body.className = 'grow';

      const label = document.createElement('span');
      label.textContent = reminder.text;
      if (reminder.status !== 'pending') label.className = 'done';

      const meta = document.createElement('span');
      meta.className = 'meta';
      const when = formatWhen(reminder.due_at);
      meta.textContent = reminder.status === 'pending' ? when : `${reminder.status} · ${when}`;
      body.append(label, meta);
      row.append(body);

      if (reminder.status === 'pending') {
        row.append(
          iconButton(`Cancel reminder: ${reminder.text}`, '×', async () => {
            await this.api.remove(`/api/reminders/${reminder.id}`);
            await this.refresh();
          }),
        );
      }
      list.append(row);
    }
  }

  // ----------------------------------------------------------------- memory
  renderMemory(memory) {
    const list = el('memory-list');
    list.replaceChildren();
    if (memory.length === 0) {
      list.append(emptyRow('Nothing remembered yet.'));
      return;
    }
    for (const fact of memory) {
      const row = document.createElement('li');
      const body = document.createElement('span');
      body.className = 'grow';
      body.textContent = fact.content;
      row.append(
        body,
        iconButton('Forget this', '×', async () => {
          await this.api.remove(`/api/memory/${fact.id}`);
          await this.refresh();
        }),
      );
      list.append(row);
    }
  }

  // ------------------------------------------------------------------ audit
  renderAudit(audit) {
    const list = el('audit-list');
    list.replaceChildren();
    if (audit.length === 0) {
      list.append(emptyRow('No tool calls yet.'));
      return;
    }
    for (const entry of audit) {
      const row = document.createElement('li');

      const top = document.createElement('span');
      top.className = 'audit-top';

      const status = document.createElement('span');
      status.className = 'status-badge';
      status.dataset.status = entry.status;
      status.textContent = entry.status;

      const risk = document.createElement('span');
      risk.className = 'risk-badge';
      risk.dataset.risk = entry.risk;
      risk.textContent = entry.risk;

      const name = document.createElement('span');
      name.className = 'grow';
      name.textContent = entry.tool;

      const when = document.createElement('span');
      when.className = 'meta';
      when.textContent = formatWhen(entry.created_at);

      top.append(status, risk, name, when);

      const args = document.createElement('span');
      args.className = 'args';
      args.textContent = entry.args_preview || '{}';

      row.append(top, args);

      if (entry.detail) {
        const detail = document.createElement('span');
        detail.className = 'args';
        detail.textContent = entry.detail;
        row.append(detail);
      }
      list.append(row);
    }
  }

  // ----------------------------------------------------------------- config
  renderConfig(config) {
    const roots = el('roots-list');
    roots.replaceChildren();
    for (const root of config.allowed_roots || []) {
      const row = document.createElement('li');
      row.textContent = root;
      roots.append(row);
    }
    if (!(config.allowed_roots || []).length) roots.append(emptyRow('No folders allowed.'));

    const apps = el('apps-list');
    apps.replaceChildren();
    for (const app of config.allowed_apps || []) {
      const row = document.createElement('li');
      row.textContent = `${app.app} → ${app.label}`;
      apps.append(row);
    }
    if (!(config.allowed_apps || []).length) apps.append(emptyRow('No apps whitelisted.'));

    const facts = el('model-facts');
    facts.replaceChildren();
    const rows = [
      ['Model', config.model],
      ['Voice', config.voice_available ? config.voice : 'not installed'],
      ['Speech input', config.stt_available ? 'faster-whisper' : 'disabled'],
      ['Weather', config.weather_enabled ? 'on' : 'off'],
      ['Web search', config.web_search_enabled ? 'on' : 'off'],
      ['Tools', String((config.tools || []).length)],
    ];
    for (const [key, value] of rows) {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = value ?? '-';
      facts.append(dt, dd);
    }

    const note = el('voice-note');
    note.textContent = config.voice_available
      ? ''
      : 'No Piper voice installed - run python scripts/download_voice.py to enable speech.';
  }
}

/* ───────────────────────────────── toasts ───────────────────────────────── */
export function showToast({ kind = 'info', title = '', body = '', timeout = 6000 }) {
  const container = el('toasts');
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.dataset.kind = kind;
  toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');

  const content = document.createElement('div');
  content.className = 'grow';
  if (title) {
    const heading = document.createElement('strong');
    heading.textContent = title;
    content.append(heading);
  }
  if (body) {
    const paragraph = document.createElement('p');
    paragraph.textContent = body;
    content.append(paragraph);
  }

  const close = iconButton('Dismiss', '×', () => dismiss());
  toast.append(content, close);
  container.append(toast);

  let timer = null;
  function dismiss() {
    if (timer) window.clearTimeout(timer);
    toast.classList.add('leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    window.setTimeout(() => toast.remove(), 400);
  }

  if (timeout > 0) timer = window.setTimeout(dismiss, timeout);
  return dismiss;
}
