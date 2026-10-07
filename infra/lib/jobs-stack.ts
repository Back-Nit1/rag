import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import { MetisConfig, resourceName } from './config';
import { pythonFunction } from './python-function';

export interface JobsStackProps extends StackProps {
  readonly config: MetisConfig;
  readonly table: dynamodb.Table;
  readonly sourcesBucket: s3.Bucket;
  readonly key: kms.Key;
  readonly vectorBucketName: string;
  readonly vectorIndexName: string;
  readonly speechMachine: sfn.StateMachine;
}

/**
 * Background jobs: the outbox dispatcher, the reminder-due target and the
 * daily-planning target, plus the queues and the EventBridge Scheduler group
 * and role the API uses to create per-reminder / per-user schedules.
 *
 * Each function gets only the grants its handler needs. Reserved concurrency
 * bounds fan-out so a retry storm cannot become an uncapped Bedrock or DynamoDB bill.
 */
export class JobsStack extends Stack {
  public readonly ingestQueue: sqs.Queue;
  public readonly ingestDlq: sqs.Queue;
  public readonly outboxFn: lambda.Function;
  public readonly reminderFn: lambda.Function;
  public readonly dailyPlanFn: lambda.Function;
  public readonly schedulerGroupName: string;
  public readonly schedulerRole: iam.Role;

