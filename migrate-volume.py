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


def main():
    parser = argparse.ArgumentParser(
        prog="migrate-volume",
        epilog="Move the shared root EBS volume to a different Availability Zone",
    )
    parser.add_argument(
        "--stack-name",
        help=f"Name of CloudFormation stack, defaults to '{DEFAULT_STACK_NAME}'",
        default=DEFAULT_STACK_NAME,
    )
    parser.add_argument(
        "--target-az",
        required=True,
        help="Target Availability Zone (e.g. eu-central-1b)",
    )
    args = parser.parse_args()

    ec2 = boto3.client("ec2")

    try:
        volume = find_shared_root_volume(args.stack_name, ec2)
        if volume is None:
            print("No shared root volume found. Run launch.py first.")
            exit(1)

        volume_id = volume["VolumeId"]
        current_az = volume["AvailabilityZone"]

        if current_az == args.target_az:
            print(f"Volume {volume_id} is already in {args.target_az}")
            exit(0)

        if volume["State"] == "in-use":
            print(f"Volume {volume_id} is currently attached — stop the instance first.")
            exit(1)

        print(f"Snapshotting {volume_id} ({current_az})...")
        snapshot = ec2.create_snapshot(
            VolumeId=volume_id,
            Description=f"{args.stack_name} shared root migration to {args.target_az}",
        )
        snapshot_id = snapshot["SnapshotId"]
        print(f"Waiting for snapshot {snapshot_id} to complete (this can take a few minutes)...")
        ec2.get_waiter("snapshot_completed").wait(SnapshotIds=[snapshot_id])
        print(f"Snapshot complete")

        print(f"Creating new volume in {args.target_az}...")
        new_volume = ec2.create_volume(
            SnapshotId=snapshot_id,
            AvailabilityZone=args.target_az,
            VolumeType=volume["VolumeType"],
            Encrypted=volume["Encrypted"],
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": SHARED_ROOT_TAG, "Value": args.stack_name},
                    {"Key": "Name", "Value": f"{args.stack_name}-shared-root"},
                ],
            }],
        )
        new_volume_id = new_volume["VolumeId"]
        print(f"Waiting for new volume {new_volume_id} to be available...")
        ec2.get_waiter("volume_available").wait(VolumeIds=[new_volume_id])
        print(f"New volume ready in {args.target_az}")

        print(f"Removing shared root tag from old volume {volume_id}...")
        ec2.delete_tags(
            Resources=[volume_id],
            Tags=[{"Key": SHARED_ROOT_TAG}],
        )

        print(f"Deleting snapshot {snapshot_id}...")
        ec2.delete_snapshot(SnapshotId=snapshot_id)

        print(f"Deleting old volume {volume_id}...")
        ec2.delete_volume(VolumeId=volume_id)

        print(f"\nDone. Shared root volume migrated: {volume_id} ({current_az}) -> {new_volume_id} ({args.target_az})")

    except Exception as e:
        print(e)
        exit(1)


if __name__ == "__main__":
    main()
