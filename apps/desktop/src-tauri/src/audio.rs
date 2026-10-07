//! Audio capture: a source trait, a bounded 16 kHz mono recorder and WAV encode.
//!
//! The microphone is opened only after a key-down and the recorder starts empty,
//! so nothing recorded before the keypress can ever exist (ADR 0004 decision 9).
//! Hardware is behind [`AudioSource`]; tests and the scripted E2E use
//! [`WavSource`], the real device is [`CpalSource`].
use std::io::Cursor;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

pub const TARGET_RATE: u32 = 16_000;
/// Hard bound on one recording (server limit is 300 s by default).
pub const MAX_RECORD_SECS: usize = 120;
pub const MAX_SAMPLES: usize = MAX_RECORD_SECS * TARGET_RATE as usize;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Format {
    pub rate: u32,
    pub channels: u16,
}

pub type Sink = Box<dyn FnMut(&[f32]) + Send>;

/// A producer of interleaved f32 samples. Not `Send`: CoreAudio streams are
/// bound to the thread that built them, so the controller owns the source.
pub trait AudioSource {
    /// Native device format. Errors when no device or no usable format exists.
    fn format(&mut self) -> Result<Format, String>;
    /// Start delivering interleaved samples to `sink`.
    fn start(&mut self, sink: Sink) -> Result<(), String>;
    /// Stop and release the device. Idempotent.
    fn stop(&mut self);
    /// A device error reported after `start`, if any.
    fn failed(&self) -> Option<String> {
        None
    }
    /// True for real hardware that needs OS microphone consent.
    fn needs_permission(&self) -> bool {
        false
    }
}

/// Streaming area-average resampler (also correct for non-integer ratios and
/// for upsampling) from `rate` to [`TARGET_RATE`], fed mono samples.
#[derive(Debug)]
pub struct Resampler {
    step: f64,
    acc: f64,
    covered: f64,
}

impl Resampler {
    pub fn new(rate: u32) -> Self {
        Self {
            step: rate as f64 / TARGET_RATE as f64,
            acc: 0.0,
            covered: 0.0,
        }
    }

    pub fn push(&mut self, s: f32, out: &mut impl FnMut(f32)) {
        let s = s as f64;
        let mut w = 1.0;
        while w > 1e-9 {
            let need = self.step - self.covered;
            if w >= need - 1e-9 {
                out(((self.acc + s * need) / self.step) as f32);
                self.acc = 0.0;
                self.covered = 0.0;
                w -= need;
            } else {
                self.acc += s * w;
                self.covered += w;
                w = 0.0;
            }
        }
    }
}

/// Downmixes, resamples to 16 kHz mono i16 and enforces the sample bound.
#[derive(Debug)]
pub struct Recorder {
    channels: usize,
    resampler: Resampler,
    samples: Vec<i16>,
    cap: usize,
    truncated: bool,
}

impl Recorder {
    pub fn new(fmt: Format, cap: usize) -> Self {
        Self {
            channels: fmt.channels.max(1) as usize,
            resampler: Resampler::new(fmt.rate),
            samples: Vec::new(),
            cap,
            truncated: false,
        }
    }

    /// Returns false once the buffer is full (further audio is dropped).
    pub fn push(&mut self, interleaved: &[f32]) -> bool {
        for frame in interleaved.chunks_exact(self.channels) {
            if self.samples.len() >= self.cap {
                self.truncated = true;
                return false;
            }
            let mono = frame.iter().sum::<f32>() / self.channels as f32;
            let (samples, cap) = (&mut self.samples, self.cap);
            self.resampler.push(mono, &mut |v| {
                if samples.len() < cap {
                    samples.push((v.clamp(-1.0, 1.0) * i16::MAX as f32) as i16);
                }
            });
        }
        self.samples.len() < self.cap
    }

    pub fn len(&self) -> usize {
        self.samples.len()
    }

    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    pub fn is_full(&self) -> bool {
        self.samples.len() >= self.cap
    }

    pub fn truncated(&self) -> bool {
        self.truncated || self.is_full()
    }

    pub fn take_samples(&mut self) -> Vec<i16> {
        std::mem::take(&mut self.samples)
    }

