#!/usr/bin/env python

import argparse
from sys import exit
from time import sleep

import boto3

DEFAULT_STACK_NAME = "ec2-gaming-sunshine"


def get_stack_outputs(stack_name: str) -> dict:
    cf = boto3.client("cloudformation")
    response = cf.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def attach_game_data_volume(instance_id: str, volume_id: str, ec2):
    print(f"Waiting for instance {instance_id} to be running...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    vol = ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]
    if vol["State"] == "in-use":
        current = vol["Attachments"][0]["InstanceId"]
        if current == instance_id:
            print(f"Volume {volume_id} already attached")
            return
        print(f"Detaching {volume_id} from previous instance {current}...")
        ec2.detach_volume(VolumeId=volume_id, Force=True)

    print(f"Waiting for volume {volume_id} to be available...")
    ec2.get_waiter("volume_available").wait(VolumeIds=[volume_id])

    ec2.attach_volume(VolumeId=volume_id, InstanceId=instance_id, Device="/dev/xvdf")
    print(f"Attached {volume_id} to {instance_id}")


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
        "--gpu",
        choices=["nvidia", "amd"],
        default="nvidia",
        help="GPU type: nvidia (g4dn, default) or amd (g4ad)",
    )
    parser.add_argument(
        "--instance-type",
        help="Override instance type (e.g. g4dn.2xlarge)",
    )
    args = parser.parse_args()

    try:
        outputs = get_stack_outputs(args.stack_name)

        if args.gpu == "amd":
            template_key = "OnDemandLaunchTemplateNobleAmd" if args.on_demand else "SpotLaunchTemplateNobleAmd"
        else:
            template_key = "OnDemandLaunchTemplateNoble" if args.on_demand else "SpotLaunchTemplateNoble"

        template_name = outputs[template_key]
        subnet_id = outputs["SubnetId"]
        security_group_ids = outputs["SecurityGroupIds"].split(",")
        instance_profile_name = outputs["InstanceProfileName"]
        volume_id = outputs["GameDataVolumeId"]

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

        attach_game_data_volume(instance_id, volume_id, ec2)
    except Exception as e:
        print(e)
        exit(1)


if __name__ == "__main__":
    main()
