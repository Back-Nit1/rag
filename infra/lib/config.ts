import { Stack } from 'aws-cdk-lib';
import { Construct } from 'constructs';

/**
 * Deployment configuration, read from CDK context so nothing environment
 * specific is hard-coded in a stack. Prices and model ids in particular are
 * configuration: verify them against the selected region's rate card and
 * model availability before trusting any estimate derived from them.
 */
export interface MetisConfig {
  readonly envName: 'dev' | 'staging' | 'prod';
  readonly region: string;
  readonly alertEmail?: string;
  readonly monthlyBudgetUsd: number;
  readonly speechCpu: number;
  readonly speechMemoryMiB: number;
  /** A warm EC2 ASR service instead of per-job Fargate tasks. Idle compute is
   * billable, so this stays off unless steady usage justifies it. */
  readonly warmSpeechEc2: boolean;
  readonly speechModelId: string;
  readonly speechModelRepo: string;
  readonly speechModelRevision: string;
  readonly speechModelSha256: string;
  readonly extractionModelId: string;
  readonly planningModelId: string;
  readonly embeddingModelId: string;
  readonly embeddingDimensions: number;
  /** Digest (sha256:...) of the speech image pushed to ECR. */
  readonly speechImageDigest: string;
  /** Browser origins allowed by CORS and the Cognito callback list. */
  readonly webOrigins: string[];
  /** SSM SecureString holding the web-push VAPID private key (created by hand). */
  readonly vapidPrivateKeyParam: string;
  readonly vapidPublicKey: string;
  /** Lambda reserved concurrency; 0 leaves a function unreserved. */
  readonly outboxConcurrency: number;
  readonly reminderConcurrency: number;
  readonly dailyPlanConcurrency: number;
  /** `docker` bundles for real; `stub` is for `cdk synth` only and must never be deployed. */
  readonly bundling: 'docker' | 'stub';
}

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const UNSET_DIGEST = `sha256:${'0'.repeat(64)}`;

export function readConfig(scope: Construct): MetisConfig {
  const ctx = (key: string) => scope.node.tryGetContext(`metis:${key}`);
  const envName = (ctx('env') ?? 'dev') as MetisConfig['envName'];
  const num = (key: string, dflt: number) => Number(ctx(key) ?? dflt);
  const config: MetisConfig = {
    envName,
    region: process.env.CDK_DEFAULT_REGION ?? ctx('defaultRegion') ?? 'us-east-1',
    alertEmail: ctx('alertEmail') || undefined,
    monthlyBudgetUsd: Number(ctx('monthlyBudgetUsd') ?? 25),
    speechCpu: Number(ctx('speechCpu') ?? 2048),
    speechMemoryMiB: Number(ctx('speechMemoryMiB') ?? 4096),
    warmSpeechEc2: Boolean(ctx('warmSpeechEc2')),
    speechModelId: ctx('speechModelId') ?? 'small.en',
    speechModelRepo: ctx('speechModelRepo') ?? 'Systran/faster-whisper-small.en',
    speechModelRevision: ctx('speechModelRevision') ?? 'main',
    speechModelSha256: ctx('speechModelSha256') ?? '',
    extractionModelId: ctx('extractionModelId') ?? 'amazon.nova-micro-v1:0',
    planningModelId: ctx('planningModelId') ?? 'amazon.nova-lite-v1:0',
    embeddingModelId: ctx('embeddingModelId') ?? 'amazon.titan-embed-text-v2:0',
    embeddingDimensions: Number(ctx('embeddingDimensions') ?? 512),
    speechImageDigest: ctx('speechImageDigest') || UNSET_DIGEST,
    webOrigins: String(ctx('webOrigins') ?? 'http://localhost:5173').split(',').map((o) => o.trim()).filter(Boolean),
    vapidPrivateKeyParam: ctx('vapidPrivateKeyParam') ?? `/metis/${envName}/vapid-private-key`,
    vapidPublicKey: ctx('vapidPublicKey') ?? '',
    outboxConcurrency: num('outboxConcurrency', 2),
    reminderConcurrency: num('reminderConcurrency', 3),
    dailyPlanConcurrency: num('dailyPlanConcurrency', 1),
    bundling: (ctx('bundling') ?? 'docker') as MetisConfig['bundling'],
  };
  validateConfig(config);
  return config;
}

/** Fails at synth time, before any template exists, on a value that would deploy something unsafe. */
export function validateConfig(c: MetisConfig): void {
  const bad: string[] = [];
  if (!['dev', 'staging', 'prod'].includes(c.envName)) bad.push(`metis:env must be dev|staging|prod, got ${c.envName}`);
  if (!(c.monthlyBudgetUsd > 0)) bad.push('metis:monthlyBudgetUsd must be > 0');
  if (![256, 512, 1024, 2048, 4096].includes(c.speechCpu)) bad.push('metis:speechCpu must be a Fargate size (256..4096)');
  if (!(c.speechMemoryMiB >= 512)) bad.push('metis:speechMemoryMiB must be >= 512');
  if (![256, 512, 1024].includes(c.embeddingDimensions)) bad.push('metis:embeddingDimensions must be 256, 512 or 1024 (Titan v2)');
  if (!SHA256_DIGEST.test(c.speechImageDigest)) bad.push('metis:speechImageDigest must be sha256:<64 hex>');
  if (c.speechModelSha256 && !/^[0-9a-f]{64}$/.test(c.speechModelSha256)) bad.push('metis:speechModelSha256 must be 64 hex chars');
  if (c.warmSpeechEc2) bad.push('metis:warmSpeechEc2 is not implemented; the selected ASR boundary is finite Fargate');
  for (const [k, v] of Object.entries({ outbox: c.outboxConcurrency, reminder: c.reminderConcurrency, dailyPlan: c.dailyPlanConcurrency })) {
    if (!Number.isInteger(v) || v < 0) bad.push(`metis:${k}Concurrency must be an integer >= 0`);
  }
  if (!['docker', 'stub'].includes(c.bundling)) bad.push('metis:bundling must be docker|stub');
  if (c.envName === 'prod') {
    if (c.speechImageDigest === UNSET_DIGEST) bad.push('prod requires metis:speechImageDigest');
    if (!c.speechModelSha256) bad.push('prod requires metis:speechModelSha256');
    if (c.speechModelRevision === 'main') bad.push('prod requires a pinned metis:speechModelRevision (a commit, not main)');
    if (!c.alertEmail) bad.push('prod requires metis:alertEmail');
    if (c.bundling === 'stub') bad.push('prod must not use stub bundling');
    if (c.webOrigins.some((o) => !o.startsWith('https://'))) bad.push('prod metis:webOrigins must be https');
  }
  if (bad.length) throw new Error(`invalid Metis configuration:\n - ${bad.join('\n - ')}`);
}

/**
 * `metis-<env>-<name>`, so two environments can share an account safely.
 *
 * The stack is taken but unused: every caller already passes `this`, and the
 * name is intentionally stack-independent so moving a resource between stacks
 * does not rename (and therefore replace) it.
 */
export function resourceName(_stack: Stack, config: MetisConfig, name: string): string {
  return `metis-${config.envName}-${name}`;
}

export const isProd = (config: MetisConfig): boolean => config.envName === 'prod';
