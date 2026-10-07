# ASR model artifact pinning and mirroring

Principle: weights are fetched once, at image BUILD time, pinned by revision and checksum, and baked into the image. The running task has no internet path (no NAT) and downloads nothing.

## Pin (RAN locally except where marked)

1. Choose repo and revision: `metis:speechModelRepo` (default `Systran/faster-whisper-small.en`) and a commit SHA for `metis:speechModelRevision`. `main` is rejected for prod.
2. Build once to learn the digest (needs network, ~500 MB download):
   ```bash
   docker build --platform linux/amd64 -t metis-speech:pin \
     --build-arg MODEL_ID=small.en --build-arg MODEL_REPO=Systran/faster-whisper-small.en \
     --build-arg MODEL_REVISION=<commit-sha> services/speech
   ```
   The build prints `model.bin sha256=...` and warns when `MODEL_SHA256` is empty. Record that value as `metis:speechModelSha256`.
3. Rebuild with `--build-arg MODEL_SHA256=<digest>`; the build fails on mismatch. Note the Dockerfile verifies `model.bin` only; tokenizer/config files are covered by the revision pin.
4. Run `make speech-bench` against real hypotheses before trusting the model (without `--hypotheses` it echoes the reference and says so). Real-model WER is UNRUN.

## Mirror (recommended before prod) - UNRUN

Reasons: Hugging Face availability, repo rewrites, and build reproducibility.

1. Download the pinned snapshot, verify the sha256, upload to a private S3 bucket (or a private ECR "model" image layer) under `models/<model_id>/<revision>/`.
2. Point the Dockerfile `models` stage at the mirror in a follow-up change owned by the speech worker (not done here; `services/speech` is outside this task's paths).
3. Keep the mirror bucket private, versioned, and out of the sources bucket so user-data deletion never touches it.

## Release the image - UNRUN (needs AWS credentials, approval)

```bash
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com
docker tag metis-speech:pin <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/metis-<env>-speech:<revision-short>
docker push <ACCOUNT>.dkr.ecr.<REGION>.amazonaws.com/metis-<env>-speech:<revision-short>
aws ecr describe-images --repository-name metis-<env>-speech --image-ids imageTag=<revision-short> --query 'imageDetails[0].imageDigest'
```

The repository is created by the speech stack, so the first deploy must precede the first push, and the first deploy uses the placeholder digest (`sha256:000...`): the task definition then references a missing image and any ASR execution fails visibly. Push, then redeploy with `-c metis:speechImageDigest=sha256:<digest>`. Tags are immutable; the task definition pins the digest.

Rollback = redeploy with the previous digest. Keep the last 10 images (lifecycle rule).
