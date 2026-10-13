#!/usr/bin/env bash
# Provision (idempotent) and deploy Metis to one private GCE VM.
#
#   PROJECT=<gcp-project-id> ops/gcp/deploy.sh
#
# What runs there: METIS_MODE=local (SQLite, filesystem objects, fixture
# inference) with the real faster-whisper transcriber. Fixture identity means
# there is NO login, so the VM has no external IP and every service binds to
# loopback; the only way in is SSH through IAP. Do not open a public port.
#
# Ships committed HEAD (git archive), not the working tree.
set -euo pipefail
cd "$(dirname "$0")/../.."

PROJECT="${PROJECT:?set PROJECT to the GCP project id}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-$REGION-a}"
VM="${VM:-metis}"
# ponytail: e2-medium (2 shared vCPU, 4 GB) fits small.en int8 at concurrency 1;
# move to e2-standard-2+ if transcription latency or a larger model demands it.
MACHINE="${MACHINE:-e2-medium}"

g() { gcloud --project "$PROJECT" --quiet "$@"; }
ensure() { # ensure <describe args...> -- <create args...>
  local d=() ; while [ "$1" != "--" ]; do d+=("$1"); shift; done; shift
  g "${d[@]}" >/dev/null 2>&1 || g "$@"
}

g services enable compute.googleapis.com iap.googleapis.com

ensure compute networks describe metis -- \
  compute networks create metis --subnet-mode=custom
ensure compute networks subnets describe metis --region "$REGION" -- \
  compute networks subnets create metis --network metis --region "$REGION" --range 10.10.0.0/24
# Only Google's IAP TCP-forwarding range may reach SSH. Nothing else ingresses.
ensure compute firewall-rules describe metis-iap-ssh -- \
  compute firewall-rules create metis-iap-ssh --network metis \
  --allow tcp:22 --source-ranges 35.235.240.0/20
# Egress only (apt, PyPI, model weights at deploy time).
ensure compute routers describe metis --region "$REGION" -- \
  compute routers create metis --network metis --region "$REGION"
ensure compute routers nats describe metis --router metis --region "$REGION" -- \
  compute routers nats create metis --router metis --region "$REGION" \
  --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges
ensure compute instances describe "$VM" --zone "$ZONE" -- \
  compute instances create "$VM" --zone "$ZONE" --machine-type "$MACHINE" \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
  --boot-disk-size 30GB --boot-disk-type pd-balanced \
  --subnet metis --no-address --no-service-account --no-scopes \
  --shielded-secure-boot --metadata enable-oslogin=TRUE

# Build the web client from the same commit, in a clean export.
stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
git archive HEAD | tar -x -C "$stage"
(cd "$stage" && pnpm install --frozen-lockfile --filter @metis/web... && pnpm --filter @metis/web build)
tar -czf "$stage.tar.gz" -C "$stage" --exclude node_modules --exclude infra --exclude 'apps/desktop' .

ssh_ready() { g compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --command true 2>/dev/null; }
for _ in $(seq 20); do ssh_ready && break; sleep 6; done

g compute scp --zone "$ZONE" --tunnel-through-iap "$stage.tar.gz" "$VM:/tmp/metis.tar.gz"
g compute scp --zone "$ZONE" --tunnel-through-iap ops/gcp/vm-setup.sh "$VM:/tmp/vm-setup.sh"
rm -f "$stage.tar.gz"
g compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --command 'sudo bash /tmp/vm-setup.sh'

cat <<EOF

Deployed. Open a tunnel, then browse http://localhost:5173 :

  gcloud --project $PROJECT compute ssh $VM --zone $ZONE --tunnel-through-iap -- \\
    -N -L 5173:localhost:5173 -L 8000:localhost:8000
EOF
