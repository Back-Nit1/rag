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
}

export function readConfig(scope: Construct, envName: MetisConfig['envName']): MetisConfig {
  const ctx = (key: string) => scope.node.tryGetContext(`metis:${key}`);
  return {
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
  };
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
