import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import * as authorizers from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import * as integrations from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';
import { MetisConfig, resourceName } from './config';
import { pythonFunction } from './python-function';

export interface ApiStackProps extends StackProps {
  readonly config: MetisConfig;
  readonly table: dynamodb.Table;
  readonly sourcesBucket: s3.Bucket;
  readonly key: kms.Key;
  readonly vectorBucketName: string;
  readonly vectorIndexName: string;
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;
  readonly ingestQueue: sqs.Queue;
  readonly schedulerGroupName: string;
  readonly schedulerRoleArn: string;
  readonly reminderTargetArn: string;
  readonly dailyPlanTargetArn: string;
  readonly allowedOrigins: string[];
}

/**
 * The HTTP API: one Lambda running the same FastAPI app that uvicorn serves
 * locally, behind an HTTP API with a JWT authorizer.
 *
 * The authorizer validates the token at the edge, and the application
 * validates it again and derives `user_id` from the verified subject. Nothing
 * downstream trusts a request body for ownership.
 */
export class ApiStack extends Stack {
  public readonly httpApi: apigw.HttpApi;
  public readonly apiFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const { config } = props;

    this.apiFunction = pythonFunction(this, 'ApiFunction', {
      config,
      handler: 'metis_api.lambda_entry.http',
      description: 'Metis HTTP API (FastAPI via a pinned ASGI adapter)',
      timeout: Duration.seconds(29), // under the API Gateway integration limit
      memorySize: 1024,
      environment: {
        METIS_MODE: 'cloud',
        METIS_ENV: config.envName,
        METIS_ALLOW_FIXTURE_IDENTITY: 'false',
        METIS_TABLE_NAME: props.table.tableName,
        METIS_SOURCES_BUCKET: props.sourcesBucket.bucketName,
        METIS_VECTOR_BUCKET: props.vectorBucketName,
        METIS_VECTOR_INDEX: props.vectorIndexName,
        METIS_INGEST_QUEUE_URL: props.ingestQueue.queueUrl,
        METIS_COGNITO_USER_POOL_ID: props.userPool.userPoolId,
        METIS_COGNITO_CLIENT_ID: props.userPoolClient.userPoolClientId,
        METIS_SCHEDULER_GROUP: props.schedulerGroupName,
        METIS_SCHEDULER_ROLE_ARN: props.schedulerRoleArn,
        METIS_REMINDER_TARGET_ARN: props.reminderTargetArn,
        METIS_DAILY_PLAN_TARGET_ARN: props.dailyPlanTargetArn,
        METIS_EXTRACTION_MODEL_ID: config.extractionModelId,
        METIS_PLANNING_MODEL_ID: config.planningModelId,
        METIS_EMBEDDING_MODEL_ID: config.embeddingModelId,
        METIS_EMBEDDING_DIMENSIONS: String(config.embeddingDimensions),
        METIS_CORS_ORIGINS: props.allowedOrigins.join(','),
      },
    });

    props.table.grantReadWriteData(this.apiFunction);
    props.sourcesBucket.grantReadWrite(this.apiFunction);
    props.key.grantEncryptDecrypt(this.apiFunction);
    props.ingestQueue.grantSendMessages(this.apiFunction);

    // Narrow model permissions: only the three configured model ids.
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [config.extractionModelId, config.planningModelId, config.embeddingModelId].map(
          (modelId) => `arn:${this.partition}:bedrock:${this.region}::foundation-model/${modelId}`,
        ),
      }),
    );
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3vectors:PutVectors', 's3vectors:QueryVectors', 's3vectors:GetVectors', 's3vectors:DeleteVectors', 's3vectors:GetIndex'],
        resources: [
          `arn:${this.partition}:s3vectors:${this.region}:${this.account}:bucket/${props.vectorBucketName}`,
          `arn:${this.partition}:s3vectors:${this.region}:${this.account}:bucket/${props.vectorBucketName}/index/${props.vectorIndexName}`,
        ],
      }),
    );
    // Schedule management for reminders, scoped to this environment's group.
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['scheduler:CreateSchedule', 'scheduler:UpdateSchedule', 'scheduler:DeleteSchedule', 'scheduler:GetSchedule'],
        resources: [
          `arn:${this.partition}:scheduler:${this.region}:${this.account}:schedule/${props.schedulerGroupName}/*`,
        ],
      }),
    );
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['iam:PassRole'],
        resources: [props.schedulerRoleArn],
        conditions: { StringEquals: { 'iam:PassedToService': 'scheduler.amazonaws.com' } },
      }),
    );

    const authorizer = new authorizers.HttpJwtAuthorizer(
      'CognitoAuthorizer',
      `https://cognito-idp.${this.region}.amazonaws.com/${props.userPool.userPoolId}`,
      { jwtAudience: [props.userPoolClient.userPoolClientId], identitySource: ['$request.header.Authorization'] },
    );

    this.httpApi = new apigw.HttpApi(this, 'HttpApi', {
      apiName: resourceName(this, config, 'api'),
      description: 'Metis API',
      corsPreflight: {
        allowOrigins: props.allowedOrigins,
        allowMethods: [
          apigw.CorsHttpMethod.GET,
          apigw.CorsHttpMethod.POST,
          apigw.CorsHttpMethod.PATCH,
          apigw.CorsHttpMethod.PUT,
          apigw.CorsHttpMethod.DELETE,
          apigw.CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ['Authorization', 'Content-Type', 'Idempotency-Key', 'X-Correlation-Id'],
        allowCredentials: true,
        maxAge: Duration.minutes(10),
      },
      defaultAuthorizer: authorizer,
    });

    const integration = new integrations.HttpLambdaIntegration('ApiIntegration', this.apiFunction);
    this.httpApi.addRoutes({ path: '/{proxy+}', methods: [apigw.HttpMethod.ANY], integration });
    // Health and limits are unauthenticated so a client can detect the mode
    // and the byte limits before signing in.
    this.httpApi.addRoutes({
      path: '/v1/health',
      methods: [apigw.HttpMethod.GET],
      integration,
      authorizer: new apigw.HttpNoneAuthorizer(),
    });
    this.httpApi.addRoutes({
      path: '/v1/limits',
      methods: [apigw.HttpMethod.GET],
      integration,
      authorizer: new apigw.HttpNoneAuthorizer(),
    });

    new logs.LogGroup(this, 'AccessLogs', {
      logGroupName: `/aws/apigateway/${resourceName(this, config, 'api')}`,
      retention: logs.RetentionDays.ONE_MONTH,
    });

    new CfnOutput(this, 'ApiUrl', { value: this.httpApi.apiEndpoint });
  }
}
