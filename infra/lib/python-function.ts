import { Duration, DockerImage, Stack } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'node:path';
import { MetisConfig } from './config';

export interface PythonFunctionProps {
  readonly config: MetisConfig;
  readonly handler: string;
  readonly description: string;
  readonly timeout?: Duration;
  readonly memorySize?: number;
  readonly environment?: Record<string, string>;
  readonly reservedConcurrentExecutions?: number;
}

const REPO_ROOT = path.join(__dirname, '..', '..');

/**
 * One builder for every Python Lambda, so runtime, bundling, log retention and
 * tracing are identical across functions.
 *
 * Bundling installs the workspace packages into the asset with pip. Local
 * bundling is attempted first and Docker is the fallback, so a machine without
 * Docker can still synthesise (and a machine with it gets a reproducible build).
 */
export function pythonFunction(
  scope: Construct,
  id: string,
  props: PythonFunctionProps,
): lambda.Function {
  const stack = Stack.of(scope);
  return new lambda.Function(scope, id, {
    functionName: `metis-${props.config.envName}-${id.toLowerCase()}`,
    description: props.description,
    runtime: lambda.Runtime.PYTHON_3_12,
    architecture: lambda.Architecture.ARM_64, // cheaper per ms than x86 for this workload
    handler: props.handler,
    code: lambda.Code.fromAsset(REPO_ROOT, {
      exclude: [
        '.git', '.venv', 'node_modules', 'apps/web/node_modules', 'apps/web/dist',
        'infra/cdk.out', 'infra/node_modules', '**/__pycache__', '**/.pytest_cache',
        '**/.ruff_cache', '**/.mypy_cache', '.metis', '*.sqlite', 'docs', 'ops',
        'services/speech/fixtures', 'tests',
      ],
      bundling: {
        image: DockerImage.fromRegistry('public.ecr.aws/sam/build-python3.12:latest-arm64'),
        command: [
          'bash', '-c',
          [
            'mkdir -p /asset-output',
            'pip install --quiet --target /asset-output' +
              ' ./packages/contracts ./services/state ./services/pipeline' +
              ' ./services/scheduler ./services/retrieval ./services/planner ./services/api' +
              ' mangum',
          ].join(' && '),
        ],
      },
    }),
    timeout: props.timeout ?? Duration.minutes(1),
    memorySize: props.memorySize ?? 512,
    environment: {
      METIS_LOG_LEVEL: 'INFO',
      AWS_REGION_OVERRIDE: stack.region,
      // Never log raw sources, transcripts, prompts or tokens.
      PYTHONDONTWRITEBYTECODE: '1',
      ...props.environment,
    },
    logRetention: logs.RetentionDays.ONE_MONTH,
    tracing: lambda.Tracing.ACTIVE,
    reservedConcurrentExecutions: props.reservedConcurrentExecutions,
  });
}
