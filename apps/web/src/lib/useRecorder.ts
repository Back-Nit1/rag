/**
 * Microphone capture with MediaRecorder.
 *
 * Cancel really discards: the chunks are dropped and the track is stopped, so
 * a canceled recording has nothing to upload. The duration and the limit are
 * reported continuously because the user must see the cap before hitting it.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderState = "unsupported" | "idle" | "requesting" | "denied" | "recording" | "paused";

export type Recording = { blob: Blob; durationSeconds: number };

const TICK_MS = 200;

export function useRecorder(maxSeconds: number) {
  const supported =
    typeof window !== "undefined" && typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices;
  const [state, setState] = useState<RecorderState>(supported ? "idle" : "unsupported");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | null>(null);
  const startedAt = useRef<number>(0);
  const elapsedBeforePause = useRef<number>(0);
  const discarded = useRef(false);

  const stopTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const teardown = useCallback(() => {
    stopTimer();
    recorder.current?.stream.getTracks().forEach((track) => track.stop());
    recorder.current = null;
  }, [stopTimer]);

  useEffect(() => teardown, [teardown]);

  const start = useCallback(async () => {
    if (!supported) return;
    setError(null);
    setState("requesting");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (cause) {
      setState("denied");
      setError(cause instanceof Error ? cause.message : "the microphone is unavailable");
      return;
    }
    chunks.current = [];
    discarded.current = false;
    elapsedBeforePause.current = 0;
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    const instance = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    instance.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.current.push(event.data);
    };
    recorder.current = instance;
    instance.start(1000);
    startedAt.current = Date.now();
    setSeconds(0);
    setState("recording");
    timer.current = window.setInterval(() => {
      const elapsed = elapsedBeforePause.current + (Date.now() - startedAt.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxSeconds) instance.stop();
    }, TICK_MS);
  }, [maxSeconds, supported]);

  const pause = useCallback(() => {
    if (recorder.current?.state !== "recording") return;
    recorder.current.pause();
    elapsedBeforePause.current += (Date.now() - startedAt.current) / 1000;
    stopTimer();
    setState("paused");
  }, [stopTimer]);

  const resume = useCallback(() => {
    const instance = recorder.current;
    if (instance?.state !== "paused") return;
    instance.resume();
    startedAt.current = Date.now();
    setState("recording");
    timer.current = window.setInterval(() => {
      const elapsed = elapsedBeforePause.current + (Date.now() - startedAt.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxSeconds) instance.stop();
    }, TICK_MS);
  }, [maxSeconds]);

  /** Resolves with the recording, or null when it was canceled. */
  const stop = useCallback((): Promise<Recording | null> => {
    const instance = recorder.current;
    if (!instance || instance.state === "inactive") return Promise.resolve(null);
    const duration = elapsedBeforePause.current + (instance.state === "recording" ? (Date.now() - startedAt.current) / 1000 : 0);
    return new Promise((resolve) => {
      instance.onstop = () => {
        const parts = chunks.current;
        teardown();
        setState("idle");
        setSeconds(0);
        if (discarded.current || parts.length === 0) return resolve(null);
        resolve({ blob: new Blob(parts, { type: instance.mimeType || "audio/webm" }), durationSeconds: duration });
      };
      instance.stop();
    });
  }, [teardown]);

  const cancel = useCallback(() => {
    discarded.current = true;
    chunks.current = [];
    const instance = recorder.current;
    if (instance && instance.state !== "inactive") instance.stop();
    teardown();
    setState("idle");
    setSeconds(0);
  }, [teardown]);

  return { state, seconds, error, supported, start, pause, resume, stop, cancel };
}
