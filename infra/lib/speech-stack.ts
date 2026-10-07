import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as tasks from 'aws-cdk-lib/aws-stepfunctions-tasks';
import { Construct } from 'constructs';
import { MetisConfig, isProd, resourceName } from './config';
import { pythonFunction } from './python-function';

export interface SpeechStackProps extends StackProps {
  readonly config: MetisConfig;
  readonly vpc: ec2.Vpc;
  readonly taskSecurityGroup: ec2.SecurityGroup;
  readonly table: dynamodb.Table;
  readonly sourcesBucket: s3.Bucket;
  readonly key: kms.Key;
}

/**
 * The selected ASR boundary: a Step Functions Standard machine runs one finite
 * Fargate task per recording (`ecs:runTask.sync`), then ALWAYS calls the
 * finalize Lambda with an AsrTaskOutcome, however the task ended. The Lambda
 * verifies the persisted result; RunTask returning is never treated as success.
 *
 * The launcher (outbox Lambda) starts executions named by job id, so a
 * duplicate start collapses (ExecutionAlreadyExists).
 */
export class SpeechStack extends Stack {
  public readonly machine: sfn.StateMachine;
  public readonly finalizeFn: ReturnType<typeof pythonFunction>;
  public readonly repository: ecr.Repository;

  constructor(scope: Construct, id: string, props: SpeechStackProps) {
    super(scope, id, props);
    const { config, key, sourcesBucket } = props;

    this.repository = new ecr.Repository(this, 'Repo', {
      repositoryName: resourceName(this, config, 'speech'),
      imageScanOnPush: true,
      imageTagMutability: ecr.TagMutability.IMMUTABLE,
      encryption: ecr.RepositoryEncryption.KMS,
      encryptionKey: key,
      removalPolicy: isProd(config) ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      emptyOnDelete: !isProd(config),
      lifecycleRules: [{ maxImageCount: 10 }],
    });

    const cluster = new ecs.Cluster(this, 'Cluster', {
      clusterName: resourceName(this, config, 'speech'),
      vpc: props.vpc,
    });

    const taskLogs = new logs.LogGroup(this, 'TaskLogs', {
      logGroupName: `/metis/${config.envName}/speech-task`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const taskDef = new ecs.FargateTaskDefinition(this, 'TaskDef', {
      family: resourceName(this, config, 'speech'),
      cpu: config.speechCpu,
      memoryLimitMiB: config.speechMemoryMiB,
      runtimePlatform: { cpuArchitecture: ecs.CpuArchitecture.X86_64, operatingSystemFamily: ecs.OperatingSystemFamily.LINUX },
    });
    // Model weights are baked into the image (see ops/model-artifacts.md); the
    // task has no internet path and downloads nothing at runtime.
    taskDef.addContainer('asr', {
      containerName: 'asr',
      image: ecs.ContainerImage.fromEcrRepository(this.repository, config.speechImageDigest),
      logging: ecs.LogDrivers.awsLogs({ logGroup: taskLogs, streamPrefix: 'asr' }),
      environment: {
        METIS_MODE: 'cloud',
        METIS_ENV: config.envName,
        METIS_SOURCES_BUCKET: sourcesBucket.bucketName,
        METIS_SPEECH_MODEL_ID: config.speechModelId,
        METIS_SPEECH_ARTIFACT_REVISION: config.speechModelRevision,
      },
    });
    // The worker reads audio and writes its result JSON; nothing else.
    sourcesBucket.grantRead(taskDef.taskRole, 'users/*');
    sourcesBucket.grantPut(taskDef.taskRole, 'users/*');
    key.grantEncryptDecrypt(taskDef.taskRole);

    // ---- finalize Lambda ------------------------------------------------
    this.finalizeFn = pythonFunction(this, 'SpeechFinalize', {
      config,
      handler: 'metis_api.lambda_entry.speech_finalize',
      description: 'Verify the persisted ASR result and apply or fail the transcription job',
      timeout: Duration.seconds(60),
      memorySize: 512,
      environment: {
        METIS_MODE: 'cloud',
        METIS_ENV: config.envName,
        METIS_TABLE_NAME: props.table.tableName,
        METIS_SOURCES_BUCKET: sourcesBucket.bucketName,
      },
    });
    props.table.grantReadWriteData(this.finalizeFn);
    sourcesBucket.grantRead(this.finalizeFn);
    key.grantEncryptDecrypt(this.finalizeFn);

    // ---- state machine --------------------------------------------------
    const outcome = (status: string, failure: string | null) =>
      new sfn.Pass(this, `Outcome${status}`, {
        parameters: {
          'user_id.$': '$.user_id',
          'job_id.$': '$.job_id',
          status,
          failure,
          // Never forward the task's Cause/stderr: it is untrusted and may be large.
          error_message: failure ? `ASR task ${status}` : null,
        },
      });

    const finalize = (name: string) =>
      new tasks.LambdaInvoke(this, name, { lambdaFunction: this.finalizeFn, payloadResponseOnly: true }).addRetry({
        errors: ['Lambda.ServiceException', 'Lambda.TooManyRequestsException', 'Lambda.SdkClientException', 'States.TaskFailed'],
        interval: Duration.seconds(5),
        maxAttempts: 3,
        backoffRate: 2,
      });

    const run = new tasks.EcsRunTask(this, 'RunAsrTask', {
      integrationPattern: sfn.IntegrationPattern.RUN_JOB,
      cluster,
      taskDefinition: taskDef,
      launchTarget: new tasks.EcsFargateLaunchTarget({ platformVersion: ecs.FargatePlatformVersion.LATEST }),
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [props.taskSecurityGroup],
      assignPublicIp: false,
      containerOverrides: [
        {
          containerDefinition: taskDef.defaultContainer!,
          // The whole execution input is the TranscriptionJobInput the worker validates.
          environment: [{ name: 'METIS_JOB_INPUT', value: sfn.JsonPath.jsonToString(sfn.JsonPath.entirePayload) }],
        },
      ],
      taskTimeout: sfn.Timeout.duration(Duration.minutes(15)),
      resultPath: sfn.JsonPath.DISCARD,
    });
    // Only launch-side capacity errors are retried; an ASR failure is deterministic.
    run.addRetry({ errors: ['ECS.AmazonECSException', 'ECS.InvalidParameterException'], interval: Duration.seconds(30), maxAttempts: 2, backoffRate: 2 });
    run.addCatch(outcome('timed_out', 'provider_unavailable').next(finalize('FinalizeTimedOut')), { errors: ['States.Timeout'], resultPath: sfn.JsonPath.DISCARD });
    run.addCatch(outcome('failed', 'provider_unavailable').next(finalize('FinalizeFailed')), { errors: ['States.ALL'], resultPath: sfn.JsonPath.DISCARD });

    const succeeded = outcome('succeeded', null).next(finalize('FinalizeSucceeded'));
    const definition = run.next(succeeded);

    const machineLogs = new logs.LogGroup(this, 'MachineLogs', {
      logGroupName: `/aws/vendedlogs/states/${resourceName(this, config, 'speech')}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.machine = new sfn.StateMachine(this, 'Machine', {
      stateMachineName: resourceName(this, config, 'speech'),
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(definition),
      timeout: Duration.minutes(25),
      tracingEnabled: true,
      logs: { destination: machineLogs, level: sfn.LogLevel.ERROR, includeExecutionData: false },
    });

    new CfnOutput(this, 'SpeechStateMachineArn', { value: this.machine.stateMachineArn, description: 'METIS_SPEECH_STATE_MACHINE_ARN' });
    new CfnOutput(this, 'SpeechRepositoryUri', { value: this.repository.repositoryUri });
  }
}
