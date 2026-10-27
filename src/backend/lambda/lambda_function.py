import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Table name comes from the TABLE_NAME environment variable (set to "todos").
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

# Global secondary index: partition key userId, sort key createdAt.
INDEX_NAME = "userId-createdAt-index"

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
}


def response(status, body):
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps(body)}


def lambda_handler(event, context):
    # The Cognito authorizer puts the verified token claims here.
    # "sub" is the user's unique id, so each user only sees their own to-dos.
    try:
        user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
    except (KeyError, TypeError):
        return response(401, {"message": "Unauthorized"})

    method = event.get("httpMethod")
    todo_id = (event.get("pathParameters") or {}).get("todoId")

    try:
        if method == "OPTIONS":
            return response(200, {})

        if method == "GET":
            # Query the GSI so the list comes back newest first.
            result = table.query(
                IndexName=INDEX_NAME,
                KeyConditionExpression=Key("userId").eq(user_id),
                ScanIndexForward=False,
            )
            return response(200, result["Items"])

        if method == "POST":
            body = json.loads(event.get("body") or "{}")
            title = (body.get("title") or "").strip()
            if not title:
                return response(400, {"message": "title is required"})
            item = {
                "userId": user_id,
                "todoId": str(uuid.uuid4()),
                "title": title,
                "done": False,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
            table.put_item(Item=item)
            return response(201, item)

        if method == "PUT" and todo_id:
            body = json.loads(event.get("body") or "{}")
            result = table.update_item(
                Key={"userId": user_id, "todoId": todo_id},
                UpdateExpression="SET #t = :t, #d = :d",
                ExpressionAttributeNames={"#t": "title", "#d": "done"},
                ExpressionAttributeValues={
                    ":t": body.get("title", ""),
                    ":d": bool(body.get("done", False)),
                },
                ConditionExpression="attribute_exists(todoId)",
                ReturnValues="ALL_NEW",
            )
            return response(200, result["Attributes"])

        if method == "DELETE" and todo_id:
            table.delete_item(
                Key={"userId": user_id, "todoId": todo_id},
                ConditionExpression="attribute_exists(todoId)",
            )
            return response(200, {"deleted": todo_id})

        return response(400, {"message": "Unsupported request"})

    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            return response(404, {"message": "To-do not found"})
        print("DynamoDB error:", err)  # goes to CloudWatch Logs
        return response(500, {"message": "Internal error"})
    except json.JSONDecodeError:
        return response(400, {"message": "Invalid JSON body"})