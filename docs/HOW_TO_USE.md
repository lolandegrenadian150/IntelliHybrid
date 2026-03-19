# 📖 IntelliHybrid — How-To-Use Booklet

**Version:** 1.0  
**Author:** Shailesh Kadam  
**GitHub:** https://github.com/Clever-Boy/IntelliHybrid

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Prerequisites](#2-prerequisites)
3. [Installation](#3-installation)
4. [Step 1 — AWS Account Setup](#4-step-1--aws-account-setup)
5. [Step 2 — Configure Your On-Prem Database](#5-step-2--configure-your-on-prem-database)
6. [Step 3 — Configure VPN](#6-step-3--configure-vpn)
7. [Step 4 — Define Your DynamoDB Tables](#7-step-4--define-your-dynamodb-tables)
8. [Step 5 — Initialize & Run](#8-step-5--initialize--run)
9. [Command Reference](#9-command-reference)
10. [Security Best Practices](#10-security-best-practices)
11. [Troubleshooting](#11-troubleshooting)
12. [FAQs](#12-faqs)

---

## 1. Introduction

**IntelliHybrid** connects your on-premise databases to AWS DynamoDB through a secure, encrypted channel. Once configured, it keeps your on-prem and cloud data in sync automatically — without any code changes.

**Who is this for?**

- 🏢 Enterprise teams running hybrid cloud architectures
- 🧑‍💻 Developers migrating on-prem workloads to AWS
- 🔐 Security-conscious teams who need encrypted, VPN-backed data replication

**What you'll have after following this guide:**

```
✅ Secure VPN tunnel between your data center and AWS
✅ DynamoDB tables auto-created with your custom schema
✅ Continuous bidirectional sync running
✅ All data encrypted at rest (KMS) and in transit (TLS 1.3)
```

---

## 2. Prerequisites

Before you begin, make sure you have:

| Requirement | Details |
|---|---|
| Python | 3.9 or higher |
| AWS Account | With IAM permissions (see Step 1) |
| On-Prem Database | MySQL 5.7+, PostgreSQL 12+, Oracle 19c+, or SQL Server 2019+ |
| Public IP | Your on-prem network must have a static public IP for VPN |
| Network access | UDP 500 and 4500 open on your firewall for VPN |

---

## 3. Installation

### Option A — pip (recommended)

```bash
pip install intellihybrid
```

### Option B — from source

```bash
git clone https://github.com/Clever-Boy/IntelliHybrid.git
cd IntelliHybrid
pip install -e .
```

### Install database drivers (install only what you need)

```bash
# MySQL
pip install PyMySQL

# PostgreSQL
pip install psycopg2-binary

# Oracle
pip install oracledb

# SQL Server
pip install pyodbc
```

Verify the installation:

```bash
intellihybrid --help
```

---

## 4. Step 1 — AWS Account Setup

### 4.1 Create an IAM User for IntelliHybrid

In the AWS Console:

1. Go to **IAM → Users → Create User**
2. Name it `intellihybrid-service`
3. Attach the following policy (save as `intellihybrid-iam-policy.json`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": [
        "dynamodb:CreateTable",
        "dynamodb:DeleteTable",
        "dynamodb:DescribeTable",
        "dynamodb:ListTables",
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:Scan",
        "dynamodb:Query",
        "dynamodb:UpdateTable",
        "dynamodb:UpdateContinuousBackups",
        "dynamodb:UpdateTimeToLive",
        "dynamodb:TagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2VPN",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateCustomerGateway",
        "ec2:DescribeCustomerGateways",
        "ec2:CreateVpnGateway",
        "ec2:DescribeVpnGateways",
        "ec2:AttachVpnGateway",
        "ec2:CreateVpnConnection",
        "ec2:DescribeVpnConnections",
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "KMS",
      "Effect": "Allow",
      "Action": [
        "kms:CreateKey",
        "kms:DescribeKey",
        "kms:GenerateDataKey"
      ],
      "Resource": "*"
    }
  ]
}
```

4. Generate **Access Keys** and save them securely.

### 4.2 Set Environment Variables

**Linux / macOS:**
```bash
export AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
export AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
export AWS_ACCOUNT_ID="123456789012"
export DB_USER="mydbuser"
export DB_PASSWORD="mydbpassword"
```

**Windows (PowerShell):**
```powershell
$env:AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
$env:AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
$env:AWS_ACCOUNT_ID = "123456789012"
$env:DB_USER = "mydbuser"
$env:DB_PASSWORD = "mydbpassword"
```

> ⚠️ **Never put credentials directly in config.yaml.** Always use environment variables.

---

## 5. Step 2 — Configure Your On-Prem Database

### 5.1 Copy the config template

```bash
cp config/config.template.yaml config/config.yaml
```

### 5.2 Fill in the database section

```yaml
onprem:
  database:
    type: mysql        # ← change to your database type
    host: 192.168.1.100
    port: 3306
    name: my_production_db
    username: "${DB_USER}"
    password: "${DB_PASSWORD}"
    ssl: true
```

### 5.3 Database-specific notes

**MySQL:** Make sure the user has SELECT, INSERT, UPDATE privileges:
```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON my_production_db.* TO 'intellihybrid'@'%';
FLUSH PRIVILEGES;
```

**PostgreSQL:** Grant table access:
```sql
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO intellihybrid;
```

**Oracle:** Grant connect and resource:
```sql
GRANT CONNECT, RESOURCE TO intellihybrid;
```

**SQL Server:** Create login and user:
```sql
CREATE LOGIN intellihybrid WITH PASSWORD = '${DB_PASSWORD}';
CREATE USER intellihybrid FOR LOGIN intellihybrid;
ALTER ROLE db_datareader ADD MEMBER intellihybrid;
ALTER ROLE db_datawriter ADD MEMBER intellihybrid;
```

---

## 6. Step 3 — Configure VPN

Choose ONE VPN method depending on your infrastructure:

### Option A — AWS Site-to-Site VPN (most common)

This is the recommended option for permanent hybrid connectivity. IntelliHybrid will automatically create the Customer Gateway and Virtual Private Gateway in AWS.

```yaml
onprem:
  vpn:
    type: site-to-site
    customer_gateway_ip: "203.0.113.10"  # ← your on-prem public IP
    bgp_asn: 65000
```

**Firewall requirements on your on-prem router:**
- Allow UDP 500 (IKE)
- Allow UDP 4500 (NAT-T)
- Allow IP protocol 50 (ESP)

After `intellihybrid init`, you will receive two AWS tunnel endpoint IPs. Configure your on-prem router/firewall to establish the IPSec tunnel to those IPs.

### Option B — OpenVPN

If you already have an OpenVPN setup:

```yaml
onprem:
  vpn:
    type: openvpn
    customer_gateway_ip: "203.0.113.10"
    config_file: "config/my_vpn.ovpn"   # ← path to your .ovpn file
```

Make sure OpenVPN is installed on the machine running IntelliHybrid:
```bash
# Ubuntu/Debian
sudo apt-get install openvpn

# CentOS/RHEL
sudo yum install openvpn
```

### Option C — AWS Direct Connect

If you have a dedicated Direct Connect circuit:

```yaml
onprem:
  vpn:
    type: direct-connect
    customer_gateway_ip: "203.0.113.10"
```

IntelliHybrid will validate your existing Direct Connect connection. Provision it first in the AWS Console.

---

## 7. Step 4 — Define Your DynamoDB Tables

The most powerful part of IntelliHybrid is configuring DynamoDB tables entirely through YAML — no code needed.

### 7.1 Basic table (string PK only)

```yaml
dynamodb:
  tables:
    - name: users-table
      partition_key:
        name: userId      # ← your column that will be the PK
        type: S           # S = String, N = Number, B = Binary
      billing_mode: PAY_PER_REQUEST
      encryption: true
```

### 7.2 Table with PK + SK (composite key)

```yaml
    - name: orders-table
      partition_key:
        name: orderId
        type: S
      sort_key:
        name: createdAt   # ← sort key enables range queries
        type: N           # N = Number (timestamps work well as numbers)
```

### 7.3 Table with Global Secondary Index (GSI)

```yaml
    - name: products-table
      partition_key:
        name: productId
        type: S
      sort_key:
        name: categoryId
        type: S
      gsi:
        - name: category-price-index
          partition_key:
            name: categoryId
            type: S
          sort_key:
            name: price
            type: N
```

### 7.4 Table with TTL (auto-expire items)

```yaml
    - name: sessions-table
      partition_key:
        name: sessionId
        type: S
      ttl_attribute: expiresAt    # ← items expire automatically after this Unix timestamp
```

---

## 8. Step 5 — Initialize & Run

### 8.1 Initialize (first time only)

```bash
intellihybrid init --config config/config.yaml
```

Expected output:
```
2025-01-15 10:00:00 [INFO] ============================================================
2025-01-15 10:00:00 [INFO]   IntelliHybrid — Initialization
2025-01-15 10:00:00 [INFO] ============================================================
2025-01-15 10:00:01 [INFO] [1/3] Establishing VPN tunnel...
2025-01-15 10:00:05 [INFO]       ✅ VPN: established
2025-01-15 10:00:05 [INFO] [2/3] Provisioning DynamoDB tables...
2025-01-15 10:00:08 [INFO]       CREATED    users-table
2025-01-15 10:00:11 [INFO]       CREATED    orders-table
2025-01-15 10:00:11 [INFO] [3/3] Testing on-prem database connectivity...
2025-01-15 10:00:11 [INFO]       ✅ Database: healthy (mysql @ 192.168.1.100)
2025-01-15 10:00:11 [INFO]
2025-01-15 10:00:11 [INFO] ✅ IntelliHybrid initialized successfully!
```

### 8.2 Start syncing

```bash
# Continuous bidirectional sync (recommended for production)
intellihybrid sync --config config/config.yaml --mode bidirectional

# One-time sync and exit
intellihybrid sync --config config/config.yaml --once

# Push only (on-prem → DynamoDB)
intellihybrid sync --mode push

# Pull only (DynamoDB → on-prem)
intellihybrid sync --mode pull

# Custom interval (every 30 seconds)
intellihybrid sync --mode bidirectional --interval 30
```

### 8.3 Run as a background service (Linux)

Create `/etc/systemd/system/intellihybrid.service`:

```ini
[Unit]
Description=IntelliHybrid Hybrid Cloud Sync
After=network.target

[Service]
Type=simple
User=intellihybrid
WorkingDirectory=/opt/intellihybrid
EnvironmentFile=/opt/intellihybrid/.env
ExecStart=/usr/local/bin/intellihybrid sync --mode bidirectional
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable intellihybrid
sudo systemctl start intellihybrid
sudo systemctl status intellihybrid
```

---

## 9. Command Reference

| Command | Description |
|---|---|
| `intellihybrid init` | Initialize VPN, DynamoDB tables, and test DB |
| `intellihybrid init --skip-vpn` | Skip VPN setup (useful if already configured) |
| `intellihybrid sync` | Start continuous bidirectional sync |
| `intellihybrid sync --once` | Run one sync cycle and exit |
| `intellihybrid sync --mode push` | Only push on-prem → DynamoDB |
| `intellihybrid sync --mode pull` | Only pull DynamoDB → on-prem |
| `intellihybrid sync --interval 30` | Sync every 30 seconds |
| `intellihybrid tables` | List all DynamoDB tables |
| `intellihybrid tables --describe --table-name users-table` | Show table details |
| `intellihybrid health` | Check all component health |

---

## 10. Security Best Practices

### ✅ Do this

- Store all secrets in environment variables or AWS Secrets Manager
- Enable `ssl: true` for all database connections
- Enable `encryption: true` for all DynamoDB tables
- Enable `point_in_time_recovery: true` for all tables
- Use least-privilege IAM roles
- Rotate AWS access keys every 90 days
- Add `config/config.yaml` to `.gitignore`
- Run IntelliHybrid as a dedicated non-root service account

### ❌ Never do this

- Put passwords or keys in `config.yaml`
- Commit `config.yaml` to source control
- Use root AWS credentials
- Disable SSL/TLS
- Use overly broad IAM policies like `"Action": "*"`

---

## 11. Troubleshooting

### VPN not connecting

```
Error: Customer Gateway could not be created
```

**Fix:** Ensure the `customer_gateway_ip` is a valid public static IP. Private IPs (10.x, 192.168.x) are not valid for Customer Gateways.

---

### Database connection refused

```
Error: Can't connect to MySQL server on '192.168.1.100'
```

**Fix:**
1. Verify the IP and port are correct
2. Check that the DB server allows connections from the IntelliHybrid host
3. Check firewall rules on both ends
4. Try: `mysql -h 192.168.1.100 -u $DB_USER -p` from the same machine

---

### DynamoDB table creation fails

```
Error: AccessDeniedException
```

**Fix:** The IAM user is missing DynamoDB permissions. Re-attach the policy from Step 1.

---

### Config file not found

```
Error: Config file not found: config/config.yaml
```

**Fix:**
```bash
cp config/config.template.yaml config/config.yaml
# Then fill in your values
```

---

### Environment variable not set

```
EnvironmentError: Required environment variable 'AWS_ACCESS_KEY_ID' is not set
```

**Fix:** Export the variable before running:
```bash
export AWS_ACCESS_KEY_ID="your_key_here"
```

---

## 12. FAQs

**Q: Does this work with AWS GovCloud?**  
A: Yes — just set `aws.region` to `us-gov-west-1` or `us-gov-east-1`.

**Q: Can I use an IAM role instead of access keys?**  
A: Yes — set `aws.role_arn` in config.yaml and IntelliHybrid will assume that role.

**Q: How many tables can I define?**  
A: As many as you need. There's no limit in IntelliHybrid.

**Q: Is the sync real-time?**  
A: Near real-time — the minimum interval is 1 second (`--interval 1`). True CDC (Change Data Capture) streaming support is on the roadmap.

**Q: Can I run multiple IntelliHybrid instances?**  
A: Yes — each instance should have its own config.yaml with different table mappings.

**Q: What happens if the VPN drops?**  
A: The sync engine will log an error and retry on the next interval. AWS Site-to-Site VPN has built-in redundancy with two tunnels.

**Q: Where can I get help?**  
A: Open an issue at https://github.com/Clever-Boy/IntelliHybrid/issues

---

*IntelliHybrid is open source under the MIT License. Contributions welcome!*