    pub fn into_samples(self) -> Vec<i16> {
        self.samples
    }
}

/// 16 kHz mono 16-bit PCM WAV.
pub fn encode_wav(samples: &[i16]) -> Result<Vec<u8>, String> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: TARGET_RATE,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut cur = Cursor::new(Vec::with_capacity(44 + samples.len() * 2));
    let mut w = hound::WavWriter::new(&mut cur, spec).map_err(|e| e.to_string())?;
    for s in samples {
        w.write_sample(*s).map_err(|e| e.to_string())?;
    }
    w.finalize().map_err(|e| e.to_string())?;
    Ok(cur.into_inner())
}

/// Scripted source: replays a WAV file as if it were the microphone.
/// Delivered synchronously in 20 ms chunks on `start`, so tests are deterministic.
pub struct WavSource {
    path: PathBuf,
}

impl WavSource {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self { path: path.into() }
    }

    fn read(&self) -> Result<(Format, Vec<f32>), String> {
        let mut r = hound::WavReader::open(&self.path).map_err(|e| e.to_string())?;
        let spec = r.spec();
        let samples: Vec<f32> = match spec.sample_format {
            hound::SampleFormat::Float => r
                .samples::<f32>()
                .collect::<Result<_, _>>()
                .map_err(|e| e.to_string())?,
            hound::SampleFormat::Int => {
                let scale = (1i64 << (spec.bits_per_sample - 1)) as f32;
                r.samples::<i32>()
                    .map(|s| s.map(|v| v as f32 / scale))
                    .collect::<Result<_, _>>()
                    .map_err(|e| e.to_string())?
            }
        };
        Ok((
            Format {
                rate: spec.sample_rate,
                channels: spec.channels,
            },
            samples,
        ))
    }
}

impl AudioSource for WavSource {
    fn format(&mut self) -> Result<Format, String> {
        self.read().map(|(f, _)| f)
    }

    fn start(&mut self, mut sink: Sink) -> Result<(), String> {
        let (fmt, samples) = self.read()?;
        let chunk = (fmt.rate as usize / 50) * fmt.channels as usize;
        for c in samples.chunks(chunk.max(fmt.channels as usize)) {
            sink(c);
        }
        Ok(())
    }

    fn stop(&mut self) {}
}

/// The default input device through cpal.
#[derive(Default)]
pub struct CpalSource {
    stream: Option<cpal::Stream>,
    error: Arc<Mutex<Option<String>>>,
    started: Arc<AtomicBool>,
}

impl CpalSource {
    fn device() -> Result<(cpal::Device, cpal::SupportedStreamConfig), String> {
        use cpal::traits::{DeviceTrait, HostTrait};
        let dev = cpal::default_host()
            .default_input_device()
            .ok_or("no input device")?;
        let cfg = dev
            .default_input_config()
            .map_err(|e| format!("input device unusable: {e}"))?;
        Ok((dev, cfg))
    }
}

fn build<T>(
    dev: &cpal::Device,
    cfg: &cpal::StreamConfig,
    mut sink: Sink,
    err: impl FnMut(cpal::StreamError) + Send + 'static,
) -> Result<cpal::Stream, String>
where
    T: cpal::SizedSample + Send + 'static,
    f32: cpal::FromSample<T>,
{
    use cpal::traits::DeviceTrait;
    let mut buf: Vec<f32> = Vec::new();
    dev.build_input_stream(
        cfg,
        move |data: &[T], _| {
            buf.clear();
            buf.extend(data.iter().map(|s| <f32 as cpal::Sample>::from_sample(*s)));
            sink(&buf);
        },
        err,
        None,
    )
    .map_err(|e| format!("cannot open input stream: {e}"))
}

impl AudioSource for CpalSource {
    fn format(&mut self) -> Result<Format, String> {
        let (_, cfg) = Self::device()?;
        Ok(Format {
            rate: cfg.sample_rate(),
            channels: cfg.channels(),
        })
    }

