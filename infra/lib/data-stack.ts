import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { CfnResource } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { MetisConfig, isProd, resourceName } from './config';

export interface DataStackProps extends StackProps {
  readonly config: MetisConfig;
}

/**
 * Authoritative state, private source storage and the vector index.
 *
 * The table layout and access patterns are documented in
 * `docs/architecture/0002-single-table-dynamodb.md`; the indexes here exist to
 * accelerate those exact queries and nothing else.
 */
export class DataStack extends Stack {
  public readonly table: dynamodb.Table;
  public readonly sourcesBucket: s3.Bucket;
  public readonly vectorBucketName: string;
  public readonly vectorIndexName: string;
  public readonly key: kms.Key;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const { config } = props;

    this.key = new kms.Key(this, 'Key', {
      alias: resourceName(this, config, 'cmk'),
      description: 'Metis data at rest: table, sources, logs',
      enableKeyRotation: true,
      removalPolicy: config.envName === 'dev' ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
    });

    this.table = new dynamodb.Table(this, 'Table', {
      tableName: resourceName(this, config, 'state'),
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: this.key,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      // TTL is cleanup for idempotency and claim rows only. It is never the
      // timer for a reminder or a budget release: deletion is eventual.
      timeToLiveAttribute: 'ttl',
      removalPolicy: config.envName === 'dev' ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
      deletionProtection: isProd(config),
    });

    // Deadline / local-date accelerator. Eventually consistent, so it never
    // defines the planning obligation set.
    this.table.addGlobalSecondaryIndex({
      indexName: 'gsi_due',
      partitionKey: { name: 'gsi1pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'gsi1sk', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.INCLUDE,
      nonKeyAttributes: ['kind', 'entity_id', 'revision', 'updated_at'],
    });

    // The outbox dispatcher's ready queue, partitioned by state.
    this.table.addGlobalSecondaryIndex({
      indexName: 'gsi_outbox',
      partitionKey: { name: 'obx_state', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'available_at', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.sourcesBucket = new s3.Bucket(this, 'Sources', {
      bucketName: `${resourceName(this, config, 'sources')}-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: this.key,
      enforceSSL: true,
      versioned: isProd(config),
      removalPolicy: config.envName === 'dev' ? RemovalPolicy.DESTROY : RemovalPolicy.RETAIN,
      autoDeleteObjects: config.envName === 'dev',
      lifecycleRules: [
        {
          // Raw recordings expire; the TRANSCRIPT is a separate prefix and is
          // not deleted with the audio, so losing the audio never silently
          // removes the text.
          id: 'expire-raw-recordings',
          prefix: 'users/',
          tagFilters: { metis_kind: 'recording' },
          expiration: Duration.days(30),
        },
        { id: 'abort-incomplete-uploads', abortIncompleteMultipartUploadAfter: Duration.days(1) },
      ],
      cors: [
        {
          // Direct browser upload to the presigned POST target.
          allowedMethods: [s3.HttpMethods.POST, s3.HttpMethods.PUT],
          allowedOrigins: config.webOrigins,
          allowedHeaders: ['Content-Type'],
          exposedHeaders: ['ETag'],
          maxAge: 300,
        },
      ],
    });

    /**
     * S3 Vectors. Exposed through CfnResource because the L2 construct is not
     * available in every CDK version — this keeps the deployment reproducible
     * without pretending a higher-level construct exists. Verify the current
     * API, limits and metadata-filter support before relying on it.
     */
    const vectorBucketName = `${resourceName(this, config, 'vectors')}-${this.account}`;
    const vectorBucket = new CfnResource(this, 'VectorBucket', {
      type: 'AWS::S3Vectors::VectorBucket',
      properties: {
        VectorBucketName: vectorBucketName,
        EncryptionConfiguration: { SseType: 'aws:kms', KmsKeyArn: this.key.keyArn },
      },
    });
    const vectorIndex = new CfnResource(this, 'VectorIndex', {
      type: 'AWS::S3Vectors::Index',
      properties: {
        VectorBucketName: vectorBucketName,
        IndexName: 'memories',
        DataType: 'float32',
        Dimension: config.embeddingDimensions,
        DistanceMetric: 'cosine',
        // Filterable metadata: ownership first. 'content' is explicitly
        // non-filterable so prose does not inflate index metadata.
        MetadataConfiguration: { NonFilterableMetadataKeys: ['content'] },
      },
    });
    vectorIndex.addResourceDependency(vectorBucket);
    this.vectorBucketName = vectorBucketName;
    this.vectorIndexName = 'memories';

    new CfnOutput(this, 'TableName', { value: this.table.tableName });
    new CfnOutput(this, 'SourcesBucketName', { value: this.sourcesBucket.bucketName });
    new CfnOutput(this, 'VectorBucketName', { value: vectorBucketName });
    new CfnOutput(this, 'EmbeddingDimensions', {
      value: String(config.embeddingDimensions),
      description: 'Stored with every vector; an index must not be queried with another model',
    });
  }
}
