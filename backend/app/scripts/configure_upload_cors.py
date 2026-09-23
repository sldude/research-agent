"""Preview or apply browser-upload CORS on an existing bucket, preserving other rules."""

import argparse
import json

import boto3
from botocore.exceptions import ClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--origin", action="append", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    s3 = boto3.Session(profile_name=args.profile, region_name=args.region).client("s3")
    try:
        rules = s3.get_bucket_cors(Bucket=args.bucket)["CORSRules"]
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchCORSConfiguration":
            raise
        rules = []
    rule = {
        "ID": "research-agent-browser-upload",
        "AllowedOrigins": list(dict.fromkeys(args.origin)),
        "AllowedMethods": ["POST"],
        "AllowedHeaders": ["content-type"],
        "MaxAgeSeconds": 300,
    }
    config = {"CORSRules": [r for r in rules if r.get("ID") != rule["ID"]] + [rule]}
    print(json.dumps(config, indent=2))
    if args.apply:
        s3.put_bucket_cors(Bucket=args.bucket, CORSConfiguration=config)
        print("Applied upload CORS configuration.")
    else:
        print("Preview only. Add --apply to save this configuration.")


if __name__ == "__main__":
    main()
