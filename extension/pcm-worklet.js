/**
 * AudioWorklet processor: batches 128-frame render quanta into fixed-size
 * Int16 PCM frames (default 40 ms) and posts them to the main thread with
 * a transferable buffer. Runs on the audio thread, so it adds no latency
 * beyond the frame size itself.
 *
 * The AudioContext that hosts this node is created at 16 kHz, so `sampleRate`
 * here is 16000 and Chrome resamples the tab stream for us.
 */
class PcmFrameProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const frameMs = (options && options.processorOptions && options.processorOptions.frameMs) || 40;
    this.frameSamples = Math.round(sampleRate * frameMs / 1000);
    this.buffer = new Int16Array(this.frameSamples);
    this.index = 0;
    this.framesSent = 0;
    this.sumSquares = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const channels = input.length;
    const ch0 = input[0];
    for (let i = 0; i < ch0.length; i++) {
      // downmix to mono
      let s = ch0[i];
      if (channels > 1) {
        for (let c = 1; c < channels; c++) s += input[c][i];
        s /= channels;
      }
      s = Math.max(-1, Math.min(1, s));
      this.sumSquares += s * s;
      this.buffer[this.index++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.index === this.frameSamples) {
        const rms = Math.sqrt(this.sumSquares / this.frameSamples);
        this.port.postMessage(
          { pcm: this.buffer.buffer, rms: rms, t: currentTime },
          [this.buffer.buffer]
        );
        this.buffer = new Int16Array(this.frameSamples);
        this.index = 0;
        this.sumSquares = 0;
        this.framesSent++;
      }
    }
    return true;
  }
}

registerProcessor('pcm-frame-processor', PcmFrameProcessor);
