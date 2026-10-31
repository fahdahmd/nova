/**
 * The orb: a canvas visualiser that reflects what the assistant is doing.
 *
 *   idle       slow breathing
 *   listening  reacts to microphone level
 *   thinking   travelling pulse around the ring
 *   speaking   waveform driven by the output analyser
 *
 * Honours prefers-reduced-motion by drawing a single static frame.
 */

const TWO_PI = Math.PI * 2;

export class Orb {
  /** @param {HTMLCanvasElement} canvas */
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'idle';
    this.level = 0;
    this.smoothed = 0;
    this.phase = 0;
    this.frame = null;
    this.ratio = window.devicePixelRatio || 1;
    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    this.resize();
    window.addEventListener('resize', () => this.resize());
    window
      .matchMedia('(prefers-reduced-motion: reduce)')
      .addEventListener('change', (event) => {
        this.reduced = event.matches;
        this.restart();
      });
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(120, rect.width || 320);
    const height = Math.max(80, rect.height || 160);
    this.canvas.width = Math.round(width * this.ratio);
    this.canvas.height = Math.round(height * this.ratio);
    this.width = width;
    this.height = height;
    if (this.reduced) this.draw(0);
  }

  palette() {
    const styles = getComputedStyle(document.documentElement);
    return {
      accent: styles.getPropertyValue('--accent').trim() || '#6ea8fe',
      soft: styles.getPropertyValue('--text-dim').trim() || '#6c7793',
      warn: styles.getPropertyValue('--warn').trim() || '#f0b354',
      ok: styles.getPropertyValue('--ok').trim() || '#4ec98b',
    };
  }

  colourFor(colours) {
    if (this.state === 'listening') return colours.ok;
    if (this.state === 'thinking') return colours.warn;
    return colours.accent;
  }

  start() {
    if (this.reduced) {
      this.draw(0);
      return;
    }
    if (this.frame !== null) return;
    const loop = (time) => {
      this.draw(time);
      this.frame = requestAnimationFrame(loop);
    };
    this.frame = requestAnimationFrame(loop);
  }

  stop() {
    if (this.frame !== null) {
      cancelAnimationFrame(this.frame);
      this.frame = null;
    }
  }

  restart() {
    this.stop();
    this.start();
  }

  /** @param {'idle'|'listening'|'thinking'|'speaking'} state */
  setState(state) {
    this.state = state;
    if (this.reduced) this.draw(0);
  }

  /** @param {number} level 0..1 */
  setLevel(level) {
    this.level = Math.max(0, Math.min(1, level));
  }

  draw(time) {
    const { ctx } = this;
    if (!ctx) return;

    const w = this.width;
    const h = this.height;
    ctx.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const cx = w / 2;
    const cy = h / 2;
    const base = Math.min(w, h) * 0.3;
    const colours = this.palette();
    const colour = this.colourFor(colours);

    this.smoothed += (this.level - this.smoothed) * 0.25;
    this.phase = (time || 0) / 1000;

    if (this.state === 'speaking' || this.state === 'listening') {
      this.drawWave(ctx, cx, cy, base, colour);
    } else {
      this.drawRing(ctx, cx, cy, base, colour);
    }
  }

  drawRing(ctx, cx, cy, base, colour) {
    const breathing = this.state === 'thinking' ? 0.06 : 0.035;
    const speed = this.state === 'thinking' ? 2.6 : 0.9;
    const radius = base * (1 + Math.sin(this.phase * speed) * breathing);

    // Outer halo
    const halo = ctx.createRadialGradient(cx, cy, radius * 0.4, cx, cy, radius * 2.1);
    halo.addColorStop(0, this.withAlpha(colour, 0.26));
    halo.addColorStop(1, this.withAlpha(colour, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 2.1, 0, TWO_PI);
    ctx.fill();

    // Core
    const core = ctx.createRadialGradient(
      cx - radius * 0.3, cy - radius * 0.35, radius * 0.1,
      cx, cy, radius,
    );
    core.addColorStop(0, this.withAlpha(colour, 0.95));
    core.addColorStop(1, this.withAlpha(colour, 0.35));
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, TWO_PI);
    ctx.fill();

    // Ring
    ctx.strokeStyle = this.withAlpha(colour, 0.55);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.45, 0, TWO_PI);
    ctx.stroke();

    if (this.state === 'thinking') {
      // A travelling arc, so "working" is unmistakable.
      const start = (this.phase * 1.8) % TWO_PI;
      ctx.strokeStyle = colour;
      ctx.lineWidth = 2.6;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 1.45, start, start + Math.PI * 0.45);
      ctx.stroke();
    }
  }

  drawWave(ctx, cx, cy, base, colour) {
    const amplitude = base * (0.25 + this.smoothed * 1.25);
    const bars = 36;
    const spacing = (base * 3.4) / bars;
    const left = cx - (spacing * (bars - 1)) / 2;

    ctx.fillStyle = this.withAlpha(colour, 0.9);
    for (let i = 0; i < bars; i += 1) {
      const fromCentre = Math.abs(i - (bars - 1) / 2) / ((bars - 1) / 2);
      const envelope = Math.cos(fromCentre * Math.PI * 0.5) ** 1.6;
      const wobble = Math.sin(this.phase * 7 + i * 0.55) * 0.5 + 0.5;
      const height = Math.max(2, amplitude * envelope * (0.35 + wobble * 0.9));
      const x = left + i * spacing;
      const barWidth = Math.max(2, spacing * 0.42);
      ctx.beginPath();
      ctx.roundRect(x - barWidth / 2, cy - height / 2, barWidth, height, barWidth / 2);
      ctx.fill();
    }

    const halo = ctx.createRadialGradient(cx, cy, base * 0.2, cx, cy, base * 2.4);
    halo.addColorStop(0, this.withAlpha(colour, 0.16));
    halo.addColorStop(1, this.withAlpha(colour, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(cx, cy, base * 2.4, 0, TWO_PI);
    ctx.fill();
  }

  /** Accepts #rgb, #rrggbb and anything else (returned unchanged). */
  withAlpha(colour, alpha) {
    const value = colour.trim();
    if (value.startsWith('#')) {
      const hex = value.slice(1);
      const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
      const r = parseInt(full.slice(0, 2), 16);
      const g = parseInt(full.slice(2, 4), 16);
      const b = parseInt(full.slice(4, 6), 16);
      if (![r, g, b].some(Number.isNaN)) return `rgb(${r} ${g} ${b} / ${alpha})`;
    }
    return value;
  }
}
