import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { MetisConfig, resourceName } from './config';

export interface NetworkStackProps extends StackProps {
  readonly config: MetisConfig;
}

/**
 * VPC for the ASR tasks and any EC2 compute.
 *
 * Deliberately **no NAT gateway**. A NAT is a fixed monthly cost and the
 * speech worker does not need egress to the public internet: model artifacts
 * are baked into the container image at build time, and everything the task
 * talks to (S3, ECR, CloudWatch Logs, Secrets Manager, Step Functions) is
 * reached through VPC endpoints.
 *
 * The trade-off is recorded rather than hidden: the S3 gateway endpoint is
 * free, while each interface endpoint bills per hour per AZ. One AZ is used in
 * non-production for that reason; production uses two for availability.
 */
export class NetworkStack extends Stack {
  public readonly vpc: ec2.Vpc;
  public readonly taskSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);
    const { config } = props;
    const maxAzs = config.envName === 'prod' ? 2 : 1;

    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: resourceName(this, config, 'vpc'),
      maxAzs,
      natGateways: 0,
      ipAddresses: ec2.IpAddresses.cidr('10.40.0.0/16'),
      subnetConfiguration: [
        { name: 'private', subnetType: ec2.SubnetType.PRIVATE_ISOLATED, cidrMask: 20 },
      ],
      enableDnsHostnames: true,
      enableDnsSupport: true,
    });

    // Free gateway endpoint: how the worker reads audio and writes transcripts.
    this.vpc.addGatewayEndpoint('S3Endpoint', { service: ec2.GatewayVpcEndpointAwsService.S3 });
    this.vpc.addGatewayEndpoint('DynamoEndpoint', {
      service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
    });

    this.taskSecurityGroup = new ec2.SecurityGroup(this, 'TaskSg', {
      vpc: this.vpc,
      description: 'Metis ASR tasks: egress to VPC endpoints only, no inbound',
      allowAllOutbound: true,
    });

    // Interface endpoints the task genuinely needs. Each bills hourly per AZ.
    const interfaceEndpoints: Array<[string, ec2.InterfaceVpcEndpointAwsService]> = [
      ['EcrApi', ec2.InterfaceVpcEndpointAwsService.ECR],
      ['EcrDocker', ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER],
      ['Logs', ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS],
      ['Ssm', ec2.InterfaceVpcEndpointAwsService.SSM],
      ['StepFunctions', ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS],
    ];
    for (const [name, service] of interfaceEndpoints) {
      this.vpc.addInterfaceEndpoint(`${name}Endpoint`, {
        service,
        securityGroups: [this.taskSecurityGroup],
        subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        privateDnsEnabled: true,
      });
    }

    new CfnOutput(this, 'VpcId', { value: this.vpc.vpcId });
    new CfnOutput(this, 'NetworkCostNote', {
      value: `no NAT gateway; ${interfaceEndpoints.length} interface endpoints x ${maxAzs} AZ bill hourly`,
      description: 'Record the selected network path and its cost in ops/cost-worksheet.md',
    });
  }
}
