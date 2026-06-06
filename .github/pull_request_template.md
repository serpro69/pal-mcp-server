## Description

Please provide a clear and concise description of what this PR does.

## Changes Made

- [ ] List the specific changes made
- [ ] Include any breaking changes
- [ ] Note any dependencies added/removed

## Testing

**Please review our [Testing Guide](../docs/testing.md) before submitting.**

### Run all linting and tests (required):
```bash
# Activate virtual environment first
source venv/bin/activate

# Run comprehensive code quality checks (recommended)
./code_quality_checks.sh

# If you made tool changes, also run simulator tests
python communication_simulator_test.py
```

- [ ] All linting passes (ruff, black, isort)
- [ ] All unit tests pass
- [ ] **For new features**: Unit tests added in `tests/`
- [ ] **For tool changes**: Simulator tests added in `simulator_tests/`
- [ ] **For bug fixes**: Tests added to prevent regression
- [ ] Simulator tests pass (if applicable)
- [ ] Manual testing completed with realistic scenarios

## Related Issues

Fixes #(issue number)

## Checklist

- [ ] Self-review completed
- [ ] **Activated venv and ran code quality checks: `source venv/bin/activate && ./code_quality_checks.sh`**
- [ ] **Tests added for ALL changes** (see Testing section above)
- [ ] Documentation updated as needed
- [ ] All unit tests passing
- [ ] Relevant simulator tests passing (if tool changes)
- [ ] Ready for review

## Additional Notes

Any additional information that reviewers should know.
