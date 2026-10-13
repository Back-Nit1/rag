#!/usr/bin/env bash
# Publish the static UI to Vercel, pointed at the VM's public API host.
#   PROJECT=<gcp-project-id> ops/vercel/deploy.sh
# The bundle holds the API URL and a "key required" flag, never a secret.
set -euo pipefail
cd "$(dirname "$0")/../.."
PROJECT="${PROJECT:?set PROJECT to the GCP project id}"
IP="$(gcloud --project "$PROJECT" compute addresses describe metis --region "${REGION:-us-central1}" --format='value(address)')"
export VITE_METIS_API="https://${IP//./-}.sslip.io" VITE_METIS_ACCESS_KEY=required

pnpm --filter @metis/web build
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
cp -R apps/web/dist/. "$out/" && cp ops/vercel/vercel.json "$out/"
cd "$out"
vercel link --yes --project "${VERCEL_PROJECT:-metis}" >/dev/null
rm -f .env.local  # `vercel link` writes an OIDC token here; it must not be deployed
vercel deploy --prod --yes
