# 🤝 Contributing to IntelliHybrid

Thank you for your interest in contributing! IntelliHybrid welcomes contributions of all kinds.

## Ways to Contribute

- 🐛 **Bug reports** — Open an issue with steps to reproduce
- 💡 **Feature requests** — Open an issue describing the use case
- 🔧 **Code contributions** — Fork, branch, PR
- 📖 **Documentation** — Improve guides, add examples
- ⭐ **Stars** — The simplest contribution that helps visibility

## Development Setup

```bash
git clone https://github.com/Clever-Boy/IntelliHybrid.git
cd IntelliHybrid
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,all]"
```

## Running Tests

```bash
pytest tests/ -v --cov=src --cov-report=term
```

## Pull Request Process

1. Fork the repo and create a branch: `git checkout -b feature/my-feature`
2. Make your changes with tests
3. Run `pytest` — all tests must pass
4. Run `bandit -r src/ -ll` — no new security issues
5. Open a PR with a clear description of what and why

## Roadmap — Good First Issues

| Feature | Difficulty | Description |
|---|---|---|
| MongoDB connector | Medium | Add `MongoDBConnector` in `src/onprem/database.py` |
| Terraform module | Medium | IaC version of VPN + DynamoDB setup |
| CDC streaming | Hard | Real-time change data capture using DynamoDB Streams |
| Web UI | Hard | Browser dashboard for monitoring sync status |
| SNS/SQS integration | Medium | Event-driven sync via AWS messaging |

## Code Style

- Follow PEP 8
- Max line length: 100 characters  
- Type hints on all public functions
- Docstrings on all classes and public methods

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
