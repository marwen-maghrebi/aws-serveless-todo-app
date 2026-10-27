import json


def lambda_handler(event, context):
    # Triggered by the DynamoDB stream on the "todos" table.
    # Each record describes one change: INSERT, MODIFY or REMOVE.
    for record in event["Records"]:
        print(json.dumps({
            "action": record["eventName"],
            "keys": record["dynamodb"]["Keys"],
        }))
    return {"processed": len(event["Records"])}