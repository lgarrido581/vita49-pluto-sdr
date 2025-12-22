# Contributing to VITA49 Pluto

Thank you for your interest in contributing to the VITA49 Pluto project!

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/vita49-pluto.git
   cd vita49-pluto
   ```

2. **Install the package in development mode**
   ```bash
   # Install in editable mode with all dependencies
   pip install -e .

   # Or install with development/testing dependencies
   pip install -e ".[dev]"
   ```

   This installs the `vita49` package in editable mode, so:
   - Changes to the code are immediately available
   - The package can be imported from anywhere: `from vita49 import ...`
   - All example scripts and tests can find the library

3. **Run tests**
   ```bash
   pytest tests/ -v
   ```

## Code Style

- **Python**: Follow PEP 8
  - Use meaningful variable names
  - Add docstrings to functions and classes
  - Keep functions focused and small

- **C**: Follow Linux kernel style
  - 4-space indentation (or tabs as configured)
  - K&R brace style
  - Clear function names

- **EditorConfig**: The `.editorconfig` file enforces consistent formatting

## Testing

### Running Tests

```bash
# All tests
pytest tests/ -v

# Unit tests only
pytest tests/test_*.py -v

# With coverage
pytest tests/ --cov=src --cov-report=html
```

### End-to-End Tests

E2E tests require ADALM-Pluto hardware:

```bash
python tests/e2e/full_pipeline.py --pluto-uri ip:192.168.2.1
```

### Writing Tests

- Add unit tests for new features
- Test edge cases and error conditions
- Use descriptive test names

## Pull Request Process

1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/my-new-feature
   ```

3. **Make your changes**
   - Write code
   - Add tests
   - Update documentation

4. **Test your changes**
   ```bash
   pytest tests/ -v
   ```

5. **Commit with clear messages**
   ```bash
   git commit -m "Add feature: description of what you added"
   ```

6. **Push to your fork**
   ```bash
   git push origin feature/my-new-feature
   ```

7. **Submit a pull request**
   - Describe what your PR does
   - Reference any related issues
   - Explain testing performed

## Code Review Checklist

Before submitting, ensure:

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Code follows style guidelines
- [ ] Documentation updated (if needed)
- [ ] No breaking changes (or clearly documented)
- [ ] Commit messages are clear and descriptive

## Documentation

Update documentation when:
- Adding new features → Update relevant docs in `docs/`
- Changing APIs → Update `docs/DEVELOPMENT.md`
- Modifying build process → Update `docs/BUILD.md`
- Changing usage → Update `docs/USAGE.md`

## Reporting Issues

When reporting bugs, include:
- Your environment (OS, Python version, Pluto firmware)
- Steps to reproduce
- Expected vs actual behavior
- Error messages and logs

## Questions?

- Check the documentation in `docs/`
- Read `docs/DEVELOPMENT.md` for architecture details
- Open an issue for questions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
