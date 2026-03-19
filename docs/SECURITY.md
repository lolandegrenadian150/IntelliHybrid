# 🔐 IntelliHybrid Security Guide

## Overview

IntelliHybrid is designed security-first. This guide covers all security mechanisms and hardening recommendations.

---

## Credential Management

**Rule:** Never put credentials in `config.yaml`. Always use environment variables.

```yaml
# ✅ Correct — uses env var reference
aws:
  access_key_id: "${AWS_ACCESS_KEY_ID}"

# ❌ Wrong — hardcoded credential
aws:
  access_key_id: "AKIAIOSFODNN7EXAMPLE"
```

For production, use **AWS Secrets Manager** instead of environment variables:

```bash
# Store your DB password in Secrets Manager
aws secretsmanager create-secret \
  --name intellihybrid/db-password \
  --secret-string '{"password":"your_db_password"}'
```

Then retrieve it at runtime rather than storing in env vars.

---

## IAM Least Privilege

The IAM policy in `docs/HOW_TO_USE.md` grants only the specific actions IntelliHybrid needs. Never use `"Action": "*"` or `"Resource": "*"` in production — scope to your specific table ARNs:

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:PutItem", "dynamodb:GetItem"],
  "Resource": "arn:aws:dynamodb:us-east-1:123456789012:table/my-table"
}
```

---

## Encryption

### At Rest
All DynamoDB tables created by IntelliHybrid use AWS KMS encryption (`SSEType: KMS`) by default. Set `encryption: true` in config (this is the default).

### In Transit
- All AWS API calls use HTTPS (TLS 1.3)
- All database connections use SSL (`ssl: true` in config)
- VPN tunnel uses IPSec (AES-256-GCM)

---

## Network Security

### VPN
The Site-to-Site VPN creates two redundant IPSec tunnels. Only open the following ports on your on-prem firewall:
- UDP 500 (IKE key exchange)
- UDP 4500 (NAT traversal)
- IP Protocol 50 (ESP — encrypted payload)

### DynamoDB VPC Endpoint (Recommended)
Instead of DynamoDB traffic going over the public internet, use a VPC Endpoint:

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxxxxxxxx \
  --service-name com.amazonaws.us-east-1.dynamodb \
  --route-table-ids rtb-xxxxxxxxx
```

This keeps all DynamoDB traffic within AWS's private network.

---

## Key Rotation

Rotate AWS access keys every 90 days:
```bash
# Create new key
aws iam create-access-key --user-name intellihybrid-service

# Update env vars with new key, then delete old key
aws iam delete-access-key --user-name intellihybrid-service --access-key-id OLD_KEY_ID
```

---

## .gitignore Verification

Always verify `config/config.yaml` is ignored before pushing:
```bash
git check-ignore -v config/config.yaml
# Should output: .gitignore:3:config/config.yaml   config/config.yaml
```

If it's not ignored, add it:
```bash
echo "config/config.yaml" >> .gitignore
```

---

## Security Scanning

Run Bandit (Python security linter) before each release:
```bash
pip install bandit
bandit -r src/ -ll
```

Scan for hardcoded secrets with Gitleaks:
```bash
brew install gitleaks   # or apt install gitleaks
gitleaks detect --source . --verbose
```
