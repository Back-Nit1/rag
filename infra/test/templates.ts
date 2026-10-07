// Focused synth assertions. Run: pnpm --filter @metis/infra test. No AWS calls.
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as assert from 'node:assert/strict';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { readConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { JobsStack } from '../lib/jobs-stack';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { SpeechStack } from '../lib/speech-stack';

const app = new App({ context: { 'metis:bundling': 'stub', 'metis:alertEmail': 'ops@example.invalid' } });
const config = readConfig(app);
const common = { config };
const network = new NetworkStack(app, 'n', common);
const data = new DataStack(app, 'd', common);
const auth = new AuthStack(app, 'a', { ...common, callbackUrls: config.webOrigins });
const speech = new SpeechStack(app, 's', { ...common, vpc: network.vpc, taskSecurityGroup: network.taskSecurityGroup, table: data.table, sourcesBucket: data.sourcesBucket, key: data.key });
const jobs = new JobsStack(app, 'j', { ...common, table: data.table, sourcesBucket: data.sourcesBucket, key: data.key, vectorBucketName: data.vectorBucketName, vectorIndexName: data.vectorIndexName, speechMachine: speech.machine });
const api = new ApiStack(app, 'api', { ...common, table: data.table, sourcesBucket: data.sourcesBucket, key: data.key, vectorBucketName: data.vectorBucketName, vectorIndexName: data.vectorIndexName, userPool: auth.userPool, userPoolClient: auth.client, ingestQueue: jobs.ingestQueue, schedulerGroupName: jobs.schedulerGroupName, schedulerRoleArn: jobs.schedulerRole.roleArn, reminderTargetArn: jobs.reminderFn.functionArn, dailyPlanTargetArn: jobs.dailyPlanFn.functionArn, allowedOrigins: config.webOrigins });
const obs = new ObservabilityStack(app, 'o', { ...common, jobs, speech });

const j = Template.fromStack(jobs);
j.resourceCountIs('AWS::Lambda::Function', 3);
j.hasResourceProperties('AWS::Lambda::Function', { Handler: 'metis_api.lambda_entry.outbox', ReservedConcurrentExecutions: 2 });
j.hasResourceProperties('AWS::Lambda::Function', { Handler: 'metis_api.lambda_entry.reminder_due', ReservedConcurrentExecutions: 3 });
j.hasResourceProperties('AWS::Lambda::Function', { Handler: 'metis_api.lambda_entry.daily_planning', ReservedConcurrentExecutions: 1 });
j.hasResourceProperties('AWS::SQS::Queue', { RedrivePolicy: Match.objectLike({ maxReceiveCount: 5 }) });
j.resourceCountIs('AWS::Scheduler::ScheduleGroup', 1);
j.resourceCountIs('AWS::Events::Rule', 1);

// Only the env names the backend reads are wired; the reminder and daily targets are separate functions.
const outboxEnv = JSON.stringify(j.findResources('AWS::Lambda::Function'));
for (const k of ['METIS_REMINDER_TARGET_ARN', 'METIS_DAILY_PLAN_TARGET_ARN', 'METIS_SPEECH_STATE_MACHINE_ARN', 'METIS_SCHEDULER_ROLE_ARN']) assert.ok(outboxEnv.includes(k), `${k} missing`);
Template.fromStack(api).hasResourceProperties('AWS::Lambda::Function', {
  Environment: { Variables: Match.objectLike({ METIS_DAILY_PLAN_TARGET_ARN: Match.anyValue(), METIS_REMINDER_TARGET_ARN: Match.anyValue() }) },
});

// Reminder and daily-plan functions never get model access.
for (const [, r] of Object.entries(j.findResources('AWS::IAM::Policy'))) {
  const s = JSON.stringify(r);
  if (s.includes('bedrock:InvokeModel')) assert.ok(s.includes('Outbox'), 'only the outbox role may invoke Bedrock');
}
// No IAM statement outside the allow-list uses Resource "*".
const star: string[] = [];
for (const t of [j, Template.fromStack(speech), Template.fromStack(api), Template.fromStack(obs)]) {
  for (const [id, r] of Object.entries(t.findResources('AWS::IAM::Policy'))) {
    for (const st of [(r as any).Properties.PolicyDocument.Statement].flat()) {
      const res = [st.Resource].flat();
      if (res.includes('*')) star.push(`${id}: ${JSON.stringify(st.Action)}`);
    }
  }
}
// Known CDK-generated: SFN log delivery (AWS requires *), ecs:RunTask/Describe*/Stop*/events rules for the sync integration, X-Ray, ECR auth token.
for (const s of star) assert.match(s, /xray|ecr:GetAuthorizationToken|ecs:|events:|states:|logs:CreateLogDelivery/i, `unexpected wildcard resource: ${s}`);

const sp = Template.fromStack(speech);
sp.resourceCountIs('AWS::StepFunctions::StateMachine', 1);
sp.hasResourceProperties('AWS::ECS::TaskDefinition', { RequiresCompatibilities: ['FARGATE'], NetworkMode: 'awsvpc' });
const def = JSON.stringify(sp.findResources('AWS::StepFunctions::StateMachine'));
for (const k of ['ecs:runTask.sync', 'States.Timeout', 'timed_out', 'FinalizeSucceeded']) assert.ok(def.includes(k), `state machine lacks ${k}`);

const o = Template.fromStack(obs);
o.resourceCountIs('AWS::Budgets::Budget', 1);
assert.ok(o.findResources('AWS::CloudWatch::Alarm') && Object.keys(o.findResources('AWS::CloudWatch::Alarm')).length >= 14);
assert.ok(!JSON.stringify(o.toJSON()).toLowerCase().includes('hard cap') || /not a (spending )?cap/i.test(JSON.stringify(o.toJSON())));

// Validation rejects unsafe prod config.
assert.throws(() => readConfig(new App({ context: { 'metis:env': 'prod' } })), /prod requires/);
console.log('infra template assertions passed');
