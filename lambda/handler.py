"""CloudFormation custom resource that publishes the configured demo website."""
import json
import logging
import os
import urllib.request
import zipfile
from io import BytesIO

import boto3

LOG = logging.getLogger()
LOG.setLevel(logging.INFO)
s3 = boto3.client("s3")
ssm = boto3.client("ssm")
cloudfront = boto3.client("cloudfront")


def respond(event, context, status, data=None, reason=None, physical_id=None):
    body = {
        "Status": status,
        "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": physical_id or event.get("PhysicalResourceId") or context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "NoEcho": False,
        "Data": data or {},
    }
    request = urllib.request.Request(
        event["ResponseURL"], data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "", "content-length": str(len(json.dumps(body).encode("utf-8")))},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        LOG.info("CloudFormation response status=%s", response.status)


def object_content_type(key):
    return {
        ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json",
        ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    }.get(os.path.splitext(key)[1].lower(), "application/octet-stream")


def publish(event):
    props = event["ResourceProperties"]
    bucket = props["WebsiteBucket"]
    if event["RequestType"] == "Delete":
        token = None
        while True:
            args = {"Bucket": bucket}
            if token:
                args["ContinuationToken"] = token
            page = s3.list_objects_v2(**args)
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                for start in range(0, len(keys), 1000):
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": keys[start:start + 1000]})
            if not page.get("IsTruncated"):
                break
            token = page["NextContinuationToken"]
        return

    monitor_id = ssm.get_parameter(Name=props["MonitorIdParameter"])["Parameter"]["Value"]
    identity_pool_id = props["IdentityPoolId"]
    region = props["Region"]
    config = {
        "sessionSampleRate": 1,
        "guestRoleArn": props["GuestRoleArn"],
        "identityPoolId": identity_pool_id,
        "endpoint": f"https://dataplane.rum.{region}.amazonaws.com",
        "telemetries": ["errors", "performance", "http"],
        "allowCookies": True,
        "enableXRay": False,
    }
    rum_snippet = """(function(n,i,v,r,s,c,u,x,z){
  x=window.AwsRumClient={q:[],n:n,i:i,v:v,r:r,c:c};
  window[n]=function(c,p){x.q.push({c:c,p:p});};
  z=document.createElement('script');z.async=true;z.src=s;
  document.head.insertBefore(z,document.head.getElementsByTagName('script')[0]);
})('cwr',%s,'1.0.0',%s,'https://client.rum.us-east-1.amazonaws.com/1.0.2/cwr.js',%s);
""" % (json.dumps(monitor_id), json.dumps(region), json.dumps(config))
    response = s3.get_object(Bucket=props["ArtifactBucket"], Key=props["SiteArtifactKey"])
    with zipfile.ZipFile(BytesIO(response["Body"].read())) as archive:
        expected = set()
        for member in archive.infolist():
            if member.is_dir():
                continue
            key = member.filename.replace("\\", "/").lstrip("/")
            if key.startswith("site/"):
                key = key[5:]
            if not key or ".." in key.split("/"):
                raise ValueError(f"Invalid website artifact path: {member.filename}")
            content = archive.read(member)
            if key == "rum-config.js":
                content = rum_snippet.encode("utf-8")
            elif key.endswith(".html"):
                content = content.replace(b'<script src="https://client.rum.us-east-1.amazonaws.com/1.x/cwr.js"></script>', b"")
            expected.add(key)
            s3.put_object(Bucket=bucket, Key=key, Body=content, ContentType=object_content_type(key), CacheControl="no-cache" if key.endswith((".html", ".js")) else "public, max-age=300")

    token = None
    while True:
        args = {"Bucket": bucket}
        if token:
            args["ContinuationToken"] = token
        page = s3.list_objects_v2(**args)
        stale = [{"Key": item["Key"]} for item in page.get("Contents", []) if item["Key"] not in expected]
        if stale:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": stale})
        if not page.get("IsTruncated"):
            break
        token = page["NextContinuationToken"]
    distribution_id = props.get("DistributionId")
    if distribution_id:
        cloudfront.create_invalidation(DistributionId=distribution_id, InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": event["RequestId"]
        })


def handler(event, context):
    physical_id = event.get("PhysicalResourceId") or f"rum-site-publisher-{event['StackId'].rsplit('/', 1)[-1]}"
    try:
        if event["RequestType"] != "Delete":
            publish(event)
        else:
            publish(event)
        respond(event, context, "SUCCESS", {"Message": "Website published"}, physical_id=physical_id)
    except Exception as exc:
        LOG.exception("Website publishing failed")
        try:
            respond(event, context, "FAILED", reason=str(exc), physical_id=physical_id)
        except Exception:
            LOG.exception("Could not send failure response to CloudFormation")
        raise
