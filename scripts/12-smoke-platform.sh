#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

kubectl exec deployment/sqs-worker -n scaling-study -- python -c \
  "import boto3,os; boto3.client('sqs',region_name=os.environ.get('AWS_REGION','us-east-1')).get_queue_attributes(QueueUrl=os.environ['INPUT_QUEUE_URL'],AttributeNames=['QueueArn'])"
kubectl top nodes
kubectl get pods -A -o wide
kubectl get serviceaccount sqs-worker -n scaling-study \
  -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'
printf '\n'
kubectl get serviceaccount keda-operator -n keda \
  -o jsonpath='{.metadata.annotations.eks\.amazonaws\.com/role-arn}'
printf '\n'