    fn start(&mut self, sink: Sink) -> Result<(), String> {
        use cpal::traits::StreamTrait;
        use cpal::SampleFormat as F;
        let (dev, supported) = Self::device()?;
        let cfg = supported.config();
        let slot = self.error.clone();
        *slot.lock().unwrap() = None;
        let err = move |e: cpal::StreamError| *slot.lock().unwrap() = Some(e.to_string());
        let started = self.started.clone();
        let mut sink = sink;
        let sink: Sink = Box::new(move |s| {
            started.store(true, Ordering::SeqCst);
            sink(s)
        });
        let stream = match supported.sample_format() {
            F::F32 => build::<f32>(&dev, &cfg, sink, err),
            F::I16 => build::<i16>(&dev, &cfg, sink, err),
            F::U16 => build::<u16>(&dev, &cfg, sink, err),
            F::I32 => build::<i32>(&dev, &cfg, sink, err),
            other => Err(format!("unsupported sample format {other:?}")),
        }?;
        stream
            .play()
            .map_err(|e| format!("cannot start input stream: {e}"))?;
        self.stream = Some(stream);
        Ok(())
    }

    fn stop(&mut self) {
        self.stream = None; // dropping the stream releases the device
    }

    fn failed(&self) -> Option<String> {
        self.error.lock().unwrap().clone()
    }

    fn needs_permission(&self) -> bool {
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec(rate: u32, ch: u16, cap: usize) -> Recorder {
        Recorder::new(Format { rate, channels: ch }, cap)
    }

    #[test]
    fn resamples_48k_stereo_to_16k_mono() {
        let mut r = rec(48_000, 2, MAX_SAMPLES);
        // 1 s of a constant 0.5 on both channels
        r.push(&vec![0.5; 48_000 * 2]);
        assert_eq!(r.len(), 16_000);
        let s = r.into_samples();
        assert!(s.iter().all(|v| (*v as i32 - 16_383).abs() <= 1));
    }

    #[test]
    fn resamples_44k1_and_keeps_duration() {
        let mut r = rec(44_100, 1, MAX_SAMPLES);
        r.push(&vec![0.25; 44_100]);
        assert!((r.len() as i64 - 16_000).abs() <= 1, "{}", r.len());
    }

    #[test]
    fn upsamples_8k() {
        let mut r = rec(8_000, 1, MAX_SAMPLES);
        r.push(&vec![0.1; 8_000]);
        assert_eq!(r.len(), 16_000);
    }

    #[test]
    fn stereo_is_averaged() {
        let mut r = rec(16_000, 2, MAX_SAMPLES);
        r.push(&[1.0, -1.0, 1.0, 0.0]);
        assert_eq!(r.into_samples(), vec![0, 16_383]);
    }

    #[test]
    fn chunk_boundaries_do_not_change_output() {
        let sig: Vec<f32> = (0..9_000).map(|i| ((i % 97) as f32) / 100.0).collect();
        let mut a = rec(44_100, 1, MAX_SAMPLES);
        a.push(&sig);
        let mut b = rec(44_100, 1, MAX_SAMPLES);
        for c in sig.chunks(333) {
            b.push(c);
        }
        assert_eq!(a.into_samples(), b.into_samples());
    }

    #[test]
    fn buffer_is_bounded_and_reports_truncation() {
        let mut r = rec(16_000, 1, 1_000);
        assert!(r.push(&vec![0.1; 999]));
        assert!(!r.truncated());
        assert!(!r.push(&vec![0.1; 5_000]));
        assert_eq!(r.len(), 1_000);
        assert!(r.truncated() && r.is_full());
        r.push(&vec![0.1; 100]);
        assert_eq!(r.len(), 1_000, "never grows past the cap");
    }

    #[test]
    fn max_bound_is_two_minutes() {
        assert_eq!(MAX_SAMPLES, 1_920_000);
    }

    #[test]
    fn wav_roundtrip_is_16k_mono_16bit() {
        let wav = encode_wav(&[1, -2, 3]).unwrap();
        let r = hound::WavReader::new(Cursor::new(wav)).unwrap();
        let s = r.spec();
        assert_eq!(
            (s.sample_rate, s.channels, s.bits_per_sample),
            (16_000, 1, 16)
        );
        assert_eq!(r.len(), 3);
    }
}
