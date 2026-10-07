# Secrets and rotation

V1 needs no Anthropic, OpenAI, speech, vector-SaaS or calendar keys. Cloud components use IAM roles; there are no long-lived AWS keys in the browser or in Lambda environment variables.

## Inventory

| Secret / key | Purpose | Storage | Rotation |
| --- | --- | --- | --- |
| VAPID private key | web-push signing | SSM SecureString `/metis/<env>/vapid-private-key` (name = `metis:vapidPrivateKeyParam`); created by hand, never by CloudFormation | manual, below |
| VAPID public key | browser subscription | `metis:vapidPublicKey` context, delivered to the web app | with the private key |
| Cognito tokens | user auth | issued by Cognito, 1 h access, 30 d refresh, revocation enabled | refresh revocation: `aws cognito-idp admin-user-global-sign-out` |
| KMS data key | table, bucket, ECR at rest | CMK with automatic yearly rotation | automatic |
| Desktop auth/speech secrets | local desktop shell only | per-launch; not deployed | n/a |
| Deployer AWS credentials | `cdk deploy` | SSO profile on the operator machine | per account policy |

Only the outbox and reminder-due roles can read the VAPID parameter (`ssm:GetParameter` on that one ARN). The KMS grant covers decryption of a SecureString encrypted with the default SSM key only if the key policy allows it; if the parameter uses the account's `aws/ssm` key no extra grant is needed. If it uses a customer key, add that key's grant - UNRUN check.

## Create (UNRUN)

```bash
# generate a P-256 VAPID pair with any standard tool; keep the private half off disk afterwards
aws ssm put-parameter --name /metis/<env>/vapid-private-key --type SecureString --value "$VAPID_PRIVATE" --overwrite
```

Then set `metis:vapidPublicKey` and redeploy.

## Rotate VAPID (UNRUN)

Rotating the key invalidates every existing push subscription (a subscription is bound to the public key).

1. Generate a new pair; `put-parameter --overwrite`.
2. Redeploy with the new `metis:vapidPublicKey`.
3. Clients re-subscribe on next load. Until they do, delivery returns a subscription error that the delivery handler must surface as a delivery failure, not "sent".
4. Cold Lambdas read the parameter again on start; to force it, update any function configuration (a redeploy does).

## If a secret leaks

Rotate immediately; for deployer credentials revoke the SSO session/keys first. Check CloudTrail for `ssm:GetParameter` on the parameter and for unfamiliar `bedrock:InvokeModel` volume. Never paste secrets into issues, logs or this repo; logs must not contain tokens, prompts, transcripts or raw sources.
