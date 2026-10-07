import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import { Construct } from 'constructs';
import { MetisConfig, isProd, resourceName } from './config';

export interface AuthStackProps extends StackProps {
  readonly config: MetisConfig;
  readonly callbackUrls: string[];
}

/**
 * Cognito user pool with the authorization-code + PKCE flow.
 *
 * The implicit flow is disabled on purpose: a browser app should never receive
 * a token in a URL fragment. No client secret is issued, because a public
 * client cannot keep one.
 */
export class AuthStack extends Stack {
  public readonly userPool: cognito.UserPool;
  public readonly client: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);
    const { config } = props;

    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: resourceName(this, config, 'users'),
      selfSignUpEnabled: false, // a personal deployment invites its own user
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: false } },
      passwordPolicy: {
        minLength: 12,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
        tempPasswordValidity: Duration.days(3),
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      mfa: isProd(config) ? cognito.Mfa.OPTIONAL : cognito.Mfa.OFF,
      mfaSecondFactor: { sms: false, otp: true },
      // Threat protection needs the paid Plus feature plan; left off deliberately (see ops/cost-worksheet.md).
      removalPolicy: isProd(config) ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
    });

    this.client = this.userPool.addClient('WebClient', {
      userPoolClientName: resourceName(this, config, 'web'),
      generateSecret: false,
      authFlows: { userSrp: true, custom: false, userPassword: false },
      oAuth: {
        flows: { authorizationCodeGrant: true, implicitCodeGrant: false },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: props.callbackUrls,
        logoutUrls: props.callbackUrls,
      },
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(30),
      preventUserExistenceErrors: true,
      enableTokenRevocation: true,
    });

    this.userPool.addDomain('Domain', {
      cognitoDomain: { domainPrefix: `metis-${config.envName}-${this.account}`.slice(0, 63) },
    });

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: this.client.userPoolClientId });
  }
}
