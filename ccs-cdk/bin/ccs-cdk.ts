#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CcsStack } from '../lib/ccs-stack';

const app = new cdk.App();

new CcsStack(app, 'CcsStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
  },
});

app.synth();