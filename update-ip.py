#!/usr/bin/env python

import argparse
import http.client
import logging
import socket
import ssl
import sys

import boto3
import botocore

DEFAULT_STACK_NAME = "ec2-gaming-sunshine"


def main():
    parser = argparse.ArgumentParser(prog="update-ip", epilog="Update whitelisted IP")
    parser.add_argument(
        "--stack-name",
        help=f"Name of CloudFormation stack, defaults to '{DEFAULT_STACK_NAME}'",
        default=DEFAULT_STACK_NAME,
    )

    args = parser.parse_args()
    stack_name = args.stack_name

    logging.basicConfig(level=logging.INFO)
    logging.info(f"Updating stack {stack_name}")

    cf = boto3.client("cloudformation")
    ec2 = boto3.client("ec2")

    try:
        ip = get_ip()
        ip_cidr = f"{ip}/32"
        logging.info(f"Updating IP whitelisting to: {ip_cidr}")

        cf.update_stack(
            StackName=stack_name,
            UsePreviousTemplate=True,
            Parameters=[
                {"ParameterKey": "MyIp", "ParameterValue": ip_cidr},
                {"ParameterKey": "KeyPair", "UsePreviousValue": True},
                {"ParameterKey": "Route53HostedZoneName", "UsePreviousValue": True},
                {"ParameterKey": "NotificationEmail", "UsePreviousValue": True},
            ],
            Capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND"],
        )

        update_security_groups(stack_name, ip_cidr, ec2, cf)

    except botocore.exceptions.ClientError as error:
        if error.response["Error"]["Code"] == "ValidationError":
            logging.error(error.response["Error"]["Message"])
            return 1
        else:
            raise error


def update_security_groups(stack_name: str, new_ip_cidr: str, ec2, cf):
    resources = cf.describe_stack_resources(StackName=stack_name)
    sg_ids = [
        r["PhysicalResourceId"]
        for r in resources["StackResources"]
        if r["ResourceType"] == "AWS::EC2::SecurityGroup"
        and r["LogicalResourceId"] in ["AdminAccess", "GamingAccess"]
    ]

    for sg_id in sg_ids:
        sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
        for rule in sg["IpPermissions"]:
            old_ranges = [r for r in rule["IpRanges"] if r["CidrIp"] != "0.0.0.0/0"]
            if not old_ranges:
                continue
            ec2.revoke_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{**rule, "IpRanges": old_ranges}],
            )
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{**rule, "IpRanges": [{"CidrIp": new_ip_cidr}]}],
            )
            logging.info(f"Updated {sg['GroupName']} ({sg_id})")


def get_ip():
    host = "ifconfig.me"
    conn = IPv4HTTPSConnection(host)
    conn.request("GET", "/ip", headers={"Host": host})
    response = conn.getresponse()
    ip = response.read().decode("utf-8")
    conn.close()
    return ip


class IPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        if self._tunnel_host:
            self._tunnel()
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(self.sock, server_hostname=self.host)


if __name__ == "__main__":
    sys.exit(main())