  constructor(scope: Construct, id: string, props: JobsStackProps) {
    super(scope, id, props);
    const { config, table, sourcesBucket, key } = props;
    const arn = (svc: string, rest: string) => `arn:${this.partition}:${svc}:${this.region}:${this.account}:${rest}`;

    // ---- queues ----------------------------------------------------------
    this.ingestDlq = new sqs.Queue(this, 'IngestDlq', {
      queueName: resourceName(this, config, 'ingest-dlq'),
      retentionPeriod: Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });
    // The message is only a wake-up (the outbox row carries retry state), so a
    // small receive count is right: after 5 failed invocations a human looks.
    this.ingestQueue = new sqs.Queue(this, 'IngestQueue', {
      queueName: resourceName(this, config, 'ingest'),
      visibilityTimeout: Duration.minutes(6), // Lambda guidance: at least 6x the 1-minute function timeout
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      deadLetterQueue: { queue: this.ingestDlq, maxReceiveCount: 5 },
    });

    // ---- scheduler group + role names (fixed strings so no function<->role cycle)
    this.schedulerGroupName = resourceName(this, config, 'reminders');
    const schedulerRoleName = resourceName(this, config, 'scheduler');
    const schedulerRoleArn = `arn:${this.partition}:iam::${this.account}:role/${schedulerRoleName}`;

    // ---- shared env ------------------------------------------------------
    const base = {
      METIS_SCHEDULER_GROUP: this.schedulerGroupName,
      METIS_SCHEDULER_ROLE_ARN: schedulerRoleArn,
      METIS_MODE: 'cloud',
      METIS_ENV: config.envName,
      METIS_TABLE_NAME: table.tableName,
      METIS_SOURCES_BUCKET: sourcesBucket.bucketName,
      METIS_INGEST_QUEUE_URL: this.ingestQueue.queueUrl,
      METIS_VECTOR_BUCKET: props.vectorBucketName,
      METIS_VECTOR_INDEX: props.vectorIndexName,
      METIS_EXTRACTION_MODEL_ID: config.extractionModelId,
      METIS_PLANNING_MODEL_ID: config.planningModelId,
      METIS_EMBEDDING_MODEL_ID: config.embeddingModelId,
      METIS_EMBEDDING_DIMENSIONS: String(config.embeddingDimensions),
      METIS_VAPID_PRIVATE_KEY_PARAM: config.vapidPrivateKeyParam,
      METIS_VAPID_PUBLIC_KEY: config.vapidPublicKey,
    };

    // ---- scheduler group + role -----------------------------------------
    new scheduler.CfnScheduleGroup(this, 'ScheduleGroup', { name: this.schedulerGroupName });

    this.reminderFn = pythonFunction(this, 'ReminderDue', {
      config,
      handler: 'metis_api.lambda_entry.reminder_due',
      description: 'EventBridge Scheduler target: deliver one due reminder (no model calls)',
      timeout: Duration.seconds(30),
      memorySize: 512,
      reservedConcurrentExecutions: config.reminderConcurrency,
      environment: base,
    });
    this.dailyPlanFn = pythonFunction(this, 'DailyPlanning', {
      config,
      handler: 'metis_api.lambda_entry.daily_planning',
      description: 'EventBridge Scheduler target: enqueue plan.requested for one user and day',
      timeout: Duration.seconds(30),
      memorySize: 512,
      reservedConcurrentExecutions: config.dailyPlanConcurrency,
      environment: base,
    });

    this.schedulerRole = new iam.Role(this, 'SchedulerRole', {
      roleName: schedulerRoleName,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: 'EventBridge Scheduler: invoke the reminder and daily-planning targets only',
    });
    this.reminderFn.grantInvoke(this.schedulerRole);
    this.dailyPlanFn.grantInvoke(this.schedulerRole);

    // ---- outbox dispatcher ----------------------------------------------
    this.outboxFn = pythonFunction(this, 'Outbox', {
      config,
      handler: 'metis_api.lambda_entry.outbox',
      description: 'Transactional-outbox dispatcher: schedule/index/ASR/export/delivery side effects',
      timeout: Duration.minutes(1),
      memorySize: 1024,
      reservedConcurrentExecutions: config.outboxConcurrency,
      environment: {
        ...base,
        METIS_REMINDER_TARGET_ARN: this.reminderFn.functionArn,
        METIS_DAILY_PLAN_TARGET_ARN: this.dailyPlanFn.functionArn,
        METIS_SPEECH_STATE_MACHINE_ARN: props.speechMachine.stateMachineArn,
      },
    });
    this.outboxFn.addEventSource(
      new SqsEventSource(this.ingestQueue, { batchSize: 10, maxBatchingWindow: Duration.seconds(5), maxConcurrency: Math.max(2, config.outboxConcurrency) }),
    );
    // Timer: picks up rows whose wake-up message was lost or whose retry time has come.
    new events.Rule(this, 'OutboxTimer', {
      ruleName: resourceName(this, config, 'outbox-timer'),
      description: 'Drain due outbox rows even when no SQS wake-up arrives',
      schedule: events.Schedule.rate(Duration.minutes(5)),
      targets: [new targets.LambdaFunction(this.outboxFn, { retryAttempts: 1 })],
    });

    // ---- least-privilege grants -----------------------------------------
    for (const fn of [this.outboxFn, this.reminderFn, this.dailyPlanFn]) {
      table.grantReadWriteData(fn);
      key.grantEncryptDecrypt(fn);
    }
    this.ingestQueue.grantSendMessages(this.dailyPlanFn);
    this.ingestQueue.grantSendMessages(this.reminderFn);
    sourcesBucket.grantReadWrite(this.outboxFn); // export archives, transcripts, deletion
    sourcesBucket.grantRead(this.reminderFn);

    const vapid = arn('ssm', `parameter${config.vapidPrivateKeyParam}`);
    for (const fn of [this.outboxFn, this.reminderFn]) {
      fn.addToRolePolicy(new iam.PolicyStatement({ actions: ['ssm:GetParameter'], resources: [vapid] }));
    }

    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [config.extractionModelId, config.planningModelId, config.embeddingModelId].map(
          (m) => `arn:${this.partition}:bedrock:${this.region}::foundation-model/${m}`,
        ),
      }),
    );
    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3vectors:PutVectors', 's3vectors:QueryVectors', 's3vectors:GetVectors', 's3vectors:DeleteVectors', 's3vectors:GetIndex'],
        resources: [
          arn('s3vectors', `bucket/${props.vectorBucketName}`),
          arn('s3vectors', `bucket/${props.vectorBucketName}/index/${props.vectorIndexName}`),
        ],
      }),
    );
    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['scheduler:CreateSchedule', 'scheduler:UpdateSchedule', 'scheduler:DeleteSchedule', 'scheduler:GetSchedule'],
        resources: [arn('scheduler', `schedule/${this.schedulerGroupName}/*`)],
      }),
    );
    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['iam:PassRole'],
        resources: [schedulerRoleArn],
        conditions: { StringEquals: { 'iam:PassedToService': 'scheduler.amazonaws.com' } },
      }),
    );
    // ASR launch boundary. Execution ARNs derive from the machine ARN.
    const machineArn = props.speechMachine.stateMachineArn;
    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['states:ListExecutions'], resources: [machineArn] }),
    );
    this.outboxFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['states:StartExecution', 'states:StopExecution'],
        resources: [machineArn, `arn:${this.partition}:states:${this.region}:${this.account}:execution:${props.speechMachine.stateMachineName}:*`],
      }),
    );

    // ---- metrics other stacks alarm on ------------------------------------
    new CfnOutput(this, 'IngestQueueUrl', { value: this.ingestQueue.queueUrl });
    new CfnOutput(this, 'IngestDlqUrl', { value: this.ingestDlq.queueUrl });
    new CfnOutput(this, 'SchedulerGroup', { value: this.schedulerGroupName });
    new CfnOutput(this, 'SchedulerRoleArn', { value: this.schedulerRole.roleArn });
    new CfnOutput(this, 'ReminderTargetArn', { value: this.reminderFn.functionArn, description: 'METIS_REMINDER_TARGET_ARN' });
    new CfnOutput(this, 'DailyPlanTargetArn', { value: this.dailyPlanFn.functionArn, description: 'METIS_DAILY_PLAN_TARGET_ARN' });
  }

  /** Scheduler-side health, by group; used by the observability stack. */
  schedulerMetric(metricName: string): cw.Metric {
    return new cw.Metric({
      namespace: 'AWS/Scheduler',
      metricName,
      dimensionsMap: { ScheduleGroup: this.schedulerGroupName },
      statistic: 'Sum',
      period: Duration.minutes(5),
    });
  }
}
