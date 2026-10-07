import json, subprocess, sys
from pathlib import Path
out = Path("audio"); out.mkdir(exist_ok=True)
ev = json.load(open("/Users/nityanthmaramreddy/Downloads/Metis/services/speech/fixtures/evaluation_set.json"))["items"]
FF = "/opt/homebrew/opt/ffmpeg@6/bin/ffmpeg"
def run(*a): subprocess.run(a, check=True, capture_output=True)
for it in ev:
    i, cat, ref = it["id"], it["category"], it["reference"]
    aiff, webm = out/f"{i}.aiff", out/f"{i}.webm"
    if cat == "silence":
        run(FF,"-y","-f","lavfi","-i","anullsrc=r=16000:cl=mono","-t","3","-c:a","libopus",str(webm)); continue
    if cat == "nonspeech":
        run(FF,"-y","-f","lavfi","-i","anoisesrc=color=pink:amplitude=0.3:r=16000","-t","3","-c:a","libopus",str(webm)); continue
    txt = ref.replace("...", " [[slnc 800]] ") if cat == "pauses" else ref
    cmd = ["say","-o",str(aiff)] + (["-r","260"] if cat=="fast" else []) + [txt]
    run(*cmd)
    if cat == "noise":  # pink noise mixed ~ 15 dB below speech
        run(FF,"-y","-i",str(aiff),"-f","lavfi","-i","anoisesrc=color=pink:amplitude=0.05:r=22050","-filter_complex","amix=inputs=2:duration=first:normalize=0","-ac","1","-c:a","libopus",str(webm))
    else:
        run(FF,"-y","-i",str(aiff),"-ac","1","-c:a","libopus",str(webm))
    aiff.unlink()
print(len(list(out.glob("*.webm"))))
