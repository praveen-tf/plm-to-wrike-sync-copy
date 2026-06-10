# 🧪 Testing Strategy

## TDD Approach

1. Write test first → 2. Watch it fail → 3. Write minimal code → 4. Refactor → 5. Repeat

## Testing Best Practices

```python
import pytest

@pytest.fixture
def sample_user():
    return User(id=123, name="Test User", email="test@example.com")

def test_user_can_update_email_when_valid(sample_user):
    """Test that users can update their email with valid input."""
    sample_user.update_email("new@example.com")
    assert sample_user.email == "new@example.com"

def test_user_update_email_fails_with_invalid_format(sample_user):
    """Test that invalid email formats are rejected."""
    with pytest.raises(ValidationError):
        sample_user.update_email("not-an-email")
```

**Test Organization**: Unit tests (isolation), integration tests (component interaction), E2E tests (workflows). Use `conftest.py` for shared fixtures. Aim for 80%+ coverage on critical paths.
