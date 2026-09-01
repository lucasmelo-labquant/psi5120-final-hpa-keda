#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"

echo "EKS clusters:"
aws eks list-clusters --region "${REGION}" --output table
echo "Project EC2 instances:"
aws ec2 describe-instances --region "${REGION}" \
  --filters 'Name=tag:Project,Values=PSI5120-FINAL' \
  'Name=instance-state-name,Values=pending,running,stopping,stopped' \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,InstanceType]' --output table
echo "Available project EBS volumes:"
aws ec2 describe-volumes --region "${REGION}" \
  --filters 'Name=tag:Project,Values=PSI5120-FINAL' 'Name=status,Values=available' \
  --query 'Volumes[].[VolumeId,Size,State]' --output table
echo "NAT gateways:"
aws ec2 describe-nat-gateways --region "${REGION}" \
  --filter 'Name=state,Values=pending,available,deleting' \
  --query 'NatGateways[].[NatGatewayId,State]' --output table
echo "Elastic IPs:"
aws ec2 describe-addresses --region "${REGION}" \
  --query 'Addresses[].[AllocationId,AssociationId,PublicIp]' --output table
echo "Load balancers:"
aws elbv2 describe-load-balancers --region "${REGION}" \
  --query "LoadBalancers[?contains(LoadBalancerName, 'psi5120')].[LoadBalancerName,State.Code]" \
  --output table
echo "Project CloudFormation stacks:"
aws cloudformation describe-stacks --region "${REGION}" \
  --query "Stacks[?contains(StackName, 'psi5120-final')].[StackName,StackStatus]" \
  --output table 2>/dev/null || true
echo "Project SQS queues:"
aws sqs list-queues --region "${REGION}" --queue-name-prefix psi5120-final --output table
echo "Project ECR repositories:"
aws ecr describe-repositories --region "${REGION}" \
  --query "repositories[?starts_with(repositoryName, 'psi5120-final')].[repositoryName,repositoryUri]" \
  --output table
echo "Project IAM policies:"
aws iam list-policies --scope Local \
  --query "Policies[?starts_with(PolicyName, 'PSI5120Final')].[PolicyName,Arn]" \
  --output table
echo "Project log groups:"
aws logs describe-log-groups --region "${REGION}" \
  --log-group-name-prefix /aws/eks/psi5120-final \
  --query 'logGroups[].[logGroupName,storedBytes]' --output table
