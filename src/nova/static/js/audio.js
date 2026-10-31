/**
 * Microphone capture and speech playback.
 *
 * Capture: getUserMedia -> AudioWorklet -> 16 kHz PCM16 frames -> WebSocket.
 * Playback: PCM16 chunks from the server are queued and scheduled on an
 * AudioContext, with an analyser so the visualiser can follow the voice.
 */

const WORKLET_URL = '/static/js/mic-worklet.js';

export class Microphone {
  /** @param {(pcm: Int16Array, level: number) => void} onFrame */
  constructor(onFrame) {
    this.onFrame = onFrame;
    this.context = null;
    this.stream = null;
    this.node = null;
    this.source = null;
    this.active = false;
    this.level = 0;
  }

  get supported() {
    return Boolean(navigator.mediaDevices && window.AudioWorkletNode);
  }

  async start() {
    if (this.active) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.context = new AudioContext();
    await this.context.audioWorklet.addModule(WORKLET_URL);

    this.source = this.context.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.context, 'mic-processor');
    this.node.port.onmessage = (event) => {
      const { pcm, level } = event.data;
      this.level = level;
      this.onFrame(pcm, level);
    };

    this.source.connect(this.node);
    // Keep the graph alive without routing the microphone to the speakers.
    const sink = this.context.createGain();
    sink.gain.value = 0;
    this.node.connect(sink);
    sink.connect(this.context.destination);

    this.active = true;
  }

  async stop() {
    this.active = false;
    this.level = 0;
    if (this.node) {
      this.node.port.onmessage = null;
      this.node.disconnect();
      this.node = null;
    }
    if (this.source) {
      this.source.disconnect();
      this.source = null;
    }
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
    if (this.context) {
      await this.context.close().catch(() => undefined);
      this.context = null;
    }
  }
}

export class Playback {
  constructor() {
    this.context = null;
    this.analyser = null;
    this.gain = null;
    this.sources = new Set();
    this.nextStart = 0;
    this.sampleRate = 22050;
    this.rate = 1;
    this.muted = false;
    this.levels = null;
  }

  ensureContext() {
    if (this.context) return this.context;
    this.context = new AudioContext();
    this.gain = this.context.createGain();
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.75;
    this.levels = new Uint8Array(this.analyser.frequencyBinCount);
    this.gain.connect(this.analyser);
    this.analyser.connect(this.context.destination);
    return this.context;
  }

  /** @param {number} sampleRate */
  begin(sampleRate) {
    this.sampleRate = sampleRate || this.sampleRate;
    const context = this.ensureContext();
    if (context.state === 'suspended') context.resume().catch(() => undefined);
    this.nextStart = Math.max(context.currentTime + 0.08, this.nextStart);
  }

  /** @param {ArrayBuffer} buffer raw little-endian PCM16 */
  enqueue(buffer) {
    if (this.muted) return;
    const context = this.ensureContext();
    const samples = new Int16Array(buffer);
    if (samples.length === 0) return;

    const audio = context.createBuffer(1, samples.length, this.sampleRate);
    const channel = audio.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 32768;

    const source = context.createBufferSource();
    source.buffer = audio;
    source.playbackRate.value = this.rate;
    source.connect(this.gain);

    const startAt = Math.max(context.currentTime + 0.02, this.nextStart);
    source.start(startAt);
    this.nextStart = startAt + audio.duration / this.rate;

    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /** Stop everything at once - this is what makes barge-in feel instant. */
  stop() {
    for (const source of this.sources) {
      try {
        source.onended = null;
        source.stop();
      } catch {
        /* already finished */
      }
    }
    this.sources.clear();
    this.nextStart = this.context ? this.context.currentTime : 0;
  }

  get playing() {
    return this.sources.size > 0;
  }

  /** @returns {number} 0..1 output loudness for the visualiser */
  outputLevel() {
    if (!this.analyser || !this.levels || this.sources.size === 0) return 0;
    this.analyser.getByteFrequencyData(this.levels);
    let total = 0;
    for (let i = 0; i < this.levels.length; i += 1) total += this.levels[i];
    return Math.min(1, total / this.levels.length / 110);
  }

  setRate(rate) {
    this.rate = rate;
    for (const source of this.sources) source.playbackRate.value = rate;
  }

  setMuted(muted) {
    this.muted = muted;
    if (muted) this.stop();
  }
}
