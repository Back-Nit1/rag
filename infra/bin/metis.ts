#!/usr/bin/env node
import { App, Tags } from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { AuthStack } from '../lib/auth-stack';
import { readConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { JobsStack } from '../lib/jobs-stack';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { SpeechStack } from '../lib/speech-stack';

const app = new App();
const config = readConfig(app);
// Pin account+region only when the CLI supplies an account; otherwise synth
// stays environment-agnostic and makes no AWS lookups.
const account = process.env.CDK_DEFAULT_ACCOUNT;
const env = account ? { account, region: config.region } : undefined;
const name = (n: string) => `metis-${config.envName}-${n}`;
const common = { config, env };

const network = new NetworkStack(app, name('network'), common);
const data = new DataStack(app, name('data'), common);
const auth = new AuthStack(app, name('auth'), { ...common, callbackUrls: config.webOrigins });
const speech = new SpeechStack(app, name('speech'), {
  ...common,
  vpc: network.vpc,
  taskSecurityGroup: network.taskSecurityGroup,
  table: data.table,
  sourcesBucket: data.sourcesBucket,
  key: data.key,
});
const jobs = new JobsStack(app, name('jobs'), {
  ...common,
  table: data.table,
  sourcesBucket: data.sourcesBucket,
  key: data.key,
  vectorBucketName: data.vectorBucketName,
  vectorIndexName: data.vectorIndexName,
  speechMachine: speech.machine,
});
new ApiStack(app, name('api'), {
  ...common,
  table: data.table,
  sourcesBucket: data.sourcesBucket,
  key: data.key,
  vectorBucketName: data.vectorBucketName,
  vectorIndexName: data.vectorIndexName,
  userPool: auth.userPool,
  userPoolClient: auth.client,
  ingestQueue: jobs.ingestQueue,
  schedulerGroupName: jobs.schedulerGroupName,
  schedulerRoleArn: jobs.schedulerRole.roleArn,
  reminderTargetArn: jobs.reminderFn.functionArn,
  dailyPlanTargetArn: jobs.dailyPlanFn.functionArn,
  allowedOrigins: config.webOrigins,
});
new ObservabilityStack(app, name('observability'), { ...common, jobs, speech });

Tags.of(app).add('app', 'metis');
Tags.of(app).add('env', config.envName);
