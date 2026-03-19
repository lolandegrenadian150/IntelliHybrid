# 📦 Zenodo Setup & GitHub Release Guide
# How to get your DOI and start tracking downloads for your EB-1A

---

## PART 1 — Connect GitHub Repo to Zenodo

### Step 1: Create Zenodo account
1. Go to https://zenodo.org
2. Click "Log in with GitHub"
3. Authorize Zenodo to access your GitHub account

### Step 2: Enable the repo on Zenodo
1. Go to https://zenodo.org/account/settings/github/
2. You will see a list of all your GitHub repos
3. Find **IntelliHybrid** and flip the toggle to **ON**

That's it. Zenodo now watches this repo for new GitHub Releases.

---

## PART 2 — Create Your First GitHub Release (triggers DOI)

### Step 1: Push all files to GitHub
```bash
cd IntelliHybrid
git init
git add .
git commit -m "Initial release: IntelliHybrid v1.0.0"
git remote add origin https://github.com/Clever-Boy/IntelliHybrid.git
git branch -M main
git push -u origin main
```

### Step 2: Create a GitHub Release
1. Go to https://github.com/Clever-Boy/IntelliHybrid
2. Click **"Releases"** → **"Create a new release"**
3. Click **"Choose a tag"** → type `v1.0.0` → click **"Create new tag"**
4. Title: `IntelliHybrid v1.0.0 — Initial Release`
5. Description (copy-paste this):

```
## IntelliHybrid v1.0.0

First stable release of IntelliHybrid — a configuration-driven framework 
for secure, bidirectional integration between on-premise databases and AWS DynamoDB.

### Features
- AWS Site-to-Site VPN, OpenVPN, and Direct Connect support
- Auto-provisions DynamoDB tables with configurable PK/SK schemas
- Supports MySQL, PostgreSQL, Oracle, and SQL Server
- Continuous bidirectional sync (on-prem ↔ DynamoDB)
- KMS encryption, TLS 1.3, least-privilege IAM — security first
- Complete how-to-use documentation included

### Installation
pip install intellihybrid

### Quick Start
See README.md and docs/HOW_TO_USE.md
```

6. Check **"Set as the latest release"**
7. Click **"Publish release"**

---

## PART 3 — Get Your DOI from Zenodo

Within a few minutes of publishing the release:

1. Go to https://zenodo.org/account/settings/github/
2. Click on **IntelliHybrid**
3. You will see a DOI like: `10.5281/zenodo.1234567`
4. Copy the DOI badge markdown from Zenodo

### Update README.md with your real DOI

Replace this line in README.md:
```
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.XXXXXXX.svg)](https://doi.org/10.5281/zenodo.XXXXXXX)
```
With your real DOI:
```
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.1234567.svg)](https://doi.org/10.5281/zenodo.1234567)
```

Also update:
- `CITATION.cff` — add the DOI field
- `.zenodo.json` — this file is already present and Zenodo reads it automatically
- `pyproject.toml` — update the Zenodo URL in `[project.urls]`

---

## PART 4 — Maximize Download & View Metrics (for EB-1A)

### What Zenodo tracks:
- ✅ Total downloads of each release ZIP
- ✅ Total views of the Zenodo record page
- ✅ Citations from other works
- ✅ GitHub stars and forks (via GitHub API)

### Strategies to maximize metrics:

#### A. Publish to PyPI (makes it pip-installable → more downloads)
```bash
pip install build twine
python -m build
twine upload dist/*
```
Each `pip install intellihybrid` shows up as a PyPI download.

#### B. Submit to Awesome Lists
Search GitHub for:
- `awesome-aws` → submit a PR adding IntelliHybrid
- `awesome-python` → submit a PR
- `awesome-devops` → submit a PR
- `awesome-hybrid-cloud` → submit a PR

Each inclusion drives organic downloads.

#### C. Post on communities
Post the GitHub link on:
- Reddit: r/aws, r/devops, r/python, r/cloudcomputing
- Hacker News: "Show HN: IntelliHybrid — on-prem to AWS DynamoDB connector"
- Dev.to article with technical walkthrough
- Hashnode blog post
- LinkedIn (use the posts in MARKETING.md)

#### D. Add to your GitHub Profile README
Pin **IntelliHybrid** to your profile so every visitor to github.com/Clever-Boy sees it.

#### E. Create a GitHub Topic
Add these topics to the repo (Settings → Topics):
`hybrid-cloud`, `aws`, `dynamodb`, `on-premise`, `cloud-integration`, `vpn`, `python`, `devops`, `cloud-migration`, `data-sync`

This helps the repo appear in GitHub topic search.

---

## PART 5 — EB-1A Evidence Checklist

When you use this repo as EB-1A evidence, you can document:

| Evidence Type | What to Screenshot/Export |
|---|---|
| Original contribution | GitHub commit history showing you authored all code |
| Downloads | Zenodo record page showing download count |
| Views | Zenodo record page showing view count |
| Stars | GitHub repo page (currently visible) |
| Citations | Zenodo "cited by" section (grows over time) |
| PyPI downloads | https://pypistats.org/packages/intellihybrid |
| Community engagement | Reddit/LinkedIn post engagement screenshots |
| DOI | Zenodo DOI badge in README |
| Academic use | CITATION.cff and .zenodo.json showing academic-style metadata |

### How to export Zenodo stats:
1. Go to your Zenodo record: `https://zenodo.org/records/XXXXXXX`
2. Screenshot the stats panel showing downloads + views
3. The Zenodo record URL itself is citable evidence of the DOI

---

## PART 6 — Ongoing Releases (keep metrics growing)

Plan future releases to keep the repo active and growing:

| Version | Planned Features | Timeline |
|---|---|---|
| v1.0.0 | Initial release (current) | Now |
| v1.1.0 | MongoDB support | Month 2 |
| v1.2.0 | Terraform module | Month 3 |
| v1.3.0 | CDC streaming (real-time) | Month 4 |
| v2.0.0 | Web UI dashboard | Month 6 |

Each release creates a new Zenodo version with its own download counter that adds to the aggregate.
