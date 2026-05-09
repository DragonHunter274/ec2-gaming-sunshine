#!/usr/bin/env python

import argparse
from sys import exit

import boto3

DEFAULT_STACK_NAME = "ec2-gaming-sunshine"


def get_stack_outputs(stack_name: str) -> dict:
    cf = boto3.client("cloudformation")
    response = cf.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def main():
    parser = argparse.ArgumentParser(prog="launch", epilog="Launch a new EC2 gaming instance")
    parser.add_argument(
        "--stack-name",
        help=f"Name of CloudFormation stack, defaults to '{DEFAULT_STACK_NAME}'",
        default=DEFAULT_STACK_NAME,
    )
    parser.add_argument(
        "--on-demand",
        action="store_true",
        default=False,
        help="Use on-demand launch template (default is spot)",
    )
    parser.add_argument(
        "--instance-type",
        help="Override instance type (e.g. g5.4xlarge)",
    )
    args = parser.parse_args()

    try:
        outputs = get_stack_outputs(args.stack_name)

        template_name = (
            outputs["OnDemandLaunchTemplateNoble"]
            if args.on_demand
            else outputs["SpotLaunchTemplateNoble"]
        )
        subnet_id = outputs["SubnetId"]
        security_group_ids = outputs["SecurityGroupIds"].split(",")
        instance_profile_name = outputs["InstanceProfileName"]

        run_kwargs = {
            "LaunchTemplate": {"LaunchTemplateId": template_name, "Version": "$Latest"},
            "SubnetId": subnet_id,
            "SecurityGroupIds": security_group_ids,
            "IamInstanceProfile": {"Name": instance_profile_name},
            "MinCount": 1,
            "MaxCount": 1,
        }
        if args.instance_type:
            run_kwargs["InstanceType"] = args.instance_type

        ec2 = boto3.client("ec2")
        response = ec2.run_instances(**run_kwargs)
        instance_id = response["Instances"][0]["InstanceId"]
        print(f"Launched instance: {instance_id}")
    except Exception as e:
        print(e)
        exit(1)


if __name__ == "__main__":
    main()
