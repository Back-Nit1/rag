# Usage: METIS_SPEECH_MODEL_DIR=<dir containing small.en/> METIS_SPEECH_ARTIFACT_REVISION=<sha> python bench_real.py <audio_dir> <out_dir>
"""Warm in-process bench: load time, first (cold) decode, 3 warm passes, hypotheses."""
import json, platform, sys, time, statistics, os
from pathlib import Path
t0 = time.monotonic()
from metis_speech.transcriber import FasterWhisperTranscriber
import_s = time.monotonic() - t0
audio = Path(sys.argv[1]); out = Path(sys.argv[2])
files = sorted(audio.glob("*.webm"))
t = FasterWhisperTranscriber()
t0 = time.monotonic(); t.warm(); load_s = time.monotonic() - t0
rows, hyp = [], {}
for p in files:  # pass 0 = first decode after load
    t0 = time.monotonic(); text, segs, meta = t.transcribe(p.read_bytes(), max_seconds=300); wall = time.monotonic() - t0
    rows.append(dict(id=p.stem, pass_=0, wall=wall, decode=meta["decode_seconds"], audio=meta["audio_seconds"], no_speech=meta.get("no_speech", False), cold=meta["cold_start"]))
    hyp[p.stem] = text
    if p == files[0]: meta0 = meta
for n in (1, 2, 3):
    for p in files:
        t0 = time.monotonic(); text, _, meta = t.transcribe(p.read_bytes(), max_seconds=300); wall = time.monotonic() - t0
        rows.append(dict(id=p.stem, pass_=n, wall=wall, decode=meta["decode_seconds"], audio=meta["audio_seconds"], no_speech=meta.get("no_speech", False), cold=meta["cold_start"]))
        if text != hyp[p.stem]: print("NONDETERMINISTIC", p.stem, file=sys.stderr)
out.joinpath("hypotheses.json").write_text(json.dumps(hyp, indent=1))
speech = [r for r in rows if not r["no_speech"]]
def stats(rs):
    w = sorted(r["wall"] for r in rs); a = sum(r["audio"] for r in rs)
    return dict(n=len(rs), wall_mean=statistics.mean(w), wall_p50=w[len(w)//2], wall_p95=w[int(len(w)*.95)-1], wall_max=w[-1], rtf=sum(w)/a)
res = dict(import_seconds=import_s, model_load_seconds=load_s,
  first_decode=stats([r for r in speech if r["pass_"] == 0]),
  first_file_first_decode_wall=rows[0]["wall"],
  warm=stats([r for r in speech if r["pass_"] > 0]),
  silence_wall=[r["wall"] for r in rows if r["no_speech"]][:4],
  no_speech_ids=sorted({r["id"] for r in rows if r["no_speech"]}),
  env=dict(os=platform.platform(), machine=platform.machine(), python=sys.version.split()[0], cpu_count=os.cpu_count(), meta_sample=meta0))
import faster_whisper, ctranslate2, av, numpy
res["env"]["versions"] = dict(faster_whisper=faster_whisper.__version__, ctranslate2=ctranslate2.__version__, av=av.__version__, numpy=numpy.__version__)
out.joinpath("bench.json").write_text(json.dumps(res, indent=1)); out.joinpath("rows.json").write_text(json.dumps(rows))
print(json.dumps(res, indent=1))
