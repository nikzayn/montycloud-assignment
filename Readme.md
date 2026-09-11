# Image Service

An Instagram-style image upload and storage service, built on API Gateway, Lambda,
S3 and DynamoDB, and runnable entirely on your laptop with LocalStack.

Images are stored in **S3**; their metadata is stored in **DynamoDB**. A single
Lambda function behind an API Gateway `{proxy+}` resource serves every route.

## Design Thoughts
- So as per problem statement, it is mentioned that the stack is fixed which is API Gateway,
Lambda, S3, DynamoDB, etc.
- Also, what I believe is that images should go in S3, since S3 can handle large binary files efficiently,
whereas we could store metadata in DynamoDB, because for flexible schema with faster queries resolution. Also, as per the file size limit, dynamoDB handles item sizes to 400KB, whereas S3 handles files upto 5TB.

 