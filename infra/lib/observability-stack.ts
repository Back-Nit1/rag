import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { MetisConfig, resourceName } from './config';
import { JobsStack } from './jobs-stack';
import { SpeechStack } from './speech-stack';

export interface ObservabilityStackProps extends StackProps {
  readonly config: MetisConfig;
  readonly jobs: JobsStack;
  readonly speech: SpeechStack;
}

/**
 * Alarms for the failure modes the product promises to surface, and a monthly
 * cost budget.
 *
 * The budget is an ALERT, not a cap: AWS Budgets notifies after spend is
 * recorded (with hours of delay) and never stops a resource. Spend limits are
 * enforced by the application's reserve-then-settle model budget and by the
 * concurrency caps on the functions and the ASR task.
 *
 * Gaps that need a backend metric (stuck outbox rows by age, reminders late
 * versus their trigger time) are covered here by proxies and listed in
 * ops/runbook.md rather than faked.
 */
export class ObservabilityStack extends Stack {
  public readonly topic: sns.Topic;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    const { config, jobs, speech } = props;

    this.topic = new sns.Topic(this, 'Alerts', { topicName: resourceName(this, config, 'alerts'), enforceSSL: true });
    if (config.alertEmail) this.topic.addSubscription(new subs.EmailSubscription(config.alertEmail));
    const action = new cwActions.SnsAction(this.topic);

    const alarm = (name: string, description: string, metric: cw.IMetric, opts: Partial<cw.AlarmProps> = {}) => {
      const a = new cw.Alarm(this, name, {
        alarmName: resourceName(this, config, name.toLowerCase()),
        alarmDescription: description,
        metric,
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cw.TreatMissingData.NOT_BREACHING,
        ...opts,
      });
      a.addAlarmAction(action);
      return a;
    };
    const fiveMin = Duration.minutes(5);

    // Dead-lettered outbox wake-ups: work that exhausted its attempts.
    alarm('IngestDlqNotEmpty', 'Messages reached the ingest DLQ; inspect outbox rows with state=dead.',
      jobs.ingestDlq.metricApproximateNumberOfMessagesVisible({ period: fiveMin }));
    // Job age: the oldest queued wake-up has waited too long.
    alarm('IngestQueueAge', 'Oldest ingest message older than 15 minutes: dispatcher is stalled or throttled.',
      jobs.ingestQueue.metricApproximateAgeOfOldestMessage({ period: fiveMin }), { threshold: 900 });
    // Stuck outbox (proxy): the 5-minute timer plus SQS should invoke the dispatcher constantly.
    alarm('OutboxNotRunning', 'Outbox dispatcher had no invocation in 15 minutes: timer or event source is broken.',
      jobs.outboxFn.metricInvocations({ period: Duration.minutes(15) }),
      { threshold: 1, comparisonOperator: cw.ComparisonOperator.LESS_THAN_THRESHOLD, treatMissingData: cw.TreatMissingData.BREACHING });
    alarm('OutboxErrors', 'Outbox dispatcher raised unhandled errors.', jobs.outboxFn.metricErrors({ period: fiveMin }));
    alarm('OutboxThrottled', 'Outbox dispatcher throttled by its reserved concurrency.', jobs.outboxFn.metricThrottles({ period: fiveMin }));

    // Missed reminders: Scheduler could not deliver, or the target failed.
    alarm('ReminderTargetErrors', 'EventBridge Scheduler target errors for reminders/daily plans.', jobs.schedulerMetric('TargetErrorCount'));
    alarm('ReminderDropped', 'EventBridge Scheduler dropped an invocation (a reminder was missed).', jobs.schedulerMetric('InvocationDroppedCount'));
    alarm('ReminderFnErrors', 'Reminder-due Lambda errors.', jobs.reminderFn.metricErrors({ period: fiveMin }));
    alarm('ReminderFnThrottled', 'Reminder-due Lambda throttled: a reminder may fire late.', jobs.reminderFn.metricThrottles({ period: fiveMin }));
    alarm('DailyPlanErrors', 'Daily-planning Lambda errors.', jobs.dailyPlanFn.metricErrors({ period: fiveMin }));

    // ASR failures.
    const m = speech.machine;
    const asr = (n: string, d: string, metric: cw.IMetric) => alarm(n, d, metric);
    asr('AsrExecutionsFailed', 'ASR state machine executions failed.', m.metricFailed({ period: fiveMin }));
    asr('AsrExecutionsTimedOut', 'ASR state machine executions timed out.', m.metricTimedOut({ period: fiveMin }));
    asr('AsrExecutionsAborted', 'ASR state machine executions aborted.', m.metricAborted({ period: fiveMin }));
    asr('AsrFinalizeErrors', 'ASR finalize Lambda errors: a transcript may not have been verified.', speech.finalizeFn.metricErrors({ period: fiveMin }));

    // Budget: alert only. Note the topic policy: Budgets must be allowed to publish.
    this.topic.addToResourcePolicy(
      new iam.PolicyStatement({
        principals: [new iam.ServicePrincipal('budgets.amazonaws.com')],
        actions: ['sns:Publish'],
        resources: [this.topic.topicArn],
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
    );
    const notify = (type: 'ACTUAL' | 'FORECASTED', pct: number): budgets.CfnBudget.NotificationWithSubscribersProperty => ({
      notification: { notificationType: type, comparisonOperator: 'GREATER_THAN', threshold: pct, thresholdType: 'PERCENTAGE' },
      subscribers: [{ subscriptionType: 'SNS', address: this.topic.topicArn }],
    });
    new budgets.CfnBudget(this, 'MonthlyCostAlarm', {
      budget: {
        budgetName: resourceName(this, config, 'monthly-cost-alarm'),
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: config.monthlyBudgetUsd, unit: 'USD' },
      },
      notificationsWithSubscribers: [notify('ACTUAL', 50), notify('ACTUAL', 80), notify('ACTUAL', 100), notify('FORECASTED', 100)],
    });

    new CfnOutput(this, 'AlertsTopicArn', { value: this.topic.topicArn });
    new CfnOutput(this, 'BudgetNote', {
      value: 'Budget is a delayed alert, not a spending cap; nothing is stopped when it fires.',
    });
  }
}
