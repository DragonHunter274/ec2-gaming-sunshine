#!/usr/bin/env python

import argparse
from sys import exit

import boto3

DEFAULT_STACK_NAME = "ec2-gaming-sunshine"
SHARED_ROOT_TAG = "ec2-gaming-sunshine:shared-root"


def get_stack_outputs(stack_name: str) -> dict:
    cf = boto3.client("cloudformation")
    response = cf.describe_stacks(StackName=stack_name)
    outputs = response["Stacks"][0].get("Outputs", [])
    return {o["OutputKey"]: o["OutputValue"] for o in outputs}


def find_shared_root_volume(stack_name: str, ec2) -> dict | None:
    response = ec2.describe_volumes(
        Filters=[{"Name": f"tag:{SHARED_ROOT_TAG}", "Values": [stack_name]}]
    )
    vols = response["Volumes"]
    return vols[0] if vols else None


def get_root_volume_id(instance_id: str, ec2) -> str:
    instance = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    for bdm in instance["BlockDeviceMappings"]:
        if bdm["DeviceName"] in ["/dev/sda1", "/dev/xvda"]:
            return bdm["Ebs"]["VolumeId"]
    raise RuntimeError(f"Cannot find root volume for {instance_id}")


def tag_as_shared_root(volume_id: str, stack_name: str, ec2):
    ec2.create_tags(
        Resources=[volume_id],
        Tags=[{"Key": SHARED_ROOT_TAG, "Value": stack_name}],
    )
    print(f"Tagged {volume_id} as shared root for stack '{stack_name}'")


def swap_root_volume(instance_id: str, shared_volume: dict, stack_name: str, ec2):
    shared_volume_id = shared_volume["VolumeId"]
    print(f"Stopping instance {instance_id} to swap root volume...")
    ec2.stop_instances(InstanceIds=[instance_id])
    ec2.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])
    print(f"Instance stopped")

    fresh_root_id = get_root_volume_id(instance_id, ec2)
    print(f"Detaching fresh root volume {fresh_root_id}...")
    ec2.detach_volume(VolumeId=fresh_root_id, InstanceId=instance_id, Force=True)
    ec2.get_waiter("volume_available").wait(VolumeIds=[fresh_root_id])
    print(f"Deleting fresh blank root volume {fresh_root_id}...")
    ec2.delete_volume(VolumeId=fresh_root_id)

    if shared_volume["State"] == "in-use":
        current = shared_volume["Attachments"][0]["InstanceId"]
        print(f"Detaching shared root {shared_volume_id} from previous instance {current}...")
        ec2.detach_volume(VolumeId=shared_volume_id, Force=True)

    print(f"Waiting for shared root {shared_volume_id} to be available...")
    ec2.get_waiter("volume_available").wait(VolumeIds=[shared_volume_id])

    ec2.attach_volume(VolumeId=shared_volume_id, InstanceId=instance_id, Device="/dev/sda1")
    print(f"Attached shared root {shared_volume_id} to {instance_id}")

    print(f"Starting instance {instance_id}...")
    ec2.start_instances(InstanceIds=[instance_id])
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    print(f"Instance running")


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

        shared_volume = find_shared_root_volume(args.stack_name, ec2)

        response = ec2.run_instances(**run_kwargs)
        instance_id = response["Instances"][0]["InstanceId"]
        print(f"Launched instance: {instance_id}")

        if shared_volume is None:
            print("First launch: waiting for instance to be running...")
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
            root_volume_id = get_root_volume_id(instance_id, ec2)
            tag_as_shared_root(root_volume_id, args.stack_name, ec2)
        else:
            print(f"Found shared root volume: {shared_volume['VolumeId']}")
            print("Waiting for instance to be running before swap...")
            ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
            swap_root_volume(instance_id, shared_volume, args.stack_name, ec2)

    except Exception as e:
        print(e)
        exit(1)


if __name__ == "__main__":
    main()
