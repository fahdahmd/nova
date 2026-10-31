/**
 * AudioWorklet processor: resample microphone audio to 16 kHz mono PCM16.
 *
 * Runs on the audio thread, so the main thread never blocks on capture. Output
 * is posted back as Int16 buffers of ~32 ms, plus a running input level used by
 * the visualiser.
 */

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 512; // 32 ms at 16 kHz

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.position = 0;
    this.out = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.peak = 0;
    this.frames = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    // Linear resampling from the device rate down to 16 kHz.
    while (this.position < channel.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const current = channel[index];
      const next = index + 1 < channel.length ? channel[index + 1] : current;
      const sample = current + (next - current) * fraction;

      const magnitude = Math.abs(sample);
      if (magnitude > this.peak) this.peak = magnitude;

      const clamped = Math.max(-1, Math.min(1, sample));
      this.out[this.filled] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      this.filled += 1;

      if (this.filled === FRAME_SAMPLES) {
        const copy = this.out.slice(0);
        this.port.postMessage({ pcm: copy, level: this.peak }, [copy.buffer]);
        this.filled = 0;
        this.peak = 0;
      }

      this.position += this.ratio;
    }

    this.position -= channel.length;
    return true;
  }
}

registerProcessor('mic-processor', MicProcessor);
